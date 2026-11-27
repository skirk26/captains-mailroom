import logging
import os
import time
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from flask import Flask, flash, redirect, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, current_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import inspect as sa_inspect
from werkzeug.middleware.proxy_fix import ProxyFix

# The project's .env, found from this file's location, not the working
# directory (a WSGI host starts the app from somewhere else)
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path=ENV_FILE):
    # override=False: a variable that's already set (by the host, or the shell)
    # wins over the same name in .env
    load_dotenv(path, override=False)


load_env()

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


def secure_instance_folder(path):
    # Owner-only, where the OS has Unix-style permissions: instance/ holds the
    # database, the photos and the backups
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        logging.getLogger(__name__).warning("Couldn't restrict permissions on %s", path, exc_info=True)


def create_missing_tables():
    """Create any table that doesn't exist yet and return their names.
    Existing tables are never altered."""
    before = set(sa_inspect(db.engine).get_table_names())
    db.create_all()
    return sorted(set(sa_inspect(db.engine).get_table_names()) - before)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    # The SQLite file, photos and backups live in instance/ (git-ignored). Flask
    # works its location out from this package's own path, so it's the same
    # whatever the working directory is.
    os.makedirs(app.instance_path, exist_ok=True)
    secure_instance_folder(app.instance_path)

    app.config.from_mapping(
        APP_ENV=os.environ.get("APP_ENV", "production").strip().lower(),
        # The mailroom's time zone: where "today" starts on the home screen
        APP_TIMEZONE=os.environ.get("APP_TIMEZONE", "America/New_York").strip(),
        SECRET_KEY=os.environ.get("SECRET_KEY", DEV_SECRET_KEY),
        SQLALCHEMY_DATABASE_URI="sqlite:///"
        + os.path.join(app.instance_path, "captains_mail.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # Storage photos: in instance/ (git-ignored), outside static/, so only
        # the login-required /photos route can serve them
        UPLOAD_FOLDER=os.path.join(app.instance_path, "uploads"),
        # Largest request accepted, photo uploads included; bigger ones get a 413
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        # Behind a host that terminates HTTPS and forwards requests (set to "1" so
        # the real client address and https scheme are read from its headers)
        TRUST_PROXY=os.environ.get("TRUST_PROXY", "0").strip() == "1",
        # Cookies: only sent over HTTPS (development turns this off, see
        # configure_environment), no JavaScript access, and not sent on cross-site POSTs
        SESSION_COOKIE_SECURE=True,
        REMEMBER_COOKIE_SECURE=True,
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
    if app.config["TRUST_PROXY"]:
        # One proxy in front: use its X-Forwarded-For/-Proto/-Host. Without this
        # every request appears to come from the proxy's address, so the sign-in
        # rate limit would be shared by all staff.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    # Thumbnails sit beside the photos, so they're private and git-ignored too
    app.config.setdefault("THUMB_FOLDER", os.path.join(app.config["UPLOAD_FOLDER"], "thumbs"))
    os.makedirs(app.config["THUMB_FOLDER"], exist_ok=True)  # also makes UPLOAD_FOLDER

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"
    # session_protection stays at Flask-Login's "basic": "strong" would sign staff
    # out whenever their phone switches between Wi-Fi and cellular
    csrf.init_app(app)
    limiter.init_app(app)

    register_session_timeouts(app)
    register_account_state_checks(app)
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
        created = create_missing_tables()
    # Remembered so `flask init-db` can say what startup already created
    app.config["TABLES_CREATED_AT_STARTUP"] = created
    if created:
        app.logger.info("Created database tables: %s", ", ".join(created))

    from app.routes.admin import admin_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.checkin import checkin_bp
    from app.routes.checkout import checkout_bp
    from app.photos import photos_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.search import search_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(checkin_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(photos_bp)
    app.register_blueprint(search_bp)

    from app.cli import backup, create_user, init_db, purge_audit, reset_db, trim_student_ids

    app.cli.add_command(create_user)
    app.cli.add_command(create_user, "create-staff")  # the command's old name
    app.cli.add_command(init_db)
    app.cli.add_command(backup)
    app.cli.add_command(reset_db)
    app.cli.add_command(purge_audit)
    app.cli.add_command(trim_student_ids)

    return app


def configure_environment(app):
    app_env = app.config["APP_ENV"]
    if app_env not in APP_ENVS:
        raise RuntimeError(f"APP_ENV must be one of {', '.join(APP_ENVS)}, not {app_env!r}.")

    try:
        ZoneInfo(app.config["APP_TIMEZONE"])
    except (ZoneInfoNotFoundError, ValueError) as err:
        raise RuntimeError(
            f"APP_TIMEZONE {app.config['APP_TIMEZONE']!r} isn't a known time zone. "
            'Use a name like "America/New_York".'
        ) from err

    if app_env != "production":
        # Plain-HTTP local testing needs non-Secure cookies and no HSTS
        app.config["SESSION_COOKIE_SECURE"] = False
        app.config["REMEMBER_COOKIE_SECURE"] = False
        return

    # Production startup checks: refuse to run rather than run unsafely
    if app.config.get("SECRET_KEY", "") in PLACEHOLDER_SECRET_KEYS:
        raise RuntimeError(
            "SECRET_KEY is missing or still a placeholder. Set a long random "
            "SECRET_KEY in .env, or set APP_ENV=development for local testing."
        )
    if app.debug or debug_enabled():
        raise RuntimeError(
            "Debug mode is on (FLASK_DEBUG), and the in-browser debugger can run "
            "code. Turn it off for production, or set APP_ENV=development."
        )
    if not (app.config["SESSION_COOKIE_SECURE"] and app.config["REMEMBER_COOKIE_SECURE"]):
        raise RuntimeError("Secure cookies must be on in production (SESSION_COOKIE_SECURE, REMEMBER_COOKIE_SECURE).")

    @app.after_request
    def add_hsts_header(response):
        # Browsers remember for a year to only use HTTPS for this site
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    # INFO isn't shown by default; the line below is how an admin confirms the checks ran
    if app.logger.level == logging.NOTSET:
        app.logger.setLevel(logging.INFO)
    # Never the key itself
    app.logger.info(
        "Production startup checks passed: APP_ENV=production, SECRET_KEY set (not shown), "
        "debug off, secure cookies on, HSTS on, proxy headers %s.",
        "trusted (TRUST_PROXY=1)" if app.config["TRUST_PROXY"] else "not trusted (TRUST_PROXY off)",
    )


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


# Pages a signed-in user may still open while they must pick a new password
PASSWORD_CHANGE_ENDPOINTS = {"auth.change_password", "auth.logout", "static"}


def register_account_state_checks(app):
    @app.before_request
    def enforce_account_state():
        from app.models import Staff

        # Flask-Login treats an inactive user as not authenticated, so look at the
        # loaded user itself. Checked on every request, so deactivating someone
        # ends the session they already have open, not just future sign-ins.
        user = current_user._get_current_object()
        if isinstance(user, Staff) and not user.is_active:
            logout_user()
            session.clear()
            flash("Your session has ended. Please sign in again.", "warning")
            return redirect(url_for("auth.login"))

        if (
            current_user.is_authenticated
            and current_user.must_change_password
            and request.endpoint not in PASSWORD_CHANGE_ENDPOINTS
        ):
            return redirect(url_for("auth.change_password"))
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
