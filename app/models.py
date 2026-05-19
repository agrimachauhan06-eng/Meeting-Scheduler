from datetime import datetime
from app import db


class Meeting(db.Model):
    __tablename__ = "meetings"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, default="")
    organizer = db.Column(db.String(255), default="")
    location = db.Column(db.String(255), default="")
    meeting_link = db.Column(db.String(500), default="")
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    is_recurring = db.Column(db.Boolean, default=False)
    recurrence_rule = db.Column(db.String(255), default="")
    priority = db.Column(db.String(20), default="normal")  # low, normal, high, critical
    status = db.Column(db.String(20), default="scheduled")  # scheduled, in_progress, completed, cancelled
    source = db.Column(db.String(50), default="manual")  # manual, email, ics_import
    email_uid = db.Column(db.String(255), default="")  # track which email this came from
    calendar_uid = db.Column(db.String(255), default="")  # ICS UID for dedup
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    attendees = db.relationship("Attendee", backref="meeting", cascade="all, delete-orphan")
    reminders = db.relationship("Reminder", backref="meeting", cascade="all, delete-orphan")
    tags = db.relationship("MeetingTag", backref="meeting", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "organizer": self.organizer,
            "location": self.location,
            "meeting_link": self.meeting_link,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "is_recurring": self.is_recurring,
            "recurrence_rule": self.recurrence_rule,
            "priority": self.priority,
            "status": self.status,
            "source": self.source,
            "attendees": [a.to_dict() for a in self.attendees],
            "reminders": [r.to_dict() for r in self.reminders],
            "tags": [t.tag_name for t in self.tags],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<Meeting {self.title} at {self.start_time}>"


class Attendee(db.Model):
    __tablename__ = "attendees"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meetings.id"), nullable=False)
    name = db.Column(db.String(255), default="")
    email = db.Column(db.String(255), nullable=False)
    rsvp_status = db.Column(db.String(20), default="pending")  # pending, accepted, declined, tentative

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "rsvp_status": self.rsvp_status,
        }


class Reminder(db.Model):
    __tablename__ = "reminders"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meetings.id"), nullable=False)
    remind_at = db.Column(db.DateTime, nullable=False)
    reminder_type = db.Column(db.String(20), default="both")  # email, desktop, both
    is_sent = db.Column(db.Boolean, default=False)
    sent_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "remind_at": self.remind_at.isoformat() if self.remind_at else None,
            "reminder_type": self.reminder_type,
            "is_sent": self.is_sent,
        }


class MeetingTag(db.Model):
    __tablename__ = "meeting_tags"

    id = db.Column(db.Integer, primary_key=True)
    meeting_id = db.Column(db.Integer, db.ForeignKey("meetings.id"), nullable=False)
    tag_name = db.Column(db.String(50), nullable=False)


class EmailAccount(db.Model):
    __tablename__ = "email_accounts"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    display_name = db.Column(db.String(255), default="")
    imap_host = db.Column(db.String(255), default="imap.gmail.com")
    imap_port = db.Column(db.Integer, default=993)
    smtp_host = db.Column(db.String(255), default="smtp.gmail.com")
    smtp_port = db.Column(db.Integer, default=587)
    password = db.Column(db.String(500), default="")  # app password
    use_ssl = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    is_primary = db.Column(db.Boolean, default=False)
    sync_enabled = db.Column(db.Boolean, default=True)
    notify_enabled = db.Column(db.Boolean, default=True)
    last_sync_uid = db.Column(db.String(255), default="")
    last_sync_time = db.Column(db.DateTime, nullable=True)
    sync_status = db.Column(db.String(20), default="idle")
    error_message = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "imap_host": self.imap_host,
            "is_active": self.is_active,
            "is_primary": self.is_primary,
            "sync_enabled": self.sync_enabled,
            "notify_enabled": self.notify_enabled,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "sync_status": self.sync_status,
        }


class CalendarFeed(db.Model):
    __tablename__ = "calendar_feeds"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    url = db.Column(db.Text, nullable=False)
    owner_name = db.Column(db.String(255), default="")   # person whose calendar this is
    owner_email = db.Column(db.String(255), default="")  # their email
    is_active = db.Column(db.Boolean, default=True)
    last_synced = db.Column(db.DateTime, nullable=True)
    sync_status = db.Column(db.String(20), default="idle")   # idle, ok, error
    error_message = db.Column(db.Text, default="")
    events_imported = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SyncFilter(db.Model):
    """Keywords to block during iCal/email sync — titles containing these are skipped."""
    __tablename__ = "sync_filters"

    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(255), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Task(db.Model):
    """Standalone reminders / action items assigned to a person."""
    __tablename__ = "tasks"

    id             = db.Column(db.Integer, primary_key=True)
    title          = db.Column(db.String(500), nullable=False)
    assigned_to    = db.Column(db.String(255), default="")   # person name
    assigned_email = db.Column(db.String(255), default="")   # for email notification
    due_date       = db.Column(db.DateTime, nullable=True)
    priority       = db.Column(db.String(20), default="normal")  # low, normal, high, critical
    is_completed   = db.Column(db.Boolean, default=False)
    completed_at   = db.Column(db.DateTime, nullable=True)
    notes          = db.Column(db.Text, default="")
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)


class EmailSyncState(db.Model):
    __tablename__ = "email_sync_state"

    id = db.Column(db.Integer, primary_key=True)
    last_sync_uid = db.Column(db.String(255), default="")
    last_sync_time = db.Column(db.DateTime, default=datetime.utcnow)
    sync_status = db.Column(db.String(20), default="idle")
    error_message = db.Column(db.Text, default="")
