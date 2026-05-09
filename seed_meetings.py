"""Seed existing and upcoming meetings into the database."""

from datetime import datetime
from app import create_app, db
from app.meeting_manager import MeetingManager

app = create_app()

meetings = [
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

with app.app_context():
    for m in meetings:
        meeting = MeetingManager.create_meeting(
            title=m["title"],
            start_time=m["start_time"],
            end_time=m["end_time"],
            location=m["location"],
            description=m["description"],
            priority=m["priority"],
            tags=m["tags"],
            reminder_minutes=[15, 5],
        )
        print(f"  Added: {meeting.title} - {meeting.start_time.strftime('%b %d, %I:%M %p')}")

    print(f"\nDone! {len(meetings)} meetings added.")
