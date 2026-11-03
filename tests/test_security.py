import re
import time

import pytest

from app import SIGNED_IN_AT, create_app, db, debug_enabled
from app.models import Package, Staff

PASSWORD = "test-only-password"
UPS_TRACKING = "1Z999AA10123456784"
SECRET_KEY = "test-only-secret-key"


def make_app(**config):
    # Unlike the other test files, CSRF stays on here unless a test turns it off
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": SECRET_KEY,
        "APP_ENV": "production",  # explicit, so a local .env can't change what's tested
        **config,
    })
    with app.app_context():
        staff = Staff(username="tester")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.commit()
    return app


@pytest.fixture
def app():
    app = make_app()
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def csrf_token_from(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "page has no CSRF token"
    return match.group(1)


def log_in(client):
    token = csrf_token_from(client.get("/login").get_data(as_text=True))
    return client.post("/login", data={"username": "tester", "password": PASSWORD, "csrf_token": token})


def is_logged_in(client):
    return client.get("/dashboard").status_code == 200


def set_cookie_header(response, name="session"):
    headers = [h for h in response.headers.getlist("Set-Cookie") if h.startswith(f"{name}=")]
    assert headers, f"no {name} cookie set"
    return headers[-1]


# ---- CSRF ----


def test_every_post_form_has_a_csrf_token(app):
    client = app.test_client()
    csrf_token_from(client.get("/login").get_data(as_text=True))
    log_in(client)
    csrf_token_from(client.get("/dashboard").get_data(as_text=True))  # logout form
    csrf_token_from(client.get(f"/checkin/confirm?tracking={UPS_TRACKING}").get_data(as_text=True))


def test_login_without_csrf_token_is_rejected(app):
    client = app.test_client()
    client.get("/login")
    response = client.post("/login", data={"username": "tester", "password": PASSWORD})
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert not is_logged_in(client)


def test_login_with_csrf_token_works(app):
    client = app.test_client()
    response = log_in(client)
    assert response.status_code == 302
    assert is_logged_in(client)


def test_check_in_without_csrf_token_is_rejected(app):
    client = app.test_client()
    log_in(client)
    response = client.post("/checkin/confirm", data={"tracking": UPS_TRACKING, "carrier": "UPS", "box_number": "1815"})
    assert response.status_code == 400
    assert db.session.scalars(db.select(Package)).all() == []


def test_check_in_with_csrf_token_saves(app):
    client = app.test_client()
    log_in(client)
    token = csrf_token_from(client.get(f"/checkin/confirm?tracking={UPS_TRACKING}").get_data(as_text=True))
    response = client.post("/checkin/confirm", data={
        "tracking": UPS_TRACKING, "carrier": "UPS", "box_number": "1815", "csrf_token": token,
    })
    assert response.status_code == 302
    assert len(db.session.scalars(db.select(Package)).all()) == 1


def test_check_out_without_csrf_token_is_rejected(app):
    client = app.test_client()
    log_in(client)
    token = csrf_token_from(client.get(f"/checkin/confirm?tracking={UPS_TRACKING}").get_data(as_text=True))
    client.post("/checkin/confirm", data={
        "tracking": UPS_TRACKING, "carrier": "UPS", "box_number": "1815", "csrf_token": token,
    })

    response = client.post("/checkout/confirm", data={"tracking": UPS_TRACKING})
    assert response.status_code == 400
    package = db.session.scalar(db.select(Package))
    assert package.status == "checked_in"


def test_logout_without_csrf_token_is_rejected(app):
    client = app.test_client()
    log_in(client)
    response = client.post("/logout")
    assert response.status_code == 400
    assert is_logged_in(client)


# ---- Logout ----


def test_logout_requires_post(app):
    client = app.test_client()
    log_in(client)
    assert client.get("/logout").status_code == 405
    assert is_logged_in(client)


def test_logout_by_post_signs_out(app):
    client = app.test_client()
    log_in(client)
    token = csrf_token_from(client.get("/dashboard").get_data(as_text=True))
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert not is_logged_in(client)


def test_dashboard_signs_out_with_a_post_form(app):
    client = app.test_client()
    log_in(client)
    html = client.get("/dashboard").get_data(as_text=True)
    assert '<form method="post" action="/logout"' in html
    assert 'href="/logout"' not in html


# ---- Session cookies and timeouts ----


def test_session_cookie_flags_in_production(app):
    response = app.test_client().get("/login")
    cookie = set_cookie_header(response)
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" in cookie


def test_session_cookie_is_not_secure_in_development():
    app = make_app(APP_ENV="development")
    cookie = set_cookie_header(app.test_client().get("/login"))
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" not in cookie


def test_remember_cookie_gets_the_same_flags(app):
    assert app.config["REMEMBER_COOKIE_HTTPONLY"] is True
    assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"
    assert app.config["REMEMBER_COOKIE_SECURE"] is True


def move_clock_forward(monkeypatch, minutes):
    # Flask checks the session cookie's signed timestamp against time.time()
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + minutes * 60)


def test_session_ends_after_30_idle_minutes(app, monkeypatch):
    client = app.test_client()
    log_in(client)
    move_clock_forward(monkeypatch, 31)
    assert not is_logged_in(client)


def test_each_request_restarts_the_30_minutes(app, monkeypatch):
    client = app.test_client()
    log_in(client)
    move_clock_forward(monkeypatch, 25)
    assert is_logged_in(client)  # activity at 25 minutes...
    move_clock_forward(monkeypatch, 25)
    assert is_logged_in(client)  # ...keeps it alive at 50


def test_session_ends_after_12_hours(app):
    client = app.test_client()
    log_in(client)
    with client.session_transaction() as session:
        session[SIGNED_IN_AT] = time.time() - (12 * 60 * 60 + 60)

    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
    assert not is_logged_in(client)


def test_session_within_12_hours_stays_signed_in(app):
    client = app.test_client()
    log_in(client)
    with client.session_transaction() as session:
        session[SIGNED_IN_AT] = time.time() - (11 * 60 * 60)
    assert is_logged_in(client)


def test_session_protection_is_not_strong(app):
    from app import login_manager
    assert app.config.get("SESSION_PROTECTION", login_manager.session_protection) != "strong"


# ---- Environment and SECRET_KEY ----


def test_hsts_header_in_production(app):
    response = app.test_client().get("/login")
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_no_hsts_header_in_development():
    app = make_app(APP_ENV="development")
    assert "Strict-Transport-Security" not in app.test_client().get("/login").headers


@pytest.mark.parametrize("key", ["", "dev-only-insecure-key", "change-me-to-a-long-random-string"])
def test_production_refuses_placeholder_secret_key(key):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"APP_ENV": "production", "SECRET_KEY": key, "SQLALCHEMY_DATABASE_URI": "sqlite://"})


def test_production_refuses_missing_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})


def test_app_env_defaults_to_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("SECRET_KEY", SECRET_KEY)
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})
    assert app.config["APP_ENV"] == "production"
    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_development_starts_without_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    app = create_app({"APP_ENV": "development", "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_unknown_app_env_is_refused():
    with pytest.raises(RuntimeError, match="APP_ENV"):
        create_app({"APP_ENV": "staging", "SECRET_KEY": SECRET_KEY, "SQLALCHEMY_DATABASE_URI": "sqlite://"})


@pytest.mark.parametrize("value, expected", [
    (None, False), ("0", False), ("", False), ("1", True), ("true", True), ("yes", True),
])
def test_debug_mode_comes_from_flask_debug(value, expected):
    environ = {} if value is None else {"FLASK_DEBUG": value}
    assert debug_enabled(environ) is expected


def test_run_py_does_not_hard_code_debug():
    with open("run.py", encoding="utf-8") as source:
        code = source.read()
    assert "debug=True" not in code
    assert "debug=debug_enabled()" in code
