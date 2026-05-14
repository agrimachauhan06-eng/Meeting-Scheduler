import os
import smtplib
import logging
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from icalendar import Calendar, Event, vText, vCalAddress

from app.models import EmailAccount

logger = logging.getLogger(__name__)


class InviteService:

    @staticmethod
    def _get_smtp_creds():
        primary = EmailAccount.query.filter_by(is_primary=True, is_active=True).first()
        if primary and primary.password:
            return primary.email, primary.password, primary.smtp_host, primary.smtp_port
        return (
            os.getenv("SMTP_USERNAME", ""),
            os.getenv("SMTP_PASSWORD", ""),
            os.getenv("SMTP_HOST", "smtp.gmail.com"),
            int(os.getenv("SMTP_PORT", "587")),
        )

    @staticmethod
    def generate_ics(meeting):
        cal = Calendar()
        cal.add('prodid', '-//OutreachPro Meeting Scheduler//')
        cal.add('version', '2.0')
        cal.add('method', 'REQUEST')

        event = Event()
        event.add('summary', meeting.title)
        event.add('dtstart', meeting.start_time)
        event.add('dtend', meeting.end_time)
        event.add('description', meeting.description or '')
        event.add('location', meeting.location or '')
        event.add('uid', meeting.calendar_uid or str(uuid.uuid4()))
        event.add('dtstamp', datetime.utcnow())

        for attendee in meeting.attendees:
            att = vCalAddress(f'mailto:{attendee.email}')
            att.params['cn'] = vText(attendee.name or attendee.email)
            att.params['ROLE'] = vText('REQ-PARTICIPANT')
            att.params['RSVP'] = vText('TRUE')
            event.add('attendee', att, encode=0)

        cal.add_component(event)
        return cal.to_ical()

    @staticmethod
    def send_invites(meeting):
        """Send ICS calendar invites to all meeting attendees."""
        if not meeting.attendees:
            return 0

        smtp_user, smtp_pass, smtp_host, smtp_port = InviteService._get_smtp_creds()
        if not smtp_user or not smtp_pass:
            logger.warning("SMTP not configured. Skipping invite sending.")
            return 0

        ics_data = InviteService.generate_ics(meeting)
        start_str = meeting.start_time.strftime('%B %d, %Y at %I:%M %p')
        end_str = meeting.end_time.strftime('%I:%M %p')
        sent = 0

        for attendee in meeting.attendees:
            try:
                msg = MIMEMultipart('mixed')
                msg['Subject'] = f"Meeting Invite: {meeting.title}"
                msg['From'] = smtp_user
                msg['To'] = attendee.email

                html = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                    <div style="background: #4A90D9; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                        <h2 style="margin: 0;">Meeting Invitation</h2>
                    </div>
                    <div style="padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 8px 8px;">
                        <h3 style="color: #333;">{meeting.title}</h3>
                        <p><strong>When:</strong> {start_str} &ndash; {end_str}</p>
                        {"<p><strong>Where:</strong> " + meeting.location + "</p>" if meeting.location else ""}
                        {"<p><strong>Meeting Link:</strong> <a href='" + meeting.meeting_link + "'>Join Meeting</a></p>" if meeting.meeting_link else ""}
                        {"<p><strong>Details:</strong> " + meeting.description + "</p>" if meeting.description else ""}
                        <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
                        <p style="color:#888;font-size:0.85em;">
                            Open the attached <strong>invite.ics</strong> file to add this meeting to your calendar (works with Google Calendar, Outlook, Apple Calendar).
                        </p>
                    </div>
                </div>
                """
                msg.attach(MIMEText(html, 'html'))

                ics_part = MIMEBase('text', 'calendar', method='REQUEST', name='invite.ics')
                ics_part.set_payload(ics_data)
                encoders.encode_base64(ics_part)
                ics_part.add_header('Content-Disposition', 'attachment', filename='invite.ics')
                msg.attach(ics_part)

                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, attendee.email, msg.as_string())

                logger.info(f"Sent invite for '{meeting.title}' to {attendee.email}")
                sent += 1
            except Exception as e:
                logger.error(f"Failed to send invite to {attendee.email}: {e}")

        return sent

    @staticmethod
    def send_reminder(meeting, minutes_until):
        """Send reminder emails to all meeting attendees."""
        if not meeting.attendees:
            return 0

        smtp_user, smtp_pass, smtp_host, smtp_port = InviteService._get_smtp_creds()
        if not smtp_user or not smtp_pass:
            logger.warning("SMTP not configured. Skipping reminder sending.")
            return 0

        subject = f"Reminder: '{meeting.title}' starts in {minutes_until} minutes"
        start_str = meeting.start_time.strftime('%B %d, %Y at %I:%M %p')
        sent = 0

        for attendee in meeting.attendees:
            try:
                msg = MIMEMultipart('alternative')
                msg['Subject'] = subject
                msg['From'] = smtp_user
                msg['To'] = attendee.email

                html = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                    <div style="background: #fd7e14; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                        <h2 style="margin: 0;">Meeting Reminder</h2>
                    </div>
                    <div style="padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 8px 8px;">
                        <h3 style="color: #333;">{meeting.title}</h3>
                        <p><strong>Starts in:</strong> {minutes_until} minutes</p>
                        <p><strong>When:</strong> {start_str}</p>
                        {"<p><strong>Where:</strong> " + meeting.location + "</p>" if meeting.location else ""}
                        {"<p><strong>Meeting Link:</strong> <a href='" + meeting.meeting_link + "'>Join Meeting</a></p>" if meeting.meeting_link else ""}
                    </div>
                </div>
                """
                msg.attach(MIMEText(html, 'html'))

                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(smtp_user, attendee.email, msg.as_string())

                sent += 1
            except Exception as e:
                logger.error(f"Failed to send reminder to {attendee.email}: {e}")

        return sent
