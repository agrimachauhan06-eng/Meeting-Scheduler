import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash

from app import db
from app.models import Meeting, Reminder, EmailAccount
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
        "dashboard.html", stats=stats, upcoming=upcoming, todays=todays
    )


@main_bp.route("/calendar")
def calendar_view():
    return render_template("calendar.html")


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


@main_bp.route("/meetings/<int:meeting_id>")
def meeting_detail(meeting_id):
    meeting = MeetingManager.get_meeting(meeting_id)
    if not meeting:
        flash("Meeting not found.", "error")
        return redirect(url_for("main.meetings_list"))
    return render_template("meeting_detail.html", meeting=meeting)


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

    events = []
    for m in meetings:
        color = {
            "critical": "#dc3545",
            "high": "#fd7e14",
            "normal": "#4A90D9",
            "low": "#6c757d",
        }.get(m.priority, "#4A90D9")

        if m.status == "cancelled":
            color = "#adb5bd"

        events.append({
            "id": m.id,
            "title": m.title,
            "start": m.start_time.isoformat(),
            "end": m.end_time.isoformat(),
            "color": color,
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
