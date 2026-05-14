import os
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from apscheduler.schedulers.background import BackgroundScheduler

from app import db
from app.models import Meeting, Reminder, EmailAccount

logger = logging.getLogger(__name__)


class ReminderEngine:
    def __init__(self, app=None):
        self.app = app
        self.scheduler = BackgroundScheduler()
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = os.getenv("SMTP_USERNAME", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.enable_email = os.getenv("ENABLE_EMAIL_REMINDERS", "true").lower() == "true"
        self.enable_desktop = os.getenv("ENABLE_DESKTOP_NOTIFICATIONS", "true").lower() == "true"

    def start(self, app):
        self.app = app
        check_interval = int(os.getenv("REMINDER_CHECK_INTERVAL", "60"))
        self.scheduler.add_job(
            self._check_reminders,
            "interval",
            seconds=check_interval,
            id="reminder_check",
            replace_existing=True,
        )

        poll_interval = int(os.getenv("EMAIL_POLL_INTERVAL", "300"))
        self.scheduler.add_job(
            self._sync_emails,
            "interval",
            seconds=poll_interval,
            id="email_sync",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._update_meeting_statuses,
            "interval",
            seconds=60,
            id="status_update",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info("Reminder engine started.")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Reminder engine stopped.")

    def _check_reminders(self):
        with self.app.app_context():
            now = datetime.utcnow()
            pending_reminders = Reminder.query.filter(
                Reminder.is_sent == False,
                Reminder.remind_at <= now,
            ).all()

            for reminder in pending_reminders:
                meeting = reminder.meeting
                if not meeting or meeting.status == "cancelled":
                    reminder.is_sent = True
                    continue

                try:
                    if reminder.reminder_type in ("email", "both") and self.enable_email:
                        self._send_email_reminder(meeting, reminder)
                        # Also remind all attendees
                        self._send_attendee_reminders(meeting, reminder)

                    if reminder.reminder_type in ("desktop", "both") and self.enable_desktop:
                        self._send_desktop_notification(meeting, reminder)

                    reminder.is_sent = True
                    reminder.sent_at = now
                    logger.info(f"Sent reminder for meeting: {meeting.title}")
                except Exception as e:
                    logger.error(f"Failed to send reminder for {meeting.title}: {e}")

            db.session.commit()

    def _send_email_reminder(self, meeting, reminder):
        accounts = EmailAccount.query.filter_by(
            is_active=True, notify_enabled=True
        ).all()

        if not accounts and (not self.smtp_username or not self.smtp_password):
            logger.warning("No email accounts configured for notifications.")
            return

        time_until = meeting.start_time - datetime.utcnow()
        minutes_until = max(0, int(time_until.total_seconds() / 60))

        subject = f"Reminder: {meeting.title} in {minutes_until} minutes"

        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: #4A90D9; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
                <h2 style="margin: 0;">Meeting Reminder</h2>
            </div>
            <div style="padding: 20px; border: 1px solid #ddd; border-top: none; border-radius: 0 0 8px 8px;">
                <h3 style="color: #333;">{meeting.title}</h3>
                <p><strong>Time:</strong> {meeting.start_time.strftime('%B %d, %Y at %I:%M %p')} - {meeting.end_time.strftime('%I:%M %p')}</p>
                <p><strong>Starts in:</strong> {minutes_until} minutes</p>
                {"<p><strong>Location:</strong> " + meeting.location + "</p>" if meeting.location else ""}
                {"<p><strong>Meeting Link:</strong> <a href='" + meeting.meeting_link + "'>Join Meeting</a></p>" if meeting.meeting_link else ""}
                {"<p><strong>Organizer:</strong> " + meeting.organizer + "</p>" if meeting.organizer else ""}
                {"<p><strong>Description:</strong> " + meeting.description[:200] + "</p>" if meeting.description else ""}
            </div>
        </div>
        """

        recipients = [acc.email for acc in accounts]
        if not recipients and self.smtp_username:
            recipients = [self.smtp_username]

        primary = next((acc for acc in accounts if acc.is_primary and acc.password), None)
        smtp_user = primary.email if primary else self.smtp_username
        smtp_pass = primary.password if primary else self.smtp_password
        smtp_host = primary.smtp_host if primary else self.smtp_host
        smtp_port = primary.smtp_port if primary else self.smtp_port

        if not smtp_user or not smtp_pass:
            logger.warning("No SMTP credentials available.")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg.as_string())

    def _send_attendee_reminders(self, meeting, reminder):
        if not meeting.attendees:
            return
        try:
            from app.invite_service import InviteService
            time_until = meeting.start_time - datetime.utcnow()
            minutes_until = max(0, int(time_until.total_seconds() / 60))
            InviteService.send_reminder(meeting, minutes_until)
        except Exception as e:
            logger.error(f"Failed to send attendee reminders for {meeting.title}: {e}")

    def _send_desktop_notification(self, meeting, reminder):
        try:
            from plyer import notification

            time_until = meeting.start_time - datetime.utcnow()
            minutes_until = max(0, int(time_until.total_seconds() / 60))

            notification.notify(
                title=f"Meeting in {minutes_until} min: {meeting.title}",
                message=f"{meeting.start_time.strftime('%I:%M %p')} - {meeting.location or 'No location'}",
                app_name="Meeting Scheduler",
                timeout=30,
            )
        except Exception as e:
            logger.warning(f"Desktop notification failed: {e}")

    def _sync_emails(self):
        with self.app.app_context():
            try:
                from app.email_integration import EmailIntegration
                accounts = EmailAccount.query.filter_by(
                    is_active=True, sync_enabled=True
                ).all()
                total = 0
                for acc in accounts:
                    ei = EmailIntegration(account=acc)
                    new = ei.sync_meetings_from_email()
                    total += len(new)
                if total:
                    logger.info(f"Synced {total} new meetings from {len(accounts)} email account(s).")
            except Exception as e:
                logger.error(f"Email sync job failed: {e}")

    def _update_meeting_statuses(self):
        with self.app.app_context():
            now = datetime.utcnow()
            in_progress = Meeting.query.filter(
                Meeting.status == "scheduled",
                Meeting.start_time <= now,
                Meeting.end_time > now,
            ).all()
            for m in in_progress:
                m.status = "in_progress"

            completed = Meeting.query.filter(
                Meeting.status.in_(["scheduled", "in_progress"]),
                Meeting.end_time <= now,
            ).all()
            for m in completed:
                m.status = "completed"

            db.session.commit()
