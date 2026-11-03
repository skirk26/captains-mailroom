import time
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import SIGNED_IN_AT, db, limiter
from app.models import Staff

bp = Blueprint("auth", __name__)


def is_safe_next_url(target):
    # Only same-site paths like /checkout?x=1. Anything starting "//" (or
    # "///") is another site to a browser. Browsers also treat "\" as "/", so
    # "/\evil.com" means "//evil.com"; and they strip tabs and newlines, so
    # control characters are refused as well.
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in target):
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc


def safe_next_url():
    target = request.args.get("next", "")
    return target if is_safe_next_url(target) else "/"


LOGIN_RATE_LIMIT = "5 per minute"


@bp.route("/login", methods=["GET", "POST"])
# Per IP, POSTs only, so loading the page doesn't use up attempts
@limiter.limit(LOGIN_RATE_LIMIT, methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect("/")

    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        staff = db.session.scalar(db.select(Staff).filter_by(username=username))

        if staff and staff.check_password(password):
            login_user(staff)
            # Starts the 12-hour limit; see enforce_session_timeouts in app/__init__.py
            session[SIGNED_IN_AT] = time.time()
            return redirect(safe_next_url())

        # Same message for an unknown user and a wrong password
        flash("Invalid username or password.", "danger")

    # Keep the typed username after a failed attempt so only the password needs retyping
    return render_template("login.html", username=username)


# POST only, so another site can't sign staff out with a link or image
@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.pop(SIGNED_IN_AT, None)
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
