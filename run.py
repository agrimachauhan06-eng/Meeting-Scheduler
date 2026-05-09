"""
Meeting Scheduler & Reminder System
Run this to start the web dashboard with background email sync and reminders.
"""

import logging
from app import create_app, db
from app.reminder_engine import ReminderEngine
from app.models import EmailAccount

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = create_app()
reminder_engine = ReminderEngine()


def seed_primary_email():
    """Seed the default company email if no accounts exist."""
    with app.app_context():
        if EmailAccount.query.count() == 0:
            primary = EmailAccount(
                email="outreachp689@gmail.com",
                display_name="Company Primary",
                imap_host="imap.gmail.com",
                imap_port=993,
                smtp_host="smtp.gmail.com",
                smtp_port=587,
                is_primary=True,
                is_active=True,
                sync_enabled=True,
                notify_enabled=True,
            )
            db.session.add(primary)
            db.session.commit()
            logging.info("Seeded primary email account: outreachp689@gmail.com")


if __name__ == "__main__":
    seed_primary_email()
    reminder_engine.start(app)
    try:
        app.run(debug=True, port=5000, use_reloader=False)
    finally:
        reminder_engine.stop()
