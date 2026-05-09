import os
import email
import logging
from datetime import datetime, timedelta
from email.utils import parseaddr

from imapclient import IMAPClient
from icalendar import Calendar
from bs4 import BeautifulSoup

from app import db
from app.models import EmailSyncState, EmailAccount
from app.meeting_manager import MeetingManager

logger = logging.getLogger(__name__)


class EmailIntegration:
    def __init__(self, account=None):
        if account:
            self.host = account.imap_host
            self.port = account.imap_port
            self.username = account.email
            self.password = account.password
            self.use_ssl = account.use_ssl
            self.account = account
        else:
            self.host = os.getenv("EMAIL_HOST", "imap.gmail.com")
            self.port = int(os.getenv("EMAIL_PORT", "993"))
            self.username = os.getenv("EMAIL_USERNAME", "")
            self.password = os.getenv("EMAIL_PASSWORD", "")
            self.use_ssl = os.getenv("EMAIL_USE_SSL", "true").lower() == "true"
            self.account = None

    def _connect(self):
        server = IMAPClient(self.host, port=self.port, ssl=self.use_ssl)
        server.login(self.username, self.password)
        return server

    def sync_meetings_from_email(self):
        if not self.username or not self.password:
            logger.warning("Email credentials not configured. Skipping sync.")
            return []

        if self.account:
            self.account.sync_status = "syncing"
            db.session.commit()
            since_date = self.account.last_sync_time or (
                datetime.utcnow() - timedelta(days=30)
            )
        else:
            sync_state = EmailSyncState.query.first()
            if not sync_state:
                sync_state = EmailSyncState()
                db.session.add(sync_state)
                db.session.commit()
            sync_state.sync_status = "syncing"
            db.session.commit()
            since_date = sync_state.last_sync_time or (
                datetime.utcnow() - timedelta(days=30)
            )

        new_meetings = []
        try:
            server = self._connect()
            server.select_folder("INBOX", readonly=True)

            messages = server.search(["SINCE", since_date.date()])

            if not messages:
                self._update_sync_done()
                server.logout()
                return []

            fetched = server.fetch(messages, ["RFC822", "UID"])

            for msg_id, data in fetched.items():
                try:
                    uid = str(data.get(b"UID", msg_id))
                    raw_email = data[b"RFC822"]
                    parsed = email.message_from_bytes(raw_email)

                    meetings = self._extract_meetings_from_email(parsed, uid)
                    new_meetings.extend(meetings)
                except Exception as e:
                    logger.error(f"Error processing email {msg_id}: {e}")

            self._update_sync_done(last_uid=str(messages[-1]) if messages else None)
            server.logout()

        except Exception as e:
            logger.error(f"Email sync failed: {e}")
            self._update_sync_error(str(e))

        return new_meetings

    def _update_sync_done(self, last_uid=None):
        if self.account:
            self.account.sync_status = "idle"
            self.account.last_sync_time = datetime.utcnow()
            if last_uid:
                self.account.last_sync_uid = last_uid
        db.session.commit()

    def _update_sync_error(self, error_msg):
        if self.account:
            self.account.sync_status = "error"
            self.account.error_message = error_msg
        db.session.commit()

    def _extract_meetings_from_email(self, parsed_email, uid):
        meetings = []

        for part in parsed_email.walk():
            content_type = part.get_content_type()

            if content_type == "text/calendar":
                cal_data = part.get_payload(decode=True)
                if cal_data:
                    extracted = self._parse_ics(cal_data, uid)
                    meetings.extend(extracted)

            elif content_type == "application/ics":
                cal_data = part.get_payload(decode=True)
                if cal_data:
                    extracted = self._parse_ics(cal_data, uid)
                    meetings.extend(extracted)

        if not meetings:
            meeting = self._detect_meeting_from_body(parsed_email, uid)
            if meeting:
                meetings.append(meeting)

        return meetings

    def _parse_ics(self, cal_data, email_uid):
        meetings = []
        try:
            cal = Calendar.from_ical(cal_data)
            for component in cal.walk():
                if component.name == "VEVENT":
                    summary = str(component.get("summary", "Untitled Meeting"))
                    description = str(component.get("description", ""))
                    location = str(component.get("location", ""))
                    dtstart = component.get("dtstart")
                    dtend = component.get("dtend")
                    organizer = component.get("organizer")
                    uid = str(component.get("uid", ""))

                    if not dtstart:
                        continue

                    start_time = dtstart.dt
                    if dtend:
                        end_time = dtend.dt
                    else:
                        end_time = start_time + timedelta(hours=1)

                    if not isinstance(start_time, datetime):
                        start_time = datetime.combine(start_time, datetime.min.time())
                    if not isinstance(end_time, datetime):
                        end_time = datetime.combine(end_time, datetime.min.time())

                    if start_time.tzinfo:
                        start_time = start_time.replace(tzinfo=None)
                    if end_time.tzinfo:
                        end_time = end_time.replace(tzinfo=None)

                    organizer_email = ""
                    if organizer:
                        org_str = str(organizer)
                        if "mailto:" in org_str.lower():
                            organizer_email = org_str.split(":")[-1]

                    attendees_list = []
                    att_prop = component.get("attendee")
                    if att_prop:
                        if not isinstance(att_prop, list):
                            att_prop = [att_prop]
                        for att in att_prop:
                            att_str = str(att)
                            att_email = att_str.split(":")[-1] if "mailto:" in att_str.lower() else att_str
                            att_name = att.params.get("CN", "") if hasattr(att, "params") else ""
                            attendees_list.append({
                                "email": att_email,
                                "name": str(att_name),
                                "rsvp_status": "pending",
                            })

                    meeting_link = ""
                    if "zoom" in description.lower() or "teams" in description.lower() or "meet.google" in description.lower():
                        import re
                        urls = re.findall(r'https?://[^\s<>"]+', description)
                        for url in urls:
                            if any(domain in url.lower() for domain in ["zoom.us", "teams.microsoft", "meet.google"]):
                                meeting_link = url
                                break

                    rrule = component.get("rrule")
                    is_recurring = rrule is not None
                    recurrence_rule = str(rrule.to_ical().decode()) if rrule else ""

                    meeting = MeetingManager.create_meeting(
                        title=summary,
                        start_time=start_time,
                        end_time=end_time,
                        description=description,
                        organizer=organizer_email,
                        location=location,
                        meeting_link=meeting_link,
                        is_recurring=is_recurring,
                        recurrence_rule=recurrence_rule,
                        source="email",
                        email_uid=email_uid,
                        calendar_uid=uid,
                        attendees=attendees_list,
                    )
                    meetings.append(meeting)
        except Exception as e:
            logger.error(f"Error parsing ICS data: {e}")

        return meetings

    def _detect_meeting_from_body(self, parsed_email, uid):
        import re

        subject = str(parsed_email.get("subject", ""))
        meeting_keywords = [
            "meeting", "call", "standup", "stand-up", "sync",
            "review", "demo", "sprint", "retrospective", "planning",
            "1:1", "one-on-one", "huddle", "check-in",
        ]

        is_meeting = any(kw in subject.lower() for kw in meeting_keywords)
        if not is_meeting:
            return None

        body = ""
        for part in parsed_email.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True)
                if html:
                    body = BeautifulSoup(html, "html.parser").get_text()
                    break
            elif part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")

        date_patterns = [
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+(?:at\s+)?(\d{1,2}:\d{2}\s*[APap][Mm]?)',
            r'(\w+\s+\d{1,2},?\s+\d{4})\s+(?:at\s+)?(\d{1,2}:\d{2}\s*[APap][Mm]?)',
        ]

        from_addr = parseaddr(parsed_email.get("from", ""))

        meeting_link = ""
        urls = re.findall(r'https?://[^\s<>"]+', body)
        for url in urls:
            if any(d in url.lower() for d in ["zoom.us", "teams.microsoft", "meet.google", "webex"]):
                meeting_link = url
                break

        email_date = parsed_email.get("date", "")
        try:
            from email.utils import parsedate_to_datetime
            start_time = parsedate_to_datetime(email_date).replace(tzinfo=None)
        except Exception:
            start_time = datetime.utcnow() + timedelta(hours=1)

        end_time = start_time + timedelta(hours=1)

        meeting = MeetingManager.create_meeting(
            title=subject,
            start_time=start_time,
            end_time=end_time,
            description=body[:500] if body else "",
            organizer=from_addr[1] if from_addr else "",
            meeting_link=meeting_link,
            source="email",
            email_uid=uid,
        )
        return meeting
