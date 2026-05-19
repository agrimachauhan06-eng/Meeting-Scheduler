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

    @staticmethod
    def send_task_notification(task):
        """Send an email notification for a newly created task/reminder."""
        if not task.assigned_email:
            return False
        smtp_user, smtp_pass, smtp_host, smtp_port = InviteService._get_smtp_creds()
        if not smtp_user or not smtp_pass:
            return False

        priority_colors = {
            "critical": "#F43F5E", "high": "#F59E0B",
            "normal": "#6366F1", "low": "#9CA3AF",
        }
        color = priority_colors.get(task.priority, "#6366F1")
        due_str = task.due_date.strftime("%B %d, %Y") if task.due_date else "No due date"

        html = f"""
        <div style="font-family:'Inter',Arial,sans-serif; max-width:520px; margin:0 auto; background:#fff; border-radius:16px; overflow:hidden; border:1px solid #E5E7EB;">
          <div style="background:linear-gradient(135deg,#6366F1,#7C3AED); padding:28px 32px;">
            <div style="font-size:13px; font-weight:700; color:rgba(255,255,255,0.7); letter-spacing:1px; text-transform:uppercase; margin-bottom:6px;">Nexus · Action Required</div>
            <div style="font-size:22px; font-weight:800; color:#fff; line-height:1.3;">{task.title}</div>
          </div>
          <div style="padding:28px 32px;">
            <table style="width:100%; border-collapse:collapse; font-size:13.5px; color:#374151;">
              <tr><td style="padding:8px 0; color:#6B7280; width:130px;">Assigned to</td>
                  <td style="padding:8px 0; font-weight:600;">{task.assigned_to or '—'}</td></tr>
              <tr><td style="padding:8px 0; color:#6B7280;">Priority</td>
                  <td style="padding:8px 0;">
                    <span style="background:{color}22; color:{color}; font-weight:700; border-radius:20px; padding:2px 10px; font-size:12px; border:1px solid {color}44;">
                      {task.priority.upper()}
                    </span>
                  </td></tr>
              <tr><td style="padding:8px 0; color:#6B7280;">Due date</td>
                  <td style="padding:8px 0; font-weight:600;">{due_str}</td></tr>
              {'<tr><td style="padding:8px 0; color:#6B7280; vertical-align:top;">Notes</td><td style="padding:8px 0;">' + task.notes + '</td></tr>' if task.notes else ''}
            </table>
            <div style="margin-top:24px; padding:14px 18px; background:#F9FAFB; border-radius:10px; font-size:13px; color:#6B7280;">
              This reminder was created in <strong>Nexus</strong>. Mark it complete once done.
            </div>
          </div>
        </div>
        """

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Nexus] Action item: {task.title}"
            msg["From"]    = smtp_user
            msg["To"]      = task.assigned_email
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, task.assigned_email, msg.as_string())
            return True
        except Exception as e:
            logger.error(f"Failed to send task notification to {task.assigned_email}: {e}")
            return False
