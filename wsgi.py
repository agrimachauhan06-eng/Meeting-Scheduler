"""Production entry point for WSGI servers (Gunicorn, etc.)"""

import os
import logging
from app import create_app, db
from app.reminder_engine import ReminderEngine
from app.models import EmailAccount, Meeting
from app.meeting_manager import MeetingManager
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = create_app()
reminder_engine = ReminderEngine()

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

    if Meeting.query.count() == 0:
        seed_meetings = [
            {
                "title": "Meeting with Fagun",
                "start_time": datetime(2026, 5, 9, 12, 0),
                "end_time": datetime(2026, 5, 9, 13, 0),
                "location": "Greater Noida",
                "description": "In-person meeting with Fagun",
                "priority": "high",
                "tags": ["in-person"],
            },
            {
                "title": "Meeting with Kulcha Kulture",
                "start_time": datetime(2026, 5, 11, 10, 0),
                "end_time": datetime(2026, 5, 11, 11, 0),
                "location": "TBD",
                "description": "In-person meeting with Kulcha Kulture. Time to be decided.",
                "priority": "normal",
                "tags": ["in-person", "time-tbd"],
            },
            {
                "title": "Meeting with Yashu",
                "start_time": datetime(2026, 5, 11, 11, 0),
                "end_time": datetime(2026, 5, 11, 12, 0),
                "location": "TBD",
                "description": "In-person meeting with Yashu. Time to be decided.",
                "priority": "normal",
                "tags": ["in-person", "time-tbd"],
            },
            {
                "title": "Meeting with Rahul",
                "start_time": datetime(2026, 5, 12, 10, 0),
                "end_time": datetime(2026, 5, 12, 11, 0),
                "location": "TBD",
                "description": "In-person meeting with Rahul. Time to be decided.",
                "priority": "normal",
                "tags": ["in-person", "time-tbd"],
            },
        ]
        for m in seed_meetings:
            MeetingManager.create_meeting(
                title=m["title"],
                start_time=m["start_time"],
                end_time=m["end_time"],
                location=m["location"],
                description=m["description"],
                priority=m["priority"],
                tags=m["tags"],
                reminder_minutes=[15, 5],
            )
        logging.info("Seeded 4 meetings.")

reminder_engine.start(app)
