import os
import time
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, flash, redirect, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
# In-memory counts: per server process, and reset when the server restarts.
# Keyed by the client's IP as the server sees it.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")

APP_ENVS = ("development", "production")
# Only acceptable in development; production refuses to start with any of these
DEV_SECRET_KEY = "dev-only-insecure-key"
PLACEHOLDER_SECRET_KEYS = {"", DEV_SECRET_KEY, "change-me-to-a-long-random-string"}

IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(hours=12)
# Session key holding when this sign-in started (seconds since the epoch)
SIGNED_IN_AT = "signed_in_at"


def debug_enabled(environ=os.environ):
    # Debug mode (auto-reload and the in-browser debugger) is off unless FLASK_DEBUG=1
    return environ.get("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    # The SQLite file lives in instance/, which is git-ignored
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(
        APP_ENV=os.environ.get("APP_ENV", "production").strip().lower(),
        SECRET_KEY=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        SQLALCHEMY_DATABASE_URI="sqlite:///"
        + os.path.join(app.instance_path, "captains_mail.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # Storage photos: in instance/ (git-ignored), outside static/, so only
        # the login-required /photos route can serve them
        UPLOAD_FOLDER=os.path.join(app.instance_path, "uploads"),
        # Largest request accepted, photo uploads included; bigger ones get a 413
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        # Cookies: no JavaScript access, and not sent on cross-site POSTs
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Remember-me isn't used yet, but its cookie gets the same flags if it ever is
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        # Flask rejects a session cookie whose signature is older than this, and
        # re-signs it on every request, so it's an inactivity timeout
        PERMANENT_SESSION_LIFETIME=IDLE_TIMEOUT,
        SESSION_REFRESH_EACH_REQUEST=True,
        # CSRF tokens last as long as the session does, so a form left open
        # doesn't fail while staff are still signed in
        WTF_CSRF_TIME_LIMIT=None,
    )
    if test_config:
        app.config.update(test_config)

    configure_environment(app)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"
    # session_protection stays at Flask-Login's "basic": "strong" would sign staff
    # out whenever their phone switches between Wi-Fi and cellular
    csrf.init_app(app)
    limiter.init_app(app)

    register_session_timeouts(app)
    register_csrf_error_handler(app)

    from app.routes.errors import register_error_pages
    from app.security import register_security_headers

    register_error_pages(app)
    register_security_headers(app)

    from app.icons import icon

    app.jinja_env.globals["icon"] = icon

    # Importing models registers the tables; create_all only adds missing ones
    from app import models  # noqa: F401

    with app.app_context():
        db.create_all()

    from app.routes.auth import bp as auth_bp
    from app.routes.checkin import checkin_bp
    from app.routes.checkout import checkout_bp
    from app.photos import photos_bp
    from app.routes.dashboard import dashboard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(checkin_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(photos_bp)

    from app.cli import create_staff

    app.cli.add_command(create_staff)

    return app


def configure_environment(app):
    app_env = app.config["APP_ENV"]
    if app_env not in APP_ENVS:
        raise RuntimeError(f"APP_ENV must be one of {', '.join(APP_ENVS)}, not {app_env!r}.")

    if app_env != "production":
        # Plain-HTTP local testing needs non-Secure cookies and no HSTS
        return

    if app.config.get("SECRET_KEY", "") in PLACEHOLDER_SECRET_KEYS:
        raise RuntimeError(
            "SECRET_KEY is missing or still a placeholder. Set a long random "
            "SECRET_KEY in .env, or set APP_ENV=development for local testing."
        )

    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = True

    @app.after_request
    def add_hsts_header(response):
        # Browsers remember for a year to only use HTTPS for this site
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response


def register_session_timeouts(app):
    @app.before_request
    def enforce_session_timeouts():
        # Permanent sessions use PERMANENT_SESSION_LIFETIME (the idle timeout)
        session.permanent = True

        if not current_user.is_authenticated:
            return None

        signed_in_at = session.get(SIGNED_IN_AT)
        if signed_in_at is None or time.time() - signed_in_at > ABSOLUTE_TIMEOUT.total_seconds():
            logout_user()
            session.clear()
            flash("Your session has expired. Please sign in again.", "warning")
            return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))
        return None


def register_csrf_error_handler(app):
    @app.errorhandler(CSRFError)
    def csrf_failed(error):
        if current_user.is_authenticated:
            from app.routes.errors import render_error

            return render_error(400)
        # Usually a form left open past the 30-minute timeout: the expired session
        # took its CSRF token with it, so send staff to sign in rather than an error
        flash("Your session has expired. Please sign in again.", "warning")
        return redirect(url_for("auth.login"))
