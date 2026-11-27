import time
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from werkzeug.security import check_password_hash

from app import SIGNED_IN_AT, db, limiter
from app.models import DUMMY_PASSWORD_HASH, Staff
from app.passwords import password_problems

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

        # Always check a hash, even for an unknown username, so the time taken
        # doesn't reveal which accounts exist
        password_ok = check_password_hash(staff.password_hash if staff else DUMMY_PASSWORD_HASH, password)
        can_sign_in = bool(staff) and staff.is_active and not staff.is_locked

        if password_ok and can_sign_in:
            staff.register_successful_login()
            db.session.commit()
            login_user(staff)
            # Starts the 12-hour limit; see enforce_session_timeouts in app/__init__.py
            session[SIGNED_IN_AT] = time.time()
            return redirect(safe_next_url())

        if can_sign_in:
            # Only live accounts count failures; the fifth locks it for 15 minutes
            staff.register_failed_login()
            db.session.commit()

        # One message for an unknown user, a wrong password, a locked account
        # and a deactivated account
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


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
# Per IP, POSTs only: this form checks the current password, so it's guessable too
@limiter.limit(LOGIN_RATE_LIMIT, methods=["POST"])
def change_password():
    errors = []
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not current_user.check_password(current):
            errors.append("Your current password isn't correct.")
        else:
            errors.extend(password_problems(new, current_user.username))
            if new == current:
                errors.append("Choose a password different from the current one.")
            if new != confirm:
                errors.append("The two new passwords don't match.")

        if not errors:
            current_user.set_password(new)
            current_user.must_change_password = False
            db.session.commit()
            flash("Password changed.", "toast")
            return redirect(url_for("dashboard.account"))

    return render_template("change_password.html", errors=errors, staff=current_user)
