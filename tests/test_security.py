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


# ---- Security headers ----


EXPECTED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(self)",
}


@pytest.mark.parametrize("url", ["/login", "/static/css/style.css", "/no-such-page"])
def test_security_headers_on_every_response(app, url):
    response = app.test_client().get(url)
    for name, value in EXPECTED_HEADERS.items():
        assert response.headers.get(name) == value, name
    assert "Content-Security-Policy" in response.headers


def test_security_headers_on_signed_in_pages(app):
    client = app.test_client()
    log_in(client)
    for url in ("/dashboard", "/checkin", "/checkout", f"/checkout/confirm?tracking={UPS_TRACKING}"):
        headers = client.get(url).headers
        assert headers["X-Frame-Options"] == "DENY", url
        assert "Content-Security-Policy" in headers, url


def csp_directives(app):
    policy = app.test_client().get("/login").headers["Content-Security-Policy"]
    return {part.split()[0]: part.split()[1:] for part in policy.split("; ")}


def test_csp_allows_only_this_sites_scripts(app):
    directives = csp_directives(app)
    assert directives["default-src"] == ["'self'"]
    assert directives["script-src"] == ["'self'"]  # no inline scripts, no eval
    assert directives["style-src"] == ["'self'", "'unsafe-inline'"]
    assert directives["img-src"] == ["'self'", "data:", "blob:"]
    assert directives["object-src"] == ["'none'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert directives["form-action"] == ["'self'"]


def test_templates_load_nothing_from_other_sites():
    import glob

    for path in glob.glob("app/templates/*.html"):
        with open(path, encoding="utf-8") as template:
            html = template.read()
        assert "https://" not in html, path
        assert "<script>" not in html and "<script type" not in html, path  # no inline scripts
        assert re.search(r"\son[a-z]+=", html) is None, path  # no inline event handlers


def test_vendored_libraries_are_served_locally(app):
    client = app.test_client()
    for path in ("vendor/bootstrap-5.3.3/bootstrap.min.css", "vendor/html5-qrcode-2.3.8/html5-qrcode.min.js"):
        response = client.get(f"/static/{path}")
        assert response.status_code == 200, path
        response.close()
    client = app.test_client()
    log_in(client)
    html = client.get("/checkin").get_data(as_text=True)
    assert 'src="/static/vendor/html5-qrcode-2.3.8/html5-qrcode.min.js"' in html
    assert 'href="/static/vendor/bootstrap-5.3.3/bootstrap.min.css"' in html


# ---- Login hardening ----


def post_login(client, password="wrong-password", username="tester"):
    return client.post("/login", data={"username": username, "password": password})


def test_login_is_limited_to_5_attempts_per_minute():
    app = make_app(WTF_CSRF_ENABLED=False)
    client = app.test_client()
    for _ in range(5):
        assert post_login(client).status_code == 200  # wrong password, form shown again

    response = post_login(client, password=PASSWORD)  # even the right password
    assert response.status_code == 429
    assert "Too many attempts" in response.get_data(as_text=True)
    assert not is_logged_in(client)


def test_login_page_views_dont_count_towards_the_limit():
    app = make_app(WTF_CSRF_ENABLED=False)
    client = app.test_client()
    for _ in range(10):
        assert client.get("/login").status_code == 200
    assert post_login(client, password=PASSWORD).status_code == 302


def test_login_limit_resets_after_a_minute(monkeypatch):
    app = make_app(WTF_CSRF_ENABLED=False)
    client = app.test_client()
    for _ in range(6):
        post_login(client)
    move_clock_forward(monkeypatch, 1.1)
    assert post_login(client, password=PASSWORD).status_code == 302


def test_login_limit_is_per_ip():
    app = make_app(WTF_CSRF_ENABLED=False)
    client = app.test_client()
    for _ in range(6):
        post_login(client)
    other = app.test_client()
    response = other.post("/login", data={"username": "tester", "password": PASSWORD},
                          environ_base={"REMOTE_ADDR": "10.0.0.2"})
    assert response.status_code == 302


def test_unknown_user_and_wrong_password_get_the_same_message():
    app = make_app(WTF_CSRF_ENABLED=False)
    wrong_password = post_login(app.test_client()).get_data(as_text=True)
    unknown_user = post_login(app.test_client(), username="nobody").get_data(as_text=True)
    assert "Invalid username or password." in wrong_password
    assert "Invalid username or password." in unknown_user
    # Identical apart from the username typed back into the form and the
    # per-session CSRF token
    def without_variable_parts(html, username):
        html = html.replace(f'value="{username}"', "")
        return re.sub(r'name="csrf_token" value="[^"]+"', "", html)

    assert without_variable_parts(wrong_password, "tester") == without_variable_parts(unknown_user, "nobody")


@pytest.mark.parametrize("target", ["/checkout", "/checkin/confirm?tracking=TBA123456789012&source=manual", "/"])
def test_login_follows_next_to_this_site(target):
    app = make_app(WTF_CSRF_ENABLED=False)
    response = app.test_client().post("/login", query_string={"next": target},
                                      data={"username": "tester", "password": PASSWORD})
    assert response.headers["Location"] == target


@pytest.mark.parametrize("target", [
    "https://evil.example",
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "/\\\\evil.example",
    "\\\\evil.example",
    "https:/evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "javascript:alert(1)",
    "evil.example",
    "",
])
def test_login_never_redirects_off_site(target):
    app = make_app(WTF_CSRF_ENABLED=False)
    response = app.test_client().post("/login", query_string={"next": target},
                                      data={"username": "tester", "password": PASSWORD})
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


# ---- Error pages ----


def assert_friendly_error(response, code):
    html = response.get_data(as_text=True)
    assert response.status_code == code
    assert "Captain&#39;s Mail" in html or "Captain's Mail" in html
    assert "Back to Captain" in html
    for leak in ("Traceback", 'File "', ".py", "Werkzeug", "werkzeug"):
        assert leak not in html, leak


def test_404_page(app):
    assert_friendly_error(app.test_client().get("/no-such-page"), 404)


def test_405_page(app):
    client = app.test_client()
    log_in(client)
    assert_friendly_error(client.get("/logout"), 405)


def test_400_page_for_a_missing_csrf_token(app):
    client = app.test_client()
    log_in(client)
    assert_friendly_error(client.post("/checkout/confirm", data={"tracking": UPS_TRACKING}), 400)


def test_429_page():
    app = make_app(WTF_CSRF_ENABLED=False)
    client = app.test_client()
    for _ in range(5):
        post_login(client)
    assert_friendly_error(post_login(client), 429)


def test_403_page(app):
    from flask import abort

    app.add_url_rule("/forbidden-for-test", "forbidden_for_test", lambda: abort(403))
    assert_friendly_error(app.test_client().get("/forbidden-for-test"), 403)


def test_500_page_hides_the_error():
    app = make_app()
    app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

    def broken():
        raise RuntimeError(r"secret detail in C:\app\routes\checkin.py")

    app.add_url_rule("/broken-for-test", "broken_for_test", broken)
    response = app.test_client().get("/broken-for-test")
    assert_friendly_error(response, 500)
    assert "secret detail" not in response.get_data(as_text=True)
    assert response.headers["X-Frame-Options"] == "DENY"  # headers on errors too
