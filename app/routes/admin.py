# Admin-only staff management: /admin/staff and the POST actions beside it.
import math
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_required

from app import db
from app.audit import EVENT_TYPES, log_event
from app.models import AuditEvent, Staff, active_admin_count, as_utc
from app.passwords import generate_temp_password, username_problem

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# Session key holding a temporary password between the POST and the page that shows it
TEMP_PASSWORD_KEY = "temp_password_notice"


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def find_staff(staff_id):
    return db.session.get(Staff, staff_id) or abort(404)


def refuse(message):
    flash(message, "danger")
    return redirect(url_for("admin.staff_list"))


def done(message):
    flash(message, "toast")
    return redirect(url_for("admin.staff_list"))


def is_last_active_admin(staff):
    return staff.is_admin and staff.is_active and active_admin_count() <= 1


def show_temp_password(staff, temp_password, heading):
    # Kept in the session just long enough for the next page to show it once
    session[TEMP_PASSWORD_KEY] = {"username": staff.username, "password": temp_password, "heading": heading}


@admin_bp.route("/staff")
@admin_required
def staff_list():
    members = db.session.scalars(db.select(Staff).order_by(db.func.lower(Staff.username))).all()
    notice = session.pop(TEMP_PASSWORD_KEY, None)  # popped, so a reload won't show it again
    last_admin_id = next((m.id for m in members if is_last_active_admin(m)), None)
    response = make_response(render_template("admin_staff.html", members=members, notice=notice, last_admin_id=last_admin_id))
    response.headers["Cache-Control"] = "no-store"  # a temporary password must not be cached
    return response


@admin_bp.route("/staff/create", methods=["POST"])
@admin_required
def create_staff():
    username = request.form.get("username", "").strip()
    make_admin = request.form.get("role") == "admin"

    problem = username_problem(username)
    if problem:
        return refuse(problem)
    if db.session.scalar(db.select(Staff).filter_by(username=username)):
        return refuse(f"A staff account named '{username}' already exists.")

    temp_password = generate_temp_password(username)
    staff = Staff(username=username, is_admin=make_admin, must_change_password=True)
    staff.set_password(temp_password)
    db.session.add(staff)
    db.session.commit()
    log_event("account_created", staff.username, f"role: {staff.role.lower()}")
    show_temp_password(staff, temp_password, "Account created")
    return redirect(url_for("admin.staff_list"))


@admin_bp.route("/staff/<int:staff_id>/deactivate", methods=["POST"])
@admin_required
def deactivate(staff_id):
    staff = find_staff(staff_id)
    if staff.id == current_user.id:
        return refuse("You can't deactivate your own account.")
    if is_last_active_admin(staff):
        return refuse("This is the last active admin, so it can't be deactivated.")
    staff.is_active = False
    db.session.commit()
    log_event("account_deactivated", staff.username)
    return done(f"Deactivated {staff.username}.")


@admin_bp.route("/staff/<int:staff_id>/reactivate", methods=["POST"])
@admin_required
def reactivate(staff_id):
    staff = find_staff(staff_id)
    staff.is_active = True
    db.session.commit()
    log_event("account_reactivated", staff.username)
    return done(f"Reactivated {staff.username}.")


@admin_bp.route("/staff/<int:staff_id>/unlock", methods=["POST"])
@admin_required
def unlock(staff_id):
    staff = find_staff(staff_id)
    staff.unlock()
    db.session.commit()
    log_event("account_unlocked", staff.username)
    return done(f"Unlocked {staff.username}.")


@admin_bp.route("/staff/<int:staff_id>/promote", methods=["POST"])
@admin_required
def promote(staff_id):
    staff = find_staff(staff_id)
    staff.is_admin = True
    db.session.commit()
    log_event("role_changed", staff.username, "now admin")
    return done(f"{staff.username} is now an admin.")


@admin_bp.route("/staff/<int:staff_id>/demote", methods=["POST"])
@admin_required
def demote(staff_id):
    staff = find_staff(staff_id)
    if staff.id == current_user.id:
        return refuse("You can't remove your own admin role.")
    if is_last_active_admin(staff):
        return refuse("This is the last active admin, so it can't be demoted.")
    staff.is_admin = False
    db.session.commit()
    log_event("role_changed", staff.username, "now staff")
    return done(f"{staff.username} is no longer an admin.")


@admin_bp.route("/staff/<int:staff_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(staff_id):
    staff = find_staff(staff_id)
    if staff.id == current_user.id:
        return refuse("To change your own password, use Change Password on the Account screen.")
    temp_password = generate_temp_password(staff.username)
    staff.set_password(temp_password)
    staff.must_change_password = True
    staff.unlock()  # a reset is usually the fix for someone locked out
    db.session.commit()
    log_event("password_reset", staff.username)
    show_temp_password(staff, temp_password, "Password reset")
    return redirect(url_for("admin.staff_list"))


AUDIT_PER_PAGE = 50


@admin_bp.route("/audit")
@admin_required
def audit_log():
    # ?type= filters to one event type; anything unknown shows every event
    event_type = request.args.get("type", "")
    if event_type not in EVENT_TYPES:
        event_type = ""
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1

    query = db.select(AuditEvent)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    total = db.session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    page_count = max(1, math.ceil(total / AUDIT_PER_PAGE))
    page = min(page, page_count)
    events = db.session.scalars(
        query.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
        .offset((page - 1) * AUDIT_PER_PAGE)
        .limit(AUDIT_PER_PAGE)
    ).all()

    # Stored in UTC; shown in the mailroom's own time zone
    zone = ZoneInfo(current_app.config["APP_TIMEZONE"])
    rows = [
        {
            "when": as_utc(event.timestamp).astimezone(zone).strftime("%b %d, %Y, %H:%M:%S"),
            "staff": event.staff_username,
            "label": EVENT_TYPES.get(event.event_type, event.event_type),
            "target": event.target,
            "details": event.details,
        }
        for event in events
    ]

    def page_url(number):
        return url_for("admin.audit_log", **{"type": event_type or None, "page": number if number > 1 else None})

    return render_template(
        "admin_audit.html",
        rows=rows,
        event_types=EVENT_TYPES,
        selected=event_type,
        total=total,
        page=page,
        page_count=page_count,
        previous_url=page_url(page - 1) if page > 1 else None,
        next_url=page_url(page + 1) if page < page_count else None,
        timezone_name=current_app.config["APP_TIMEZONE"],
    )
