import time

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import SIGNED_IN_AT, db
from app.models import Staff

bp = Blueprint("auth", __name__)


def safe_next_url():
    # Only follow same-site paths, so ?next= can't bounce users to another site
    target = request.args.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


@bp.route("/login", methods=["GET", "POST"])
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
