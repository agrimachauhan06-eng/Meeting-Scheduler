from datetime import datetime, timedelta
from app import db
from app.models import Meeting, Attendee, Reminder, MeetingTag


class MeetingManager:
    @staticmethod
    def create_meeting(
        title,
        start_time,
        end_time,
        description="",
        organizer="",
        location="",
        meeting_link="",
        is_recurring=False,
        recurrence_rule="",
        priority="normal",
        source="manual",
        email_uid="",
        calendar_uid="",
        attendees=None,
        reminder_minutes=None,
        tags=None,
    ):
        if calendar_uid:
            existing = Meeting.query.filter_by(calendar_uid=calendar_uid).first()
            if existing:
                return MeetingManager.update_meeting(existing.id, **{
                    "title": title,
                    "start_time": start_time,
                    "end_time": end_time,
                    "description": description,
                    "organizer": organizer,
                    "location": location,
                    "meeting_link": meeting_link,
                })

        meeting = Meeting(
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
            organizer=organizer,
            location=location,
            meeting_link=meeting_link,
            is_recurring=is_recurring,
            recurrence_rule=recurrence_rule,
            priority=priority,
            source=source,
            email_uid=email_uid,
            calendar_uid=calendar_uid,
        )
        db.session.add(meeting)
        db.session.flush()

        if attendees:
            for att in attendees:
                attendee = Attendee(
                    meeting_id=meeting.id,
                    name=att.get("name", ""),
                    email=att["email"],
                    rsvp_status=att.get("rsvp_status", "pending"),
                )
                db.session.add(attendee)

        if reminder_minutes is None:
            reminder_minutes = [15, 5]
        for mins in reminder_minutes:
            remind_at = start_time - timedelta(minutes=mins)
            if remind_at > datetime.utcnow():
                reminder = Reminder(
                    meeting_id=meeting.id,
                    remind_at=remind_at,
                    reminder_type="both",
                )
                db.session.add(reminder)

        if tags:
            for tag in tags:
                mt = MeetingTag(meeting_id=meeting.id, tag_name=tag)
                db.session.add(mt)

        db.session.commit()
        return meeting

    @staticmethod
    def update_meeting(meeting_id, **kwargs):
        meeting = Meeting.query.get(meeting_id)
        if not meeting:
            return None

        for key, value in kwargs.items():
            if hasattr(meeting, key):
                setattr(meeting, key, value)

        db.session.commit()
        return meeting

    @staticmethod
    def delete_meeting(meeting_id):
        meeting = Meeting.query.get(meeting_id)
        if meeting:
            db.session.delete(meeting)
            db.session.commit()
            return True
        return False

    @staticmethod
    def get_meeting(meeting_id):
        return Meeting.query.get(meeting_id)

    @staticmethod
    def get_upcoming_meetings(hours=24):
        now = datetime.utcnow()
        end = now + timedelta(hours=hours)
        return (
            Meeting.query.filter(
                Meeting.start_time >= now,
                Meeting.start_time <= end,
                Meeting.status != "cancelled",
            )
            .order_by(Meeting.start_time)
            .all()
        )

    @staticmethod
    def get_meetings_by_date(date):
        start = datetime.combine(date, datetime.min.time())
        end = datetime.combine(date, datetime.max.time())
        return (
            Meeting.query.filter(
                Meeting.start_time >= start,
                Meeting.start_time <= end,
            )
            .order_by(Meeting.start_time)
            .all()
        )

    @staticmethod
    def get_meetings_in_range(start_date, end_date):
        return (
            Meeting.query.filter(
                Meeting.start_time >= start_date,
                Meeting.start_time <= end_date,
            )
            .order_by(Meeting.start_time)
            .all()
        )

    @staticmethod
    def get_todays_meetings():
        today = datetime.utcnow().date()
        return MeetingManager.get_meetings_by_date(today)

    @staticmethod
    def search_meetings(query):
        pattern = f"%{query}%"
        return (
            Meeting.query.filter(
                db.or_(
                    Meeting.title.ilike(pattern),
                    Meeting.description.ilike(pattern),
                    Meeting.organizer.ilike(pattern),
                    Meeting.location.ilike(pattern),
                )
            )
            .order_by(Meeting.start_time.desc())
            .all()
        )

    @staticmethod
    def cancel_meeting(meeting_id):
        return MeetingManager.update_meeting(meeting_id, status="cancelled")

    @staticmethod
    def get_conflicts(start_time, end_time, exclude_id=None):
        query = Meeting.query.filter(
            Meeting.status != "cancelled",
            Meeting.start_time < end_time,
            Meeting.end_time > start_time,
        )
        if exclude_id:
            query = query.filter(Meeting.id != exclude_id)
        return query.all()

    @staticmethod
    def get_meeting_stats():
        now = datetime.utcnow()
        today_start = datetime.combine(now.date(), datetime.min.time())
        today_end = datetime.combine(now.date(), datetime.max.time())
        week_end = now + timedelta(days=7)

        return {
            "today": Meeting.query.filter(
                Meeting.start_time >= today_start,
                Meeting.start_time <= today_end,
                Meeting.status != "cancelled",
            ).count(),
            "this_week": Meeting.query.filter(
                Meeting.start_time >= now,
                Meeting.start_time <= week_end,
                Meeting.status != "cancelled",
            ).count(),
            "total": Meeting.query.filter(Meeting.status != "cancelled").count(),
            "cancelled": Meeting.query.filter(Meeting.status == "cancelled").count(),
        }
