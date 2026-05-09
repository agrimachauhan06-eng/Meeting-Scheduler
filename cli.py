"""
Meeting Scheduler CLI - Manage meetings from the command line.

Usage:
    python cli.py list              List today's meetings
    python cli.py upcoming          List upcoming meetings (next 24h)
    python cli.py add               Add a new meeting interactively
    python cli.py search <query>    Search meetings
    python cli.py sync              Sync emails now
    python cli.py stats             Show meeting statistics
    python cli.py emails            List configured email accounts
    python cli.py add-email         Add a new email account
    python cli.py remove-email <email>  Remove an email account
"""

import sys
from datetime import datetime, timedelta

from app import create_app, db
from app.meeting_manager import MeetingManager
from app.models import EmailAccount


app = create_app()


def list_meetings():
    with app.app_context():
        meetings = MeetingManager.get_todays_meetings()
        if not meetings:
            print("No meetings scheduled for today.")
            return
        print(f"\n{'='*60}")
        print(f"  Today's Meetings ({datetime.now().strftime('%B %d, %Y')})")
        print(f"{'='*60}")
        for m in meetings:
            status_icon = {"scheduled": "[>]", "in_progress": "[~]", "completed": "[v]", "cancelled": "[x]"}.get(m.status, "[ ]")
            print(f"  {status_icon} {m.start_time.strftime('%I:%M %p')} - {m.end_time.strftime('%I:%M %p')}  {m.title}")
            if m.location:
                print(f"       Location: {m.location}")
            if m.meeting_link:
                print(f"       Link: {m.meeting_link}")
        print()


def upcoming_meetings():
    with app.app_context():
        meetings = MeetingManager.get_upcoming_meetings(hours=24)
        if not meetings:
            print("No upcoming meetings in the next 24 hours.")
            return
        print(f"\n{'='*60}")
        print(f"  Upcoming Meetings (Next 24 Hours)")
        print(f"{'='*60}")
        for m in meetings:
            print(f"  [{m.priority[0].upper()}] {m.start_time.strftime('%b %d %I:%M %p')}  {m.title}")
            if m.organizer:
                print(f"       Organizer: {m.organizer}")
        print()


def add_meeting():
    with app.app_context():
        print("\n--- Add New Meeting ---")
        title = input("Title: ").strip()
        if not title:
            print("Title is required.")
            return

        start_str = input("Start time (YYYY-MM-DD HH:MM): ").strip()
        try:
            start_time = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        except ValueError:
            print("Invalid date format.")
            return

        duration = input("Duration in minutes (default 60): ").strip()
        duration = int(duration) if duration.isdigit() else 60
        end_time = start_time + timedelta(minutes=duration)

        description = input("Description (optional): ").strip()
        location = input("Location (optional): ").strip()
        meeting_link = input("Meeting link (optional): ").strip()
        priority = input("Priority (low/normal/high/critical, default normal): ").strip() or "normal"

        meeting = MeetingManager.create_meeting(
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            meeting_link=meeting_link,
            priority=priority,
        )
        print(f"\nMeeting '{meeting.title}' created! (ID: {meeting.id})")


def search_meetings(query):
    with app.app_context():
        meetings = MeetingManager.search_meetings(query)
        if not meetings:
            print(f"No meetings found matching '{query}'.")
            return
        print(f"\nFound {len(meetings)} meeting(s):")
        for m in meetings:
            print(f"  [{m.id}] {m.start_time.strftime('%b %d %I:%M %p')} - {m.title} ({m.status})")


def sync_emails():
    with app.app_context():
        accounts = EmailAccount.query.filter_by(is_active=True, sync_enabled=True).all()
        if not accounts:
            print("No active email accounts configured. Use 'add-email' to add one.")
            return
        from app.email_integration import EmailIntegration
        total = 0
        for acc in accounts:
            print(f"Syncing {acc.email}...")
            ei = EmailIntegration(account=acc)
            new = ei.sync_meetings_from_email()
            total += len(new)
            print(f"  Found {len(new)} new meeting(s).")
        print(f"\nTotal: {total} new meeting(s) synced.")


def show_stats():
    with app.app_context():
        stats = MeetingManager.get_meeting_stats()
        print(f"\n--- Meeting Statistics ---")
        print(f"  Today:     {stats['today']}")
        print(f"  This Week: {stats['this_week']}")
        print(f"  Total:     {stats['total']}")
        print(f"  Cancelled: {stats['cancelled']}")


def list_emails():
    with app.app_context():
        accounts = EmailAccount.query.all()
        if not accounts:
            print("No email accounts configured.")
            return
        print(f"\n--- Email Accounts ---")
        for acc in accounts:
            status = "Active" if acc.is_active else "Inactive"
            primary = " [PRIMARY]" if acc.is_primary else ""
            print(f"  {acc.email}{primary} - {status} (sync: {acc.sync_status})")


def add_email_account():
    with app.app_context():
        print("\n--- Add Email Account ---")
        email_addr = input("Email address: ").strip()
        if not email_addr:
            print("Email is required.")
            return

        existing = EmailAccount.query.filter_by(email=email_addr).first()
        if existing:
            print(f"Email '{email_addr}' already exists.")
            return

        display_name = input("Display name (optional): ").strip()
        password = input("App password: ").strip()
        imap_host = input("IMAP host (default imap.gmail.com): ").strip() or "imap.gmail.com"

        is_primary = EmailAccount.query.count() == 0

        acc = EmailAccount(
            email=email_addr,
            display_name=display_name,
            password=password,
            imap_host=imap_host,
            is_primary=is_primary,
            is_active=True,
        )
        db.session.add(acc)
        db.session.commit()
        print(f"Email account '{email_addr}' added!{' (set as primary)' if is_primary else ''}")


def remove_email_account(email_addr):
    with app.app_context():
        acc = EmailAccount.query.filter_by(email=email_addr).first()
        if not acc:
            print(f"Email '{email_addr}' not found.")
            return
        if acc.is_primary:
            print("Cannot remove the primary email account. Set another account as primary first.")
            return
        db.session.delete(acc)
        db.session.commit()
        print(f"Email account '{email_addr}' removed.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()

    if command == "list":
        list_meetings()
    elif command == "upcoming":
        upcoming_meetings()
    elif command == "add":
        add_meeting()
    elif command == "search":
        if len(sys.argv) < 3:
            print("Usage: python cli.py search <query>")
            return
        search_meetings(" ".join(sys.argv[2:]))
    elif command == "sync":
        sync_emails()
    elif command == "stats":
        show_stats()
    elif command == "emails":
        list_emails()
    elif command == "add-email":
        add_email_account()
    elif command == "remove-email":
        if len(sys.argv) < 3:
            print("Usage: python cli.py remove-email <email>")
            return
        remove_email_account(sys.argv[2])
    else:
        print(f"Unknown command: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
