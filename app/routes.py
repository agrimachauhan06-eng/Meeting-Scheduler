import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash

from app import db
from app.models import Meeting, Reminder, EmailAccount, CalendarFeed, SyncFilter
from app.meeting_manager import MeetingManager
from app.invite_service import InviteService

main_bp = Blueprint("main", __name__)


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if not key or key != os.getenv("API_KEY", ""):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@main_bp.route("/")
def dashboard():
    MeetingManager.auto_complete_past_meetings()
    stats = MeetingManager.get_meeting_stats()
    upcoming = MeetingManager.get_upcoming_meetings(hours=48)
    todays = MeetingManager.get_todays_meetings()
    return render_template(
        "dashboard.html", stats=stats, upcoming=upcoming, todays=todays,
        now=datetime.utcnow()
    )


@main_bp.route("/calendar")
def calendar_view():
    return render_template("calendar.html")


@main_bp.route("/availability")
def team_availability():
    from app.models import Attendee

    WORK_START = 8   # 8am
    WORK_END   = 20  # 8pm
    TOTAL_MINS = (WORK_END - WORK_START) * 60

    week_offset = request.args.get("week", 0, type=int)
    today       = datetime.utcnow().date()
    week_start  = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    week_days   = [week_start + timedelta(days=i) for i in range(5)]  # Mon–Fri

    # All meetings in this week
    wk_start_dt = datetime(week_start.year, week_start.month, week_start.day)
    wk_end_dt   = wk_start_dt + timedelta(days=5)
    meetings = Meeting.query.filter(
        Meeting.start_time >= wk_start_dt,
        Meeting.start_time <  wk_end_dt,
        Meeting.status != "cancelled",
    ).all()

    def _meeting_block(meeting):
        start_mins    = (meeting.start_time.hour - WORK_START) * 60 + meeting.start_time.minute
        duration_mins = max(15, int((meeting.end_time - meeting.start_time).total_seconds() / 60))
        top_pct    = max(0, round(start_mins / TOTAL_MINS * 100, 2))
        height_pct = min(round(duration_mins / TOTAL_MINS * 100, 2), 100 - top_pct)
        return {
            "id":         meeting.id,
            "title":      meeting.title,
            "start":      meeting.start_time.strftime("%I:%M %p"),
            "end":        meeting.end_time.strftime("%I:%M %p"),
            "top_pct":    top_pct,
            "height_pct": max(height_pct, 4),
        }

    # Build per-person data keyed by email
    people = {}
    unassigned_days = {d.isoformat(): [] for d in week_days}

    for meeting in meetings:
        day_key = meeting.start_time.date().isoformat()
        if meeting.attendees:
            for att in meeting.attendees:
                email = att.email.lower().strip()
                if email not in people:
                    people[email] = {
                        "name":  att.name or email.split("@")[0].title(),
                        "email": email,
                        "days":  {d.isoformat(): [] for d in week_days},
                    }
                if day_key in people[email]["days"]:
                    people[email]["days"][day_key].append(_meeting_block(meeting))
        else:
            if day_key in unassigned_days:
                unassigned_days[day_key].append(_meeting_block(meeting))

    # Add "Unassigned" row if there are any no-attendee meetings this week
    if any(unassigned_days[d.isoformat()] for d in week_days):
        people["__unassigned__"] = {
            "name":  "Unassigned",
            "email": "__unassigned__",
            "days":  unassigned_days,
        }

    # Find best free slots (everyone free for ≥30 min)
    all_emails = list(people.keys())
    best_slots = []
    if all_emails:
        for day in week_days:
            day_key = day.isoformat()
            # Build blocked intervals for ALL people on this day
            blocked = []
            for p in people.values():
                for m in p["days"][day_key]:
                    # convert back from pct to minutes
                    s = int(m["top_pct"]    / 100 * TOTAL_MINS)
                    e = int((m["top_pct"] + m["height_pct"]) / 100 * TOTAL_MINS)
                    blocked.append((s, e))
            blocked.sort()

            # Merge overlapping blocks
            merged = []
            for s, e in blocked:
                if merged and s <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], e))
                else:
                    merged.append([s, e])

            # Find gaps ≥30 min
            prev = 0
            for s, e in merged:
                if s - prev >= 30:
                    slot_start = WORK_START * 60 + prev
                    slot_end   = WORK_START * 60 + s
                    best_slots.append({
                        "day":   day.strftime("%a %b %d"),
                        "start": f"{slot_start//60:02d}:{slot_start%60:02d}",
                        "end":   f"{slot_end//60:02d}:{slot_end%60:02d}",
                        "mins":  s - prev,
                    })
                prev = e
            # gap after last block
            if TOTAL_MINS - prev >= 30:
                slot_start = WORK_START * 60 + prev
                best_slots.append({
                    "day":   day.strftime("%a %b %d"),
                    "start": f"{slot_start//60:02d}:{slot_start%60:02d}",
                    "end":   f"{WORK_END:02d}:00",
                    "mins":  TOTAL_MINS - prev,
                })

    # Hour labels for the grid
    hour_labels = [f"{h:02d}:00" for h in range(WORK_START, WORK_END + 1)]

    return render_template(
        "availability.html",
        people=list(people.values()),
        week_days=week_days,
        week_offset=week_offset,
        hour_labels=hour_labels,
        best_slots=best_slots[:8],  # top 8 slots
        WORK_START=WORK_START,
        TOTAL_MINS=TOTAL_MINS,
    )


@main_bp.route("/meetings")
def meetings_list():
    MeetingManager.auto_complete_past_meetings()
    page = request.args.get("page", 1, type=int)
    status = request.args.get("status", "all")
    search = request.args.get("search", "")

    query = Meeting.query
    if status != "all":
        query = query.filter(Meeting.status == status)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            db.or_(
                Meeting.title.ilike(pattern),
                Meeting.organizer.ilike(pattern),
            )
        )

    meetings = query.order_by(Meeting.start_time.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template("meetings.html", meetings=meetings, status=status, search=search)


@main_bp.route("/meetings/new", methods=["GET", "POST"])
def create_meeting():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        start_str = request.form.get("start_time", "")
        end_str = request.form.get("end_time", "")
        description = request.form.get("description", "")
        location = request.form.get("location", "")
        meeting_link = request.form.get("meeting_link", "")
        priority = request.form.get("priority", "normal")
        reminder_mins = request.form.get("reminder_minutes", "15,5")
        tags_str = request.form.get("tags", "")
        attendees_str = request.form.get("attendees", "")

        if not title or not start_str or not end_str:
            flash("Title, start time, and end time are required.", "error")
            return render_template("meeting_form.html")

        try:
            start_time = datetime.fromisoformat(start_str)
            end_time = datetime.fromisoformat(end_str)
        except ValueError:
            flash("Invalid date format.", "error")
            return render_template("meeting_form.html")

        if end_time <= start_time:
            flash("End time must be after start time.", "error")
            return render_template("meeting_form.html")

        conflicts = MeetingManager.get_conflicts(start_time, end_time)
        reminder_minutes = [int(m.strip()) for m in reminder_mins.split(",") if m.strip().isdigit()]
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        attendees = []
        for a in attendees_str.split(","):
            a = a.strip()
            if a:
                attendees.append({"email": a})

        meeting = MeetingManager.create_meeting(
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            meeting_link=meeting_link,
            priority=priority,
            reminder_minutes=reminder_minutes,
            tags=tags,
            attendees=attendees,
        )

        if conflicts:
            flash(f"Warning: This meeting conflicts with {len(conflicts)} other meeting(s).", "warning")

        if meeting.attendees:
            try:
                sent = InviteService.send_invites(meeting)
                if sent:
                    flash(f"Invite sent to {sent} attendee(s).", "info")
            except Exception as e:
                flash("Meeting created but invites could not be sent (SMTP not configured).", "warning")

        flash(f"Meeting '{title}' created successfully!", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("meeting_form.html")


@main_bp.route("/meetings/<int:meeting_id>/edit", methods=["GET", "POST"])
def edit_meeting(meeting_id):
    meeting = MeetingManager.get_meeting(meeting_id)
    if not meeting:
        flash("Meeting not found.", "error")
        return redirect(url_for("main.meetings_list"))

    if request.method == "POST":
        title        = request.form.get("title", "").strip()
        start_str    = request.form.get("start_time", "")
        end_str      = request.form.get("end_time", "")
        description  = request.form.get("description", "")
        location     = request.form.get("location", "")
        meeting_link = request.form.get("meeting_link", "")
        priority     = request.form.get("priority", "normal")
        attendees_str= request.form.get("attendees", "")
        tags_str     = request.form.get("tags", "")

        if not title or not start_str or not end_str:
            flash("Title, start time, and end time are required.", "error")
            return render_template("meeting_form.html", meeting=meeting)

        try:
            start_time = datetime.fromisoformat(start_str)
            end_time   = datetime.fromisoformat(end_str)
        except ValueError:
            flash("Invalid date format.", "error")
            return render_template("meeting_form.html", meeting=meeting)

        if end_time <= start_time:
            flash("End time must be after start time.", "error")
            return render_template("meeting_form.html", meeting=meeting)

        # Update core fields
        meeting.title        = title
        meeting.start_time   = start_time
        meeting.end_time     = end_time
        meeting.description  = description
        meeting.location     = location
        meeting.meeting_link = meeting_link
        meeting.priority     = priority

        # Replace attendees
        from app.models import Attendee, MeetingTag
        Attendee.query.filter_by(meeting_id=meeting.id).delete()
        for a in attendees_str.split(","):
            a = a.strip()
            if a:
                db.session.add(Attendee(meeting_id=meeting.id, email=a))

        # Replace tags
        MeetingTag.query.filter_by(meeting_id=meeting.id).delete()
        for t in [t.strip() for t in tags_str.split(",") if t.strip()]:
            db.session.add(MeetingTag(meeting_id=meeting.id, tag_name=t))

        db.session.commit()
        flash(f"Meeting updated successfully.", "success")
        return redirect(url_for("main.meeting_detail", meeting_id=meeting.id))

    return render_template("meeting_form.html", meeting=meeting)


@main_bp.route("/meetings/<int:meeting_id>")
def meeting_detail(meeting_id):
    meeting = MeetingManager.get_meeting(meeting_id)
    if not meeting:
        flash("Meeting not found.", "error")
        return redirect(url_for("main.meetings_list"))
    return render_template("meeting_detail.html", meeting=meeting)


@main_bp.route("/meetings/<int:meeting_id>/notes", methods=["GET"])
def view_meeting_notes(meeting_id):
    meeting = Meeting.query.get_or_404(meeting_id)
    return render_template("meeting_notes.html", meeting=meeting)


@main_bp.route("/meetings/<int:meeting_id>/notes", methods=["POST"])
def save_meeting_notes(meeting_id):
    meeting = Meeting.query.get_or_404(meeting_id)
    meeting.transcript = request.form.get("transcript", "").strip()
    meeting.formatted_transcript = ""  # clear formatted version when raw notes are updated
    db.session.commit()
    flash("Notes saved.", "success")
    return redirect(url_for("main.view_meeting_notes", meeting_id=meeting_id))


@main_bp.route("/meetings/<int:meeting_id>/notes/format", methods=["POST"])
def format_meeting_notes(meeting_id):
    meeting = Meeting.query.get_or_404(meeting_id)
    if not meeting.transcript or not meeting.transcript.strip():
        flash("No notes to format.", "warning")
        return redirect(url_for("main.view_meeting_notes", meeting_id=meeting_id))
    try:
        import google.generativeai as genai
        import os
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            "You are a meeting notes formatter. Format the following raw speech-to-text transcript "
            "into clean, well-structured meeting notes. Use clear section headers for each topic, "
            "bullet points for action items prefixed with '[ ]', and bullet points for discussion points. "
            "Remove filler words and speech artifacts. Fix grammar and punctuation. "
            "Keep ALL information — do not summarise or drop any details. "
            "Return only the formatted notes in plain markdown. No explanations.\n\n"
            "Raw transcript:\n" + meeting.transcript
        )
        response = model.generate_content(prompt)
        meeting.formatted_transcript = response.text.strip()
        db.session.commit()
        flash("Notes formatted successfully.", "success")
    except Exception as e:
        flash(f"Formatting failed: {e}", "error")
    return redirect(url_for("main.view_meeting_notes", meeting_id=meeting_id))


@main_bp.route("/meetings/<int:meeting_id>/cancel", methods=["POST"])
def cancel_meeting(meeting_id):
    MeetingManager.cancel_meeting(meeting_id)
    flash("Meeting cancelled.", "info")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/meetings/<int:meeting_id>/delete", methods=["POST"])
def delete_meeting(meeting_id):
    MeetingManager.delete_meeting(meeting_id)
    flash("Meeting deleted.", "info")
    return redirect(url_for("main.meetings_list"))


# --- API Endpoints ---

@main_bp.route("/api/meetings")
def api_meetings():
    MeetingManager.auto_complete_past_meetings()
    start = request.args.get("start")
    end = request.args.get("end")

    if start and end:
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", ""))
            end_dt = datetime.fromisoformat(end.replace("Z", ""))
            meetings = MeetingManager.get_meetings_in_range(start_dt, end_dt)
        except ValueError:
            meetings = MeetingManager.get_upcoming_meetings(hours=168)
    else:
        meetings = MeetingManager.get_upcoming_meetings(hours=168)

    palette = {
        "critical": {"backgroundColor": "#FFE4E6", "textColor": "#9F1239", "borderColor": "#F43F5E"},
        "high":     {"backgroundColor": "#FEF3C7", "textColor": "#92400E", "borderColor": "#F59E0B"},
        "normal":   {"backgroundColor": "#E0E7FF", "textColor": "#3730A3", "borderColor": "#6366F1"},
        "low":      {"backgroundColor": "#F3F4F6", "textColor": "#374151", "borderColor": "#9CA3AF"},
    }
    cancelled_style = {"backgroundColor": "#F1F5F9", "textColor": "#94A3B8", "borderColor": "#CBD5E1"}

    events = []
    for m in meetings:
        style = cancelled_style if m.status == "cancelled" else palette.get(m.priority, palette["normal"])

        priority_key = "cancelled" if m.status == "cancelled" else (m.priority or "normal")
        events.append({
            "id": m.id,
            "title": m.title,
            "start": m.start_time.isoformat(),
            "end": m.end_time.isoformat(),
            "backgroundColor": style["backgroundColor"],
            "textColor": style["textColor"],
            "borderColor": style["borderColor"],
            "classNames": [f"fc-priority-{priority_key}"],
            "url": f"/meetings/{m.id}",
            "extendedProps": {
                "location": m.location,
                "organizer": m.organizer,
                "status": m.status,
                "priority": m.priority,
            },
        })
    return jsonify(events)


@main_bp.route("/api/meetings", methods=["POST"])
@require_api_key
def api_create_meeting():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ["title", "start_time", "end_time"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    try:
        start_time = datetime.fromisoformat(data["start_time"])
        end_time = datetime.fromisoformat(data["end_time"])
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    meeting = MeetingManager.create_meeting(
        title=data["title"],
        start_time=start_time,
        end_time=end_time,
        description=data.get("description", ""),
        location=data.get("location", ""),
        meeting_link=data.get("meeting_link", ""),
        priority=data.get("priority", "normal"),
        attendees=data.get("attendees"),
        reminder_minutes=data.get("reminder_minutes"),
        tags=data.get("tags"),
    )

    if meeting.attendees:
        try:
            InviteService.send_invites(meeting)
        except Exception:
            pass

    return jsonify(meeting.to_dict()), 201


@main_bp.route("/api/meetings/<int:meeting_id>", methods=["DELETE"])
@require_api_key
def api_delete_meeting(meeting_id):
    if MeetingManager.delete_meeting(meeting_id):
        return jsonify({"message": "Deleted"}), 200
    return jsonify({"error": "Not found"}), 404


@main_bp.route("/api/sync-email", methods=["POST"])
def api_sync_email():
    try:
        from app.email_integration import EmailIntegration
        accounts = EmailAccount.query.filter_by(is_active=True, sync_enabled=True).all()
        total = 0
        for acc in accounts:
            ei = EmailIntegration(account=acc)
            new = ei.sync_meetings_from_email()
            total += len(new)
        return jsonify({"synced": total}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@main_bp.route("/api/sync-email-account/<int:account_id>", methods=["POST"])
def api_sync_email_account(account_id):
    acc = EmailAccount.query.get(account_id)
    if not acc:
        flash("Email account not found.", "error")
        return redirect(url_for("main.email_accounts"))
    try:
        from app.email_integration import EmailIntegration
        ei = EmailIntegration(account=acc)
        new = ei.sync_meetings_from_email()
        flash(f"Synced {len(new)} meeting(s) from {acc.email}.", "success")
    except Exception as e:
        flash(f"Sync failed: {e}", "error")
    return redirect(url_for("main.email_accounts"))


@main_bp.route("/api/stats")
def api_stats():
    return jsonify(MeetingManager.get_meeting_stats())


# --- Email Account Management ---

@main_bp.route("/settings/emails")
def email_accounts():
    accounts = EmailAccount.query.order_by(EmailAccount.is_primary.desc()).all()
    return render_template("email_accounts.html", accounts=accounts)


@main_bp.route("/settings/emails/add", methods=["POST"])
def add_email_account():
    email_addr = request.form.get("email", "").strip()
    if not email_addr:
        flash("Email address is required.", "error")
        return redirect(url_for("main.email_accounts"))

    existing = EmailAccount.query.filter_by(email=email_addr).first()
    if existing:
        flash(f"Email '{email_addr}' already exists.", "error")
        return redirect(url_for("main.email_accounts"))

    is_primary = EmailAccount.query.count() == 0

    acc = EmailAccount(
        email=email_addr,
        display_name=request.form.get("display_name", ""),
        password=request.form.get("password", ""),
        imap_host=request.form.get("imap_host", "imap.gmail.com"),
        imap_port=int(request.form.get("imap_port", "993")),
        smtp_host=request.form.get("smtp_host", "smtp.gmail.com"),
        smtp_port=int(request.form.get("smtp_port", "587")),
        sync_enabled="sync_enabled" in request.form,
        notify_enabled="notify_enabled" in request.form,
        is_primary=is_primary,
        is_active=True,
    )
    db.session.add(acc)
    db.session.commit()
    flash(f"Email account '{email_addr}' added successfully!", "success")
    return redirect(url_for("main.email_accounts"))


@main_bp.route("/settings/emails/<int:account_id>/toggle", methods=["POST"])
def toggle_email_account(account_id):
    acc = EmailAccount.query.get(account_id)
    if acc:
        acc.is_active = not acc.is_active
        db.session.commit()
        status = "activated" if acc.is_active else "deactivated"
        flash(f"Email account {acc.email} {status}.", "info")
    return redirect(url_for("main.email_accounts"))


@main_bp.route("/settings/emails/<int:account_id>/set-primary", methods=["POST"])
def set_primary_email(account_id):
    EmailAccount.query.update({EmailAccount.is_primary: False})
    acc = EmailAccount.query.get(account_id)
    if acc:
        acc.is_primary = True
        db.session.commit()
        flash(f"{acc.email} is now the primary email account.", "success")
    return redirect(url_for("main.email_accounts"))


@main_bp.route("/settings/emails/<int:account_id>/delete", methods=["POST"])
def delete_email_account(account_id):
    acc = EmailAccount.query.get(account_id)
    if not acc:
        flash("Account not found.", "error")
    elif acc.is_primary:
        flash("Cannot delete the primary email account. Set another as primary first.", "error")
    else:
        db.session.delete(acc)
        db.session.commit()
        flash(f"Email account '{acc.email}' removed.", "info")
    return redirect(url_for("main.email_accounts"))


# --- Public Booking Page ---

def _get_available_slots(date, duration_min):
    """Return list of available UTC slot dicts for a given date and duration."""
    from datetime import date as date_type
    WORK_START = 8   # 8am UTC
    WORK_END   = 20  # 8pm UTC
    BUFFER_MIN = 15
    STEP_MIN   = 15

    day_start = datetime(date.year, date.month, date.day, 0, 0, 0)
    day_end   = day_start + timedelta(days=1)

    meetings = Meeting.query.filter(
        Meeting.start_time >= day_start,
        Meeting.start_time < day_end,
        Meeting.status != "cancelled",
    ).all()

    blocked = [
        (m.start_time, m.end_time + timedelta(minutes=BUFFER_MIN))
        for m in meetings
    ]

    slots = []
    current = datetime(date.year, date.month, date.day, WORK_START, 0, 0)
    end_of_day = datetime(date.year, date.month, date.day, WORK_END, 0, 0)
    now_utc = datetime.utcnow()

    while current + timedelta(minutes=duration_min) <= end_of_day:
        slot_end = current + timedelta(minutes=duration_min)
        if current > now_utc:
            available = all(
                not (current < b_end and slot_end > b_start)
                for b_start, b_end in blocked
            )
            if available:
                slots.append({
                    "utc": current.strftime("%Y-%m-%dT%H:%M:00"),
                    "time": current.strftime("%H:%M"),
                    "label": current.strftime("%I:%M %p"),
                })
        current += timedelta(minutes=STEP_MIN)

    return slots


@main_bp.route("/book", methods=["GET"])
def book_meeting():
    primary = EmailAccount.query.filter_by(is_primary=True).first()
    organizer_name = (primary.display_name or primary.email) if primary else "Us"
    return render_template("booking.html", organizer_name=organizer_name)


@main_bp.route("/book", methods=["POST"])
def book_meeting_submit():
    name      = request.form.get("name", "").strip()
    email     = request.form.get("email", "").strip()
    title     = request.form.get("title", "").strip()
    notes     = request.form.get("notes", "").strip()
    utc_time  = request.form.get("utc_time", "").strip()
    duration  = request.form.get("duration", "30").strip()

    if not all([name, email, title, utc_time, duration]):
        flash("Please fill in all required fields and select a time slot.", "error")
        return redirect(url_for("main.book_meeting"))

    try:
        start_time = datetime.fromisoformat(utc_time)
        duration_min = int(duration)
        end_time = start_time + timedelta(minutes=duration_min)
    except (ValueError, TypeError):
        flash("Invalid time selection. Please try again.", "error")
        return redirect(url_for("main.book_meeting"))

    meeting = MeetingManager.create_meeting(
        title=title,
        start_time=start_time,
        end_time=end_time,
        description=notes,
        source="booking",
        attendees=[{"name": name, "email": email}],
        reminder_minutes=[15],
    )

    try:
        InviteService.send_invites(meeting)
    except Exception:
        pass

    return redirect(url_for("main.book_confirmed", meeting_id=meeting.id))


@main_bp.route("/book/confirmed")
def book_confirmed():
    meeting_id = request.args.get("meeting_id", type=int)
    meeting = MeetingManager.get_meeting(meeting_id) if meeting_id else None
    return render_template("booking_confirmed.html", meeting=meeting)


@main_bp.route("/api/booking/slots")
def api_booking_slots():
    date_str = request.args.get("date", "")
    duration = request.args.get("duration", 30, type=int)

    if not date_str:
        return jsonify({"error": "date required"}), 400

    try:
        from datetime import date as date_type
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "invalid date"}), 400

    if date.weekday() >= 5:
        return jsonify({"slots": [], "reason": "weekend"})

    if date < datetime.utcnow().date():
        return jsonify({"slots": [], "reason": "past"})

    slots = _get_available_slots(date, duration)
    return jsonify({"slots": slots})


# --- iCal Calendar Feeds ---

def _sync_feed(feed):
    """Fetch + parse an iCal URL, import new events. Returns count imported."""
    import urllib.request
    from icalendar import Calendar as ICal
    from datetime import date as date_type

    try:
        req = urllib.request.Request(feed.url, headers={"User-Agent": "Nexus/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except Exception as e:
        raise RuntimeError(f"Could not fetch URL: {e}")

    try:
        cal = ICal.from_ical(raw)
    except Exception as e:
        raise RuntimeError(f"Invalid iCal data: {e}")

    # Load active blocked keywords once
    blocked_keywords = [
        f.keyword.lower()
        for f in SyncFilter.query.filter_by(is_active=True).all()
    ]

    imported = 0
    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", "")).strip()
        summary = str(component.get("SUMMARY", "Untitled Event")).strip()

        # Skip if title contains any blocked keyword
        summary_lower = summary.lower()
        if any(kw in summary_lower for kw in blocked_keywords):
            continue

        dtstart = component.get("DTSTART")
        dtend   = component.get("DTEND") or component.get("DTSTART")
        if not dtstart:
            continue

        start_dt = dtstart.dt
        end_dt   = dtend.dt if dtend else dtstart.dt

        # All-day event → convert date to datetime
        if isinstance(start_dt, date_type) and not isinstance(start_dt, datetime):
            start_dt = datetime(start_dt.year, start_dt.month, start_dt.day, 9, 0, 0)
        if isinstance(end_dt, date_type) and not isinstance(end_dt, datetime):
            end_dt = datetime(end_dt.year, end_dt.month, end_dt.day, 10, 0, 0)

        # Strip timezone info (store as UTC-naive)
        if hasattr(start_dt, "tzinfo") and start_dt.tzinfo:
            start_dt = start_dt.replace(tzinfo=None)
        if hasattr(end_dt, "tzinfo") and end_dt.tzinfo:
            end_dt = end_dt.replace(tzinfo=None)

        description = str(component.get("DESCRIPTION", "")).strip()
        location    = str(component.get("LOCATION", "")).strip()
        organizer_str = ""

        # Parse iCal ORGANIZER
        ical_attendees = []
        org_prop = component.get("ORGANIZER")
        if org_prop:
            org_email = str(org_prop).replace("mailto:", "").strip()
            org_cn = ""
            if hasattr(org_prop, "params"):
                org_cn = str(org_prop.params.get("CN", "")).strip()
            organizer_str = org_cn or org_email
            if "@" in org_email:
                ical_attendees.append({"name": org_cn, "email": org_email})

        # Parse iCal ATTENDEE(s) — may be a list or single value
        att_props = component.get("ATTENDEE") or []
        if not isinstance(att_props, list):
            att_props = [att_props]
        seen_emails = {a["email"] for a in ical_attendees}
        for att_prop in att_props:
            att_email = str(att_prop).replace("mailto:", "").strip()
            att_cn = ""
            if hasattr(att_prop, "params"):
                att_cn = str(att_prop.params.get("CN", "")).strip()
            if "@" in att_email and att_email not in seen_emails:
                ical_attendees.append({"name": att_cn, "email": att_email})
                seen_emails.add(att_email)

        # Fall back to feed owner (if set) so meeting shows under the right person
        if not ical_attendees and feed.owner_email:
            ical_attendees = [{"name": feed.owner_name or feed.name, "email": feed.owner_email}]

        # Deduplicate by calendar_uid — backfill attendees if missing on existing meeting
        if uid:
            existing = Meeting.query.filter_by(calendar_uid=uid).first()
            if existing:
                if not existing.attendees and ical_attendees:
                    from app.models import Attendee as AttendeeModel
                    for att in ical_attendees:
                        db.session.add(AttendeeModel(
                            meeting_id=existing.id,
                            name=att.get("name", ""),
                            email=att.get("email", ""),
                        ))
                    db.session.commit()
                continue

        MeetingManager.create_meeting(
            title=summary,
            start_time=start_dt,
            end_time=end_dt,
            description=description,
            location=location,
            organizer=organizer_str,
            source="ical",
            calendar_uid=uid,
            attendees=ical_attendees,
        )
        imported += 1

    feed.last_synced    = datetime.utcnow()
    feed.sync_status    = "ok"
    feed.error_message  = ""
    feed.events_imported = (feed.events_imported or 0) + imported
    db.session.commit()
    return imported


@main_bp.route("/settings/calendar-feeds")
def calendar_feeds():
    feeds = CalendarFeed.query.order_by(CalendarFeed.created_at.desc()).all()
    return render_template("calendar_feeds.html", feeds=feeds)


@main_bp.route("/settings/calendar-feeds/add", methods=["POST"])
def add_calendar_feed():
    name = request.form.get("name", "").strip()
    url  = request.form.get("url", "").strip()
    if not name or not url:
        flash("Name and URL are required.", "error")
        return redirect(url_for("main.calendar_feeds"))
    if not url.startswith("http"):
        flash("URL must start with http:// or https://", "error")
        return redirect(url_for("main.calendar_feeds"))

    owner_name  = request.form.get("owner_name", "").strip()
    owner_email = request.form.get("owner_email", "").strip()
    feed = CalendarFeed(name=name, url=url, owner_name=owner_name, owner_email=owner_email)
    db.session.add(feed)
    db.session.commit()
    flash(f"Calendar feed '{name}' added. Click Sync to import events.", "success")
    return redirect(url_for("main.calendar_feeds"))


@main_bp.route("/settings/calendar-feeds/<int:feed_id>/delete", methods=["POST"])
def delete_calendar_feed(feed_id):
    feed = CalendarFeed.query.get(feed_id)
    if feed:
        db.session.delete(feed)
        db.session.commit()
        flash(f"Feed '{feed.name}' removed.", "info")
    return redirect(url_for("main.calendar_feeds"))


@main_bp.route("/settings/calendar-feeds/<int:feed_id>/toggle", methods=["POST"])
def toggle_calendar_feed(feed_id):
    feed = CalendarFeed.query.get(feed_id)
    if feed:
        feed.is_active = not feed.is_active
        db.session.commit()
    return redirect(url_for("main.calendar_feeds"))


@main_bp.route("/api/sync-calendar-feed/<int:feed_id>", methods=["POST"])
def api_sync_calendar_feed(feed_id):
    feed = CalendarFeed.query.get(feed_id)
    if not feed:
        flash("Feed not found.", "error")
        return redirect(url_for("main.calendar_feeds"))
    try:
        count = _sync_feed(feed)
        flash(f"Synced {count} new event(s) from '{feed.name}'.", "success")
    except Exception as e:
        feed.sync_status   = "error"
        feed.error_message = str(e)[:255]
        db.session.commit()
        flash(f"Sync failed: {e}", "error")
    return redirect(url_for("main.calendar_feeds"))


@main_bp.route("/api/sync-calendar", methods=["POST"])
def api_sync_all_calendars():
    feeds = CalendarFeed.query.filter_by(is_active=True).all()
    total = 0
    errors = []
    for feed in feeds:
        try:
            total += _sync_feed(feed)
        except Exception as e:
            errors.append(str(e))
    if errors:
        return jsonify({"synced": total, "errors": errors}), 207
    return jsonify({"synced": total})


# --- Sync Filters (keyword blocklist) ---

@main_bp.route("/settings/sync-filters")
def sync_filters():
    filters = SyncFilter.query.order_by(SyncFilter.created_at.desc()).all()
    return render_template("sync_filters.html", filters=filters)


@main_bp.route("/settings/sync-filters/add", methods=["POST"])
def add_sync_filter():
    keyword = request.form.get("keyword", "").strip().lower()
    if not keyword:
        flash("Keyword cannot be empty.", "error")
        return redirect(url_for("main.sync_filters"))
    if SyncFilter.query.filter_by(keyword=keyword).first():
        flash(f'"{keyword}" is already in the blocklist.', "warning")
        return redirect(url_for("main.sync_filters"))
    db.session.add(SyncFilter(keyword=keyword))
    db.session.commit()
    flash(f'"{keyword}" added to blocklist. Future syncs will skip matching events.', "success")
    return redirect(url_for("main.sync_filters"))


@main_bp.route("/settings/sync-filters/<int:filter_id>/delete", methods=["POST"])
def delete_sync_filter(filter_id):
    f = SyncFilter.query.get(filter_id)
    if f:
        db.session.delete(f)
        db.session.commit()
        flash(f'"{f.keyword}" removed from blocklist.', "info")
    return redirect(url_for("main.sync_filters"))


@main_bp.route("/settings/sync-filters/<int:filter_id>/toggle", methods=["POST"])
def toggle_sync_filter(filter_id):
    f = SyncFilter.query.get(filter_id)
    if f:
        f.is_active = not f.is_active
        db.session.commit()
    return redirect(url_for("main.sync_filters"))


@main_bp.route("/settings/sync-filters/bulk-delete", methods=["POST"])
def bulk_delete_by_keywords():
    """Delete existing meetings whose titles match active blocked keywords."""
    keywords = [f.keyword.lower() for f in SyncFilter.query.filter_by(is_active=True).all()]
    if not keywords:
        flash("No active blocked keywords. Add keywords first.", "warning")
        return redirect(url_for("main.sync_filters"))
    total = 0
    for kw in keywords:
        q = Meeting.query.filter(Meeting.title.ilike(f"%{kw}%"))
        count = q.count()
        q.delete(synchronize_session=False)
        total += count
    db.session.commit()
    flash(f"Deleted {total} existing meeting(s) matching blocked keywords.", "success")
    return redirect(url_for("main.sync_filters"))


# ── Tasks / Reminders ──────────────────────────────────────────────────────────

@main_bp.route("/tasks")
def tasks():
    from app.models import Task
    open_tasks      = Task.query.filter_by(is_completed=False).order_by(Task.created_at.desc()).all()
    completed_tasks = Task.query.filter_by(is_completed=True).order_by(Task.completed_at.desc()).limit(30).all()
    return render_template("tasks.html", open_tasks=open_tasks, completed_tasks=completed_tasks)


@main_bp.route("/tasks/add", methods=["POST"])
def add_task():
    from app.models import Task
    from app.invite_service import InviteService
    title          = request.form.get("title", "").strip()
    assigned_to    = request.form.get("assigned_to", "").strip()
    assigned_email = request.form.get("assigned_email", "").strip()
    due_date_str   = request.form.get("due_date", "").strip()
    priority       = request.form.get("priority", "normal")
    notes          = request.form.get("notes", "").strip()
    if not title:
        flash("Reminder text is required.", "error")
        return redirect(url_for("main.tasks"))
    due_date = None
    if due_date_str:
        try:
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d")
        except ValueError:
            pass
    task = Task(title=title, assigned_to=assigned_to, assigned_email=assigned_email,
                due_date=due_date, priority=priority, notes=notes)
    db.session.add(task)
    db.session.commit()
    sent = InviteService.send_task_notification(task)
    if sent:
        flash(f"Reminder added and email sent to {assigned_email}.", "success")
    else:
        flash("Reminder added.", "success")
    return redirect(url_for("main.tasks"))


@main_bp.route("/tasks/<int:task_id>/complete", methods=["POST"])
def complete_task(task_id):
    from app.models import Task
    task = Task.query.get_or_404(task_id)
    task.is_completed = True
    task.completed_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("main.tasks"))


@main_bp.route("/tasks/<int:task_id>/reopen", methods=["POST"])
def reopen_task(task_id):
    from app.models import Task
    task = Task.query.get_or_404(task_id)
    task.is_completed = False
    task.completed_at = None
    db.session.commit()
    return redirect(url_for("main.tasks"))


@main_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    from app.models import Task
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for("main.tasks"))


# ── Team Members ───────────────────────────────────────────────────────────────

@main_bp.route("/team")
def team_members():
    from app.models import TeamMember
    members = TeamMember.query.order_by(TeamMember.name).all()
    return render_template("team_members.html", members=members)


@main_bp.route("/team/add", methods=["POST"])
def add_team_member():
    from app.models import TeamMember
    name  = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role  = request.form.get("role", "").strip()
    if not name or not email:
        flash("Name and email are required.", "error")
        return redirect(url_for("main.team_members"))
    if TeamMember.query.filter_by(email=email).first():
        flash(f"{email} is already in the team.", "warning")
        return redirect(url_for("main.team_members"))
    db.session.add(TeamMember(name=name, email=email, role=role))
    db.session.commit()
    flash(f"{name} added to team.", "success")
    return redirect(url_for("main.team_members"))


@main_bp.route("/team/<int:member_id>/delete", methods=["POST"])
def delete_team_member(member_id):
    from app.models import TeamMember
    member = TeamMember.query.get_or_404(member_id)
    db.session.delete(member)
    db.session.commit()
    flash("Team member removed.", "success")
    return redirect(url_for("main.team_members"))


@main_bp.route("/api/team-members")
def api_team_members():
    from app.models import TeamMember
    members = TeamMember.query.order_by(TeamMember.name).all()
    return jsonify([{"name": m.name, "email": m.email, "role": m.role} for m in members])
