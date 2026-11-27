# Home screen (today's numbers and recent activity) and the Account screen.
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, case, func
from sqlalchemy.orm import joinedload

from app import db
from app.models import Package, utc_now
from app.routes.checkout import CHECKED_IN_STATUS

dashboard_bp = Blueprint("dashboard", __name__)

RECENT_EVENTS = 8


def stored_utc(when):
    # Timestamps are stored as UTC without a time zone (see models.utc_now)
    return when.astimezone(timezone.utc).replace(tzinfo=None)


def today_bounds(now=None):
    """Start and end of today in APP_TIMEZONE, in the stored UTC form."""
    tz = ZoneInfo(current_app.config["APP_TIMEZONE"])
    local_now = (now or utc_now()).astimezone(tz)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Calendar arithmetic in local time, so a 23- or 25-hour DST day still ends at midnight
    next_midnight = (midnight.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=tz)
    return stored_utc(midnight), stored_utc(next_midnight)


def today_counts(now=None):
    start, end = today_bounds(now)
    # One query, three counts
    checked_in, picked_up, waiting = db.session.execute(db.select(
        func.count(case((and_(Package.checked_in_at >= start, Package.checked_in_at < end), 1))),
        func.count(case((and_(Package.checked_out_at >= start, Package.checked_out_at < end), 1))),
        func.count(case((Package.status == CHECKED_IN_STATUS, 1))),
    )).one()
    return {"checked_in": checked_in, "picked_up": picked_up, "waiting": waiting}


def relative_time(when, now):
    seconds = max(0, (now - when).total_seconds())
    if seconds < 60:
        return "Just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    return "1 day ago" if days == 1 else f"{days} days ago"


def full_time(when):
    # Shown as the <time> title, in the mailroom's time zone: "Nov 18, 2026, 1:24 PM"
    local = when.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(current_app.config["APP_TIMEZONE"]))
    hour = local.hour % 12 or 12
    return f"{local:%b} {local.day}, {local.year}, {hour}:{local:%M} {local:%p}"


def recent_activity(limit=RECENT_EVENTS):
    """The newest check-in and pickup events, newest first. A package gives
    one event for its check-in and, once collected, one for its pickup."""
    newest_checkins = db.session.scalars(
        db.select(Package).options(joinedload(Package.recipient))
        .order_by(Package.checked_in_at.desc(), Package.id.desc()).limit(limit)
    ).all()
    newest_pickups = db.session.scalars(
        db.select(Package).options(joinedload(Package.recipient))
        .where(Package.checked_out_at.is_not(None))
        .order_by(Package.checked_out_at.desc(), Package.id.desc()).limit(limit)
    ).all()

    # The newest `limit` events are among these two lists
    events = [("checked_in", package, package.checked_in_at) for package in newest_checkins]
    events += [("picked_up", package, package.checked_out_at) for package in newest_pickups]
    # A pickup sorts after its own check-in when the times are equal
    events.sort(key=lambda event: (event[2], event[0] == "picked_up", event[1].id), reverse=True)

    now = stored_utc(utc_now())
    return [
        {
            "kind": kind,
            "package": package,
            "when": when,
            "ago": relative_time(when, now),
            "title": full_time(when),
        }
        for kind, package, when in events[:limit]
    ]


# Sign-in and the error pages send staff to "/". Defined here, not in run.py,
# so it works however the app is started.
@dashboard_bp.route("/")
def index():
    return redirect(url_for("dashboard.dashboard"))


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", today=today_counts(), events=recent_activity())


@dashboard_bp.route("/account")
@login_required
def account():
    return render_template("account.html", staff=current_user)
