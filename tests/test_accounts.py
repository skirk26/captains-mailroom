# Roles, the admin staff page, password rules, forced password change, and lockout.
# CSRF stays on (as in test_security.py) so every POST here needs a real token.
import re
from datetime import timedelta

import pytest
from flask import g
from flask.testing import FlaskClient

from app import create_app, db, limiter
from app import models
from app.cli import create_user, reset_db
from app.models import DUMMY_PASSWORD_HASH, MAX_FAILED_LOGINS, Staff, active_admin_count, as_utc, utc_now
from app.passwords import (
    MAX_PASSWORD_LENGTH,
    common_passwords,
    generate_temp_password,
    password_problems,
    username_problem,
)

PASSWORD = "correct horse battery staple"
GENERIC_FAILURE = "Invalid username or password."


class FreshUserClient(FlaskClient):
    """The fixture keeps one app context open for the whole test, and Flask-Login
    caches the signed-in user (and Flask-WTF the CSRF token) on that context's `g`. Clear them before each request
    so several clients in one test (an admin and a clerk) don't share a user."""

    def open(self, *args, **kwargs):
        g.pop("_login_user", None)
        g.pop("csrf_token", None)
        return super().open(*args, **kwargs)


def add_staff(username, *, admin=False, password=PASSWORD, **fields):
    staff = Staff(username=username, is_admin=admin, **fields)
    staff.set_password(password)
    db.session.add(staff)
    db.session.commit()
    return staff


def fetch(username):
    db.session.expire_all()
    return db.session.scalar(db.select(Staff).filter_by(username=username))


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-only-secret-key",
        "APP_ENV": "production",
        "RATELIMIT_ENABLED": False,  # one test below turns the sign-in limit back on
    })
    app.test_client_class = FreshUserClient
    with app.app_context():
        add_staff("boss", admin=True)
        add_staff("clerk")
        yield app
        db.session.remove()
        db.drop_all()
    limiter.enabled = True  # the limiter is shared, so undo RATELIMIT_ENABLED=False for other test files


def token_from(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "page has no CSRF token"
    return match.group(1)


def sign_in(app, username, password=PASSWORD):
    client = app.test_client()
    token = token_from(client.get("/login").get_data(as_text=True))
    response = client.post("/login", data={"username": username, "password": password, "csrf_token": token})
    return client, response


def signed_in(app, username):
    client, response = sign_in(app, username)
    assert response.status_code == 302, "test setup: sign-in should work"
    return client


def post(client, url, page="/account", **data):
    # A real form post: the token comes from a page that has a form on it
    token = token_from(client.get(page).get_data(as_text=True))
    return client.post(url, data={"csrf_token": token, **data})


def admin_post(client, url, **data):
    return post(client, url, page="/admin/staff", **data)


def alert_text(response):
    match = re.search(r'<div class="alert-body">(.*?)</div>', response.get_data(as_text=True), re.S)
    return match.group(1).strip() if match else None


# ---- Model defaults ----


def test_new_staff_has_safe_defaults(app):
    staff = add_staff("fresh")
    assert staff.is_admin is False
    assert staff.is_active is True
    assert staff.failed_login_count == 0
    assert staff.locked_until is None
    assert staff.last_login_at is None
    assert staff.must_change_password is False
    assert staff.password_changed_at is not None  # set_password records it
    assert not staff.is_locked


def test_active_admin_count_ignores_deactivated_admins(app):
    add_staff("gone", admin=True, is_active=False)
    assert active_admin_count() == 1


# ---- Admin-only access ----


ADMIN_POSTS = [
    "/admin/staff/create",
    "/admin/staff/{id}/deactivate",
    "/admin/staff/{id}/reactivate",
    "/admin/staff/{id}/unlock",
    "/admin/staff/{id}/promote",
    "/admin/staff/{id}/demote",
    "/admin/staff/{id}/reset-password",
]


def test_staff_page_needs_sign_in(app):
    response = app.test_client().get("/admin/staff")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_non_admin_gets_403_on_staff_page(app):
    client = signed_in(app, "clerk")
    response = client.get("/admin/staff")
    assert response.status_code == 403
    assert "boss" not in response.get_data(as_text=True)


@pytest.mark.parametrize("path", ADMIN_POSTS)
def test_non_admin_gets_403_on_every_admin_post(app, path):
    target = fetch("boss")
    client = signed_in(app, "clerk")
    response = post(client, path.format(id=target.id), username="sneaky")
    assert response.status_code == 403
    assert fetch("boss").is_admin and fetch("boss").is_active
    assert fetch("sneaky") is None


def test_menu_entry_only_for_admins(app):
    html = signed_in(app, "clerk").get("/account").get_data(as_text=True)
    assert "Manage staff" not in html
    assert "/admin/staff" not in html
    assert "Change password" in html

    html = signed_in(app, "boss").get("/account").get_data(as_text=True)
    assert "Manage staff" in html
    assert 'href="/admin/staff"' in html


def test_admin_sees_each_member_with_role_status_and_last_sign_in(app):
    add_staff("locked-out", locked_until=utc_now() + timedelta(minutes=5))
    add_staff("former", is_active=False)
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    for name in ("boss", "clerk", "locked-out", "former"):
        assert name in html
    assert "Admin" in html and "Deactivated" in html and "Locked" in html
    assert html.count("Locked</span>") == 1  # only the locked account has the chip
    assert "Never" in html  # clerk hasn't signed in
    assert "data-local-time" in html  # boss has (just now)


def test_staff_page_escapes_usernames(app):
    add_staff("<script>x</script>")  # not creatable through the form, but possible by CLI/DB
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;x&lt;/script&gt;" in html


def test_staff_page_has_no_inline_scripts(app):
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son\w+=", html)  # no onclick= and friends


# ---- CSRF on every admin POST ----


def test_every_admin_post_route_is_covered_and_rejects_a_missing_token(app):
    routes = [
        rule for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("admin.") and "POST" in rule.methods
    ]
    assert len(routes) == len(ADMIN_POSTS)  # a new admin POST must be added to ADMIN_POSTS too

    target = fetch("clerk")
    client = signed_in(app, "boss")
    for path in ADMIN_POSTS:
        response = client.post(path.format(id=target.id), data={"username": "sneaky", "role": "admin"})
        assert response.status_code == 400, path
    clerk = fetch("clerk")
    assert clerk.is_active and not clerk.is_admin and not clerk.must_change_password
    assert fetch("sneaky") is None


def test_admin_forms_carry_csrf_tokens(app):
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    forms = html.count("<form")
    assert forms >= 4
    assert html.count('name="csrf_token"') == forms


# ---- Admin actions ----


def test_deactivate_and_reactivate(app):
    client = signed_in(app, "boss")
    clerk = fetch("clerk")
    assert admin_post(client, f"/admin/staff/{clerk.id}/deactivate").status_code == 302
    assert fetch("clerk").is_active is False
    assert sign_in(app, "clerk")[1].status_code == 200  # refused

    admin_post(client, f"/admin/staff/{clerk.id}/reactivate")
    assert fetch("clerk").is_active is True
    assert sign_in(app, "clerk")[1].status_code == 302


def test_promote_and_demote(app):
    client = signed_in(app, "boss")
    clerk = fetch("clerk")
    admin_post(client, f"/admin/staff/{clerk.id}/promote")
    assert fetch("clerk").is_admin
    admin_post(client, f"/admin/staff/{clerk.id}/demote")
    assert not fetch("clerk").is_admin


def test_unlock(app):
    add_staff("stuck", locked_until=utc_now() + timedelta(minutes=10), failed_login_count=3)
    client = signed_in(app, "boss")
    admin_post(client, f"/admin/staff/{fetch('stuck').id}/unlock")
    stuck = fetch("stuck")
    assert not stuck.is_locked and stuck.failed_login_count == 0
    assert sign_in(app, "stuck")[1].status_code == 302


def test_unknown_staff_id_is_404(app):
    client = signed_in(app, "boss")
    assert admin_post(client, "/admin/staff/9999/deactivate").status_code == 404


# ---- Guardrails ----


def follow_message(client, response):
    assert response.status_code == 302
    return client.get(response.headers["Location"]).get_data(as_text=True)


def test_admin_cannot_demote_self(app):
    add_staff("second", admin=True)
    client = signed_in(app, "boss")
    response = admin_post(client, f"/admin/staff/{fetch('boss').id}/demote")
    assert "can&#39;t remove your own admin role" in follow_message(client, response)
    assert fetch("boss").is_admin


def test_admin_cannot_deactivate_self(app):
    add_staff("second", admin=True)
    client = signed_in(app, "boss")
    response = admin_post(client, f"/admin/staff/{fetch('boss').id}/deactivate")
    assert "can&#39;t deactivate your own account" in follow_message(client, response)
    assert fetch("boss").is_active
    assert client.get("/dashboard").status_code == 200  # still signed in


def test_admin_cannot_reset_own_password_from_the_admin_page(app):
    client = signed_in(app, "boss")
    before = fetch("boss").password_hash
    admin_post(client, f"/admin/staff/{fetch('boss').id}/reset-password")
    assert fetch("boss").password_hash == before and not fetch("boss").must_change_password


def test_self_actions_are_not_offered_in_the_page(app):
    add_staff("second", admin=True)
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    boss_id = fetch("boss").id
    for action in ("demote", "deactivate", "reset-password"):
        assert f"/admin/staff/{boss_id}/{action}" not in html


def test_last_active_admin_can_never_be_removed(app, monkeypatch):
    # Reaching this through the page isn't possible (the actor is always an
    # active admin, so there are two), so simulate the race the guard is for:
    # another request removes the second admin just before this one runs.
    add_staff("second", admin=True)
    client = signed_in(app, "boss")
    second_id = fetch("second").id
    monkeypatch.setattr("app.routes.admin.active_admin_count", lambda: 1)

    for action in ("deactivate", "demote"):
        response = admin_post(client, f"/admin/staff/{second_id}/{action}")
        assert "last active admin" in follow_message(client, response)
    second = fetch("second")
    assert second.is_admin and second.is_active


def test_deactivated_admin_does_not_count_as_an_active_admin(app):
    # boss is the only active admin: a second, deactivated admin can be demoted freely
    add_staff("retired", admin=True, is_active=False)
    client = signed_in(app, "boss")
    admin_post(client, f"/admin/staff/{fetch('retired').id}/demote")
    assert not fetch("retired").is_admin
    assert fetch("boss").is_admin and fetch("boss").is_active


def test_last_admin_has_no_remove_buttons_in_the_page(app):
    # Both are admins, so each is removable by the other...
    add_staff("second", admin=True)
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    assert f"/admin/staff/{fetch('second').id}/deactivate" in html
    # ...but with the second one deactivated, boss is the last and gets no button
    # (boss is "you", so check the page's own last_admin_id logic through demote of a deactivated one)
    fetch("second").is_active = False
    db.session.commit()
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    assert f"/admin/staff/{fetch('boss').id}/demote" not in html


# ---- Creating accounts, and temporary passwords ----


def test_create_account_shows_a_temporary_password_once(app):
    client = signed_in(app, "boss")
    response = admin_post(client, "/admin/staff/create", username="newhire", role="staff")
    assert response.status_code == 302 and response.headers["Location"] == "/admin/staff"

    first = client.get("/admin/staff")
    html = first.get_data(as_text=True)
    assert "Account created: newhire" in html
    assert first.headers["Cache-Control"] == "no-store"
    temp = re.search(r'temp-password">([^<]+)<', html).group(1)
    assert len(temp) >= 12

    again = client.get("/admin/staff").get_data(as_text=True)
    assert temp not in again
    assert "Temporary password" not in again

    newhire = fetch("newhire")
    assert newhire.must_change_password and not newhire.is_admin
    assert newhire.check_password(temp)


def test_create_admin_account(app):
    client = signed_in(app, "boss")
    admin_post(client, "/admin/staff/create", username="newboss", role="admin")
    assert fetch("newboss").is_admin


@pytest.mark.parametrize("username", ["", "ab", "has space", "semi;colon", "x" * 81])
def test_create_rejects_bad_usernames(app, username):
    client = signed_in(app, "boss")
    response = admin_post(client, "/admin/staff/create", username=username)
    assert "Usernames are 3 to 80" in follow_message(client, response)
    assert len(db.session.scalars(db.select(Staff)).all()) == 2


def test_create_rejects_duplicate_username(app):
    client = signed_in(app, "boss")
    response = admin_post(client, "/admin/staff/create", username="clerk")
    assert "already exists" in follow_message(client, response)


def test_reset_password_shows_a_temporary_password_once_and_forces_a_change(app):
    clerk = fetch("clerk")
    clerk.failed_login_count = 3
    db.session.commit()
    boss = signed_in(app, "boss")
    admin_post(boss, f"/admin/staff/{clerk.id}/reset-password")

    html = boss.get("/admin/staff").get_data(as_text=True)
    temp = re.search(r'temp-password">([^<]+)<', html).group(1)
    assert "Password reset: clerk" in html
    assert temp not in boss.get("/admin/staff").get_data(as_text=True)  # gone on the next load

    assert sign_in(app, "clerk", PASSWORD)[1].status_code == 200  # old password stopped working
    client, response = sign_in(app, "clerk", temp)
    assert response.status_code == 302
    assert fetch("clerk").must_change_password
    assert client.get("/dashboard").headers["Location"].endswith("/account/password")


def test_temporary_password_is_random_and_follows_the_rules():
    first, second = generate_temp_password("someone"), generate_temp_password("someone")
    assert first != second
    assert not password_problems(first, "someone")


# ---- Password rules ----


def test_password_rules():
    assert password_problems("short") == ["Use at least 12 characters."]
    assert password_problems("a" * 11) and not password_problems("a1b2c3d4e5f6")
    assert not password_problems("x" * 128)  # the cap is at least 128
    assert not password_problems("x" * MAX_PASSWORD_LENGTH)
    assert password_problems("x" * (MAX_PASSWORD_LENGTH + 1))


def test_common_passwords_are_refused_whatever_the_case():
    assert len(common_passwords()) >= 100 - 5
    assert any("too common" in p for p in password_problems("PASSWORD123456"))
    assert any("too common" in p for p in password_problems("Qwertyuiop12"))


def test_passwords_containing_the_username_are_refused_case_insensitively():
    assert any("username" in p for p in password_problems("my-TESTER-secret-phrase", "tester"))
    assert not password_problems("a quite different phrase", "tester")


def test_no_complexity_is_forced():
    # Lowercase words only: no capital, digit or symbol
    assert not password_problems("purple tractor window", "someone")


def test_username_rule():
    assert username_problem("front.desk_2") is None
    assert username_problem("no spaces") is not None


# ---- Change Password page ----


def change_password(client, current=PASSWORD, new="a brand new passphrase", confirm=None):
    token = token_from(client.get("/account/password").get_data(as_text=True))
    return client.post("/account/password", data={
        "csrf_token": token,
        "current_password": current,
        "new_password": new,
        "confirm_password": new if confirm is None else confirm,
    })


def test_change_password_needs_sign_in(app):
    response = app.test_client().get("/account/password")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_change_password_works(app):
    client = signed_in(app, "clerk")
    before = fetch("clerk").password_changed_at
    response = change_password(client)
    assert response.status_code == 302 and response.headers["Location"] == "/account"
    clerk = fetch("clerk")
    assert clerk.check_password("a brand new passphrase")
    assert clerk.password_changed_at >= before
    assert sign_in(app, "clerk", PASSWORD)[1].status_code == 200
    assert sign_in(app, "clerk", "a brand new passphrase")[1].status_code == 302


@pytest.mark.parametrize("kwargs, message", [
    ({"current": "wrong"}, "current password isn&#39;t correct"),
    ({"new": "tooshort"}, "at least 12 characters"),
    ({"new": "password123456"}, "too common"),
    ({"new": "my CLERK passphrase"}, "can&#39;t contain the username"),
    ({"new": "a brand new passphrase", "confirm": "something else entirely"}, "don&#39;t match"),
    ({"new": PASSWORD}, "different from the current"),
])
def test_change_password_refuses_bad_input(app, kwargs, message):
    client = signed_in(app, "clerk")
    response = change_password(client, **kwargs)
    assert response.status_code == 200
    assert message in response.get_data(as_text=True)
    assert fetch("clerk").check_password(PASSWORD)


def test_change_password_needs_a_csrf_token(app):
    client = signed_in(app, "clerk")
    response = client.post("/account/password", data={
        "current_password": PASSWORD, "new_password": "a brand new passphrase",
        "confirm_password": "a brand new passphrase",
    })
    assert response.status_code == 400
    assert fetch("clerk").check_password(PASSWORD)


def test_change_password_page_does_not_echo_passwords(app):
    client = signed_in(app, "clerk")
    html = change_password(client, new="short-pw", current="wrong-current").get_data(as_text=True)
    assert "short-pw" not in html and "wrong-current" not in html


# ---- Forced password change ----


def test_forced_change_redirects_every_page_except_sign_out(app):
    add_staff("temp", must_change_password=True)
    client = signed_in(app, "temp")
    target = "/account/password"
    for path in ("/dashboard", "/", "/account", "/checkin", "/checkout", "/search", "/admin/staff"):
        response = client.get(path)
        assert response.status_code == 302 and response.headers["Location"] == target, path

    assert client.get(target).status_code == 200
    assert client.get("/static/css/tokens.css").status_code == 200
    # Sign out stays available
    token = token_from(client.get(target).get_data(as_text=True))
    assert client.post("/logout", data={"csrf_token": token}).status_code == 302
    assert client.get("/dashboard").headers["Location"].startswith("/login")


def test_forced_change_blocks_posts_to_other_pages_too(app):
    add_staff("temp", must_change_password=True, admin=True)
    client = signed_in(app, "temp")
    token = token_from(client.get("/account/password").get_data(as_text=True))
    response = client.post("/admin/staff/create", data={"csrf_token": token, "username": "sneaky"})
    assert response.status_code == 302 and response.headers["Location"] == "/account/password"
    assert fetch("sneaky") is None


def test_forced_change_page_has_no_tab_bar_and_explains_itself(app):
    add_staff("temp", must_change_password=True)
    html = signed_in(app, "temp").get("/account/password").get_data(as_text=True)
    assert "Choose a new password to continue" in html
    assert 'class="tab-bar"' not in html


def test_changing_the_password_lifts_the_requirement(app):
    add_staff("temp", must_change_password=True)
    client = signed_in(app, "temp")
    assert change_password(client).status_code == 302
    assert fetch("temp").must_change_password is False
    assert client.get("/dashboard").status_code == 200


# ---- Deactivation ends sessions ----


def test_deactivated_users_open_session_ends_on_the_next_request(app):
    client = signed_in(app, "clerk")
    assert client.get("/dashboard").status_code == 200

    fetch("clerk").is_active = False
    db.session.commit()

    response = client.get("/dashboard")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    # The session is really gone, not just redirected once
    fetch("clerk").is_active = True
    db.session.commit()
    assert client.get("/dashboard").status_code == 302


def test_deactivated_users_post_is_refused_too(app):
    client = signed_in(app, "clerk")
    token = token_from(client.get("/account").get_data(as_text=True))
    fetch("clerk").is_active = False
    db.session.commit()
    response = client.post("/account/password", data={
        "csrf_token": token, "current_password": PASSWORD,
        "new_password": "a brand new passphrase", "confirm_password": "a brand new passphrase",
    })
    assert response.status_code == 302
    assert fetch("clerk").check_password(PASSWORD)


# ---- Lockout ----


def fail(app, times, username="clerk"):
    for _ in range(times):
        assert sign_in(app, username, "wrong password here")[1].status_code == 200


def test_five_failures_lock_the_account_for_fifteen_minutes(app):
    fail(app, MAX_FAILED_LOGINS - 1)
    assert not fetch("clerk").is_locked
    fail(app, 1)
    clerk = fetch("clerk")
    assert clerk.is_locked
    remaining = as_utc(clerk.locked_until) - utc_now()
    assert timedelta(minutes=14) < remaining <= timedelta(minutes=15)

    # Even the right password is refused while locked
    client, response = sign_in(app, "clerk", PASSWORD)
    assert response.status_code == 200 and alert_text(response) == GENERIC_FAILURE
    assert client.get("/dashboard").status_code == 302


def test_attempts_during_a_lockout_do_not_extend_it(app):
    fail(app, MAX_FAILED_LOGINS)
    locked_until = fetch("clerk").locked_until
    fail(app, 2)
    assert fetch("clerk").locked_until == locked_until


def test_lockout_expires(app):
    fail(app, MAX_FAILED_LOGINS)
    clerk = fetch("clerk")
    clerk.locked_until = utc_now() - timedelta(seconds=1)  # the 15 minutes are up
    db.session.commit()

    assert not fetch("clerk").is_locked
    assert sign_in(app, "clerk", PASSWORD)[1].status_code == 302


def test_lockout_expiry_by_clock(app, monkeypatch):
    fail(app, MAX_FAILED_LOGINS)
    later = utc_now() + timedelta(minutes=16)
    monkeypatch.setattr(models, "utc_now", lambda: later)
    assert not fetch("clerk").is_locked
    assert sign_in(app, "clerk", PASSWORD)[1].status_code == 302


def test_after_a_lockout_expires_there_are_five_fresh_attempts(app):
    fail(app, MAX_FAILED_LOGINS)
    fetch("clerk").locked_until = utc_now() - timedelta(seconds=1)
    db.session.commit()
    fail(app, MAX_FAILED_LOGINS - 1)
    assert not fetch("clerk").is_locked


def test_success_resets_the_failure_count_and_records_the_time(app):
    fail(app, MAX_FAILED_LOGINS - 1)
    assert fetch("clerk").failed_login_count == MAX_FAILED_LOGINS - 1
    assert sign_in(app, "clerk", PASSWORD)[1].status_code == 302
    clerk = fetch("clerk")
    assert clerk.failed_login_count == 0
    assert clerk.last_login_at is not None
    assert as_utc(clerk.last_login_at) > utc_now() - timedelta(minutes=1)
    fail(app, MAX_FAILED_LOGINS - 1)  # the earlier failures no longer count
    assert not fetch("clerk").is_locked


def test_failures_are_counted_per_account(app):
    fail(app, MAX_FAILED_LOGINS)
    assert fetch("clerk").is_locked and not fetch("boss").is_locked
    assert sign_in(app, "boss")[1].status_code == 302


def test_locked_chip_and_unlock_button_appear_for_admins(app):
    fail(app, MAX_FAILED_LOGINS)
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    assert f"/admin/staff/{fetch('clerk').id}/unlock" in html


# ---- Same message for every kind of failure ----


def test_all_failures_look_identical(app):
    add_staff("benched", is_active=False)
    add_staff("jailed", locked_until=utc_now() + timedelta(minutes=10))
    cases = {
        "unknown user": sign_in(app, "nobody-here", PASSWORD)[1],
        "wrong password": sign_in(app, "clerk", "not the password")[1],
        "locked, right password": sign_in(app, "jailed", PASSWORD)[1],
        "locked, wrong password": sign_in(app, "jailed", "nope nope nope")[1],
        "deactivated, right password": sign_in(app, "benched", PASSWORD)[1],
        "deactivated, wrong password": sign_in(app, "benched", "nope nope nope")[1],
    }
    bodies = set()
    for name, response in cases.items():
        assert response.status_code == 200, name
        assert alert_text(response) == GENERIC_FAILURE, name
        # Same page apart from the typed username and the per-page CSRF token
        html = response.get_data(as_text=True)
        html = re.sub(r'name="csrf_token" value="[^"]+"', "", html)
        html = re.sub(r'value="[^"]*" autocomplete="username"', "", html)
        bodies.add(html)
    assert len(bodies) == 1


def test_unknown_username_still_checks_a_password_hash(app, monkeypatch):
    hashes = []
    real = models.check_password_hash

    def spy(password_hash, password):
        hashes.append(password_hash)
        return real(password_hash, password)

    monkeypatch.setattr("app.routes.auth.check_password_hash", spy)
    sign_in(app, "nobody-here", PASSWORD)
    assert hashes == [DUMMY_PASSWORD_HASH]

    hashes.clear()
    sign_in(app, "clerk", "wrong password here")
    assert hashes == [fetch("clerk").password_hash]


def test_a_locked_or_deactivated_account_also_gets_a_real_hash_check(app, monkeypatch):
    add_staff("benched", is_active=False)
    calls = []
    monkeypatch.setattr("app.routes.auth.check_password_hash", lambda h, p: calls.append(h) or False)
    sign_in(app, "benched", PASSWORD)
    assert len(calls) == 1  # so timing doesn't tell these apart from a wrong password


def test_failed_attempts_on_a_deactivated_account_are_not_counted(app):
    add_staff("benched", is_active=False)
    fail(app, 3, "benched")
    assert fetch("benched").failed_login_count == 0


def test_deactivated_user_cannot_sign_in_even_with_the_right_password(app):
    add_staff("benched", is_active=False)
    client, response = sign_in(app, "benched", PASSWORD)
    assert response.status_code == 200
    assert client.get("/dashboard").status_code == 302
    assert fetch("benched").last_login_at is None


# ---- Works alongside the Flask-Limiter limit ----


def test_ip_rate_limit_still_applies_and_lockout_still_counts():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-only-secret-key",
        "APP_ENV": "production",
        "WTF_CSRF_ENABLED": False,
    })
    limiter.enabled = True  # an earlier test turned the shared limiter off
    with app.app_context():
        add_staff("clerk")
        client = app.test_client()
        for _ in range(5):
            assert client.post("/login", data={"username": "clerk", "password": "wrong password here"}).status_code == 200
        # The account is locked after the same five attempts that used up the IP's allowance
        assert fetch("clerk").is_locked
        response = client.post("/login", data={"username": "clerk", "password": PASSWORD})
        assert response.status_code == 429  # the limiter answers first
        db.session.remove()
        db.drop_all()
    limiter.enabled = True


# ---- Command line ----


def invoke(app, command, args, input_text=None):
    return app.test_cli_runner().invoke(command, args, input=input_text)


def test_cli_create_user(app):
    result = invoke(app, create_user, ["newhire"], f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert "Created staff user 'newhire'" in result.output
    newhire = fetch("newhire")
    assert newhire.check_password(PASSWORD) and not newhire.is_admin and not newhire.must_change_password
    assert PASSWORD not in result.output  # input is hidden, never echoed


def test_cli_create_admin(app):
    result = invoke(app, create_user, ["newboss", "--admin"], f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert "Created admin user 'newboss'" in result.output
    assert fetch("newboss").is_admin


def test_cli_asks_again_when_the_password_breaks_a_rule(app):
    result = invoke(app, create_user, ["newhire"], "short\nshort\npassword123456\npassword123456\n"
                    f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert "Use at least 12 characters." in result.output
    assert "too common" in result.output
    assert fetch("newhire").check_password(PASSWORD)


def test_cli_password_may_not_contain_the_username(app):
    bad = "Newhire is my name 1"
    result = invoke(app, create_user, ["newhire"], f"{bad}\n{bad}\n" * 3)
    assert result.exit_code != 0
    assert fetch("newhire") is None


def test_cli_gives_up_after_three_bad_passwords(app):
    result = invoke(app, create_user, ["newhire"], "short\nshort\n" * 3)
    assert result.exit_code != 0 and "nothing was created" in result.output
    assert fetch("newhire") is None


def test_cli_mismatched_confirmation_asks_again(app):
    result = invoke(app, create_user, ["newhire"], f"{PASSWORD}\nsomething else\n{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert fetch("newhire").check_password(PASSWORD)


def test_cli_refuses_duplicates_and_bad_usernames(app):
    result = invoke(app, create_user, ["clerk"], f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code != 0 and "already exists" in result.output
    result = invoke(app, create_user, ["no spaces"], f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code != 0 and "Usernames are" in result.output


def test_cli_commands_are_registered_including_the_old_name(app):
    assert {"create-user", "create-staff", "reset-db"} <= set(app.cli.commands)


def test_cli_reset_db_asks_first(app):
    result = invoke(app, reset_db, [], "n\n")
    assert result.exit_code != 0
    assert fetch("clerk") is not None  # declined: nothing erased


def test_cli_reset_db_erases_and_recreates_tables(app):
    result = invoke(app, reset_db, [], "y\n")
    assert result.exit_code == 0, result.output
    assert db.session.scalars(db.select(Staff)).all() == []
    add_staff("after-reset")  # the tables exist again
    assert fetch("after-reset") is not None


def test_cli_reset_db_yes_flag_skips_the_question(app):
    assert invoke(app, reset_db, ["--yes"]).exit_code == 0
    assert db.session.scalars(db.select(Staff)).all() == []
