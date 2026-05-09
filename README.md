# Meeting Scheduler & Reminder System

A complete meeting management system that syncs with email, provides calendar views, sends reminders, and supports multiple email accounts for your team.

## Features

- **Email Integration** - Automatically pulls meeting invites (ICS/calendar events) from email via IMAP
- **Multi-Email Support** - Add/remove team member email accounts; new joinees and departures handled easily
- **Calendar Dashboard** - Full calendar view (month/week/day/list) powered by FullCalendar
- **Smart Reminders** - Configurable reminders via email and desktop notifications
- **Conflict Detection** - Warns when meetings overlap
- **Meeting Priorities** - Low, Normal, High, Critical priority levels
- **Auto Status Updates** - Meetings auto-transition: Scheduled → In Progress → Completed
- **Search & Filter** - Find meetings by title, organizer, location, or status
- **Tags & Attendees** - Organize meetings with tags and track attendee RSVP
- **CLI Interface** - Manage meetings from the command line
- **REST API** - JSON API for integration with other tools

## Primary Email

The system is pre-configured with: `outreachp689@gmail.com`

You can add more team emails via the web dashboard (Email Accounts page) or CLI.

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your settings (optional - email accounts can be managed via the web UI)
```

### 3. Run the application

```bash
python run.py
```

Open http://localhost:5000 in your browser.

## Email Setup (Gmail)

For Gmail integration, you need an **App Password** (not your regular password):

1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable 2-Step Verification if not already
3. Go to App Passwords → Generate a new one for "Mail"
4. Use this app password when adding the email account

Add the app password either:
- Via Web UI: Go to **Email Accounts** → **Add Email Account**
- Via CLI: `python cli.py add-email`
- Via `.env` file for the default account

## Managing Email Accounts

### Web Dashboard
Navigate to **Email Accounts** in the sidebar to:
- Add new team member emails
- Remove departing team members
- Set primary email account
- Activate/deactivate accounts
- Trigger manual email sync

### CLI
```bash
python cli.py emails              # List all email accounts
python cli.py add-email           # Add a new email account
python cli.py remove-email user@company.com  # Remove an account
```

## CLI Commands

```bash
python cli.py list                # Today's meetings
python cli.py upcoming            # Next 24 hours
python cli.py add                 # Add meeting interactively
python cli.py search <query>      # Search meetings
python cli.py sync                # Sync all email accounts
python cli.py stats               # Meeting statistics
python cli.py emails              # List email accounts
python cli.py add-email           # Add email account
python cli.py remove-email <email>  # Remove email account
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/meetings` | List meetings (supports `start` and `end` query params) |
| POST | `/api/meetings` | Create a meeting (JSON body) |
| DELETE | `/api/meetings/<id>` | Delete a meeting |
| POST | `/api/sync-email` | Trigger email sync for all accounts |
| GET | `/api/stats` | Meeting statistics |

### Create Meeting via API

```bash
curl -X POST http://localhost:5000/api/meetings \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Team Standup",
    "start_time": "2026-05-09T10:00:00",
    "end_time": "2026-05-09T10:30:00",
    "location": "Conference Room A",
    "priority": "normal",
    "reminder_minutes": [15, 5],
    "attendees": [{"email": "alice@company.com"}],
    "tags": ["standup", "daily"]
  }'
```

## Project Structure

```
├── run.py                  # Main entry point (web + background jobs)
├── cli.py                  # Command-line interface
├── requirements.txt        # Python dependencies
├── .env.example            # Environment config template
├── app/
│   ├── __init__.py         # Flask app factory
│   ├── models.py           # Database models
│   ├── routes.py           # Web routes + API
│   ├── meeting_manager.py  # Business logic
│   ├── email_integration.py # IMAP email sync + ICS parsing
│   └── reminder_engine.py  # Background reminder scheduler
└── templates/
    ├── base.html           # Layout with sidebar
    ├── dashboard.html      # Main dashboard
    ├── calendar.html       # Calendar view
    ├── meetings.html       # Meetings list
    ├── meeting_form.html   # Create meeting form
    ├── meeting_detail.html # Meeting details
    └── email_accounts.html # Email account management
```

## How It Works

1. **Email Sync**: Background job polls configured IMAP accounts every 5 minutes for new meeting invites (ICS calendar events). Also detects meeting-like emails by subject keywords.
2. **Reminders**: Background scheduler checks every 60 seconds for pending reminders. Sends email to all notification-enabled accounts and shows desktop notifications.
3. **Auto Status**: Meetings automatically transition from "scheduled" to "in_progress" when start time passes, and to "completed" when end time passes.
