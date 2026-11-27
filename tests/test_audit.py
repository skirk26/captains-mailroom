# The audit log: events written, the admin page, purge-audit, the 4-digit student
# ID, and trim-student-ids. CSRF is off here (tests/test_accounts.py covers it).
import io
import re
from datetime import datetime, timedelta, timezone

import pytest
from flask import g
from flask.testing import FlaskClient
from PIL import Image

from app import create_app, db, limiter
from app import audit
from app.audit import DETAILS_MAX_LENGTH, EVENT_TYPES, TARGET_MAX_LENGTH, log_event
from app.cli import create_user, purge_audit, trim_student_ids
from app.models import AuditEvent, Package, Staff, utc_now

PASSWORD = "correct horse battery staple"
UPS_TRACKING = "1Z999AA10123456784"
STUDENT_ID = "86753091"  # a full ID, as the old form allowed
RECIPIENT_FIRST, RECIPIENT_LAST, NOTE = "Zebulon", "Quimby", "fragile-note-text"


class FreshUserClient(FlaskClient):
    # See tests/test_accounts.py: the fixture's app context would share one signed-in user
    def open(self, *args, **kwargs):
        g.pop("_login_user", None)
        g.pop("csrf_token", None)
        return super().open(*args, **kwargs)


def add_staff(username, *, admin=False, **fields):
    staff = Staff(username=username, is_admin=admin, **fields)
    staff.set_password(PASSWORD)
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
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "APP_TIMEZONE": "America/New_York",
    })
    app.test_client_class = FreshUserClient
    with app.app_context():
        add_staff("boss", admin=True)
        add_staff("clerk")
        yield app
        db.session.remove()
        db.drop_all()
    limiter.enabled = True  # the limiter is shared with other test files


def sign_in(app, username, password=PASSWORD):
    client = app.test_client()
    response = client.post("/login", data={"username": username, "password": password})
    return client, response


def signed_in(app, username):
    client, response = sign_in(app, username)
    assert response.status_code == 302
    return client


def events(event_type=None):
    db.session.expire_all()
    query = db.select(AuditEvent).order_by(AuditEvent.id)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    return db.session.scalars(query).all()


def last_event(event_type):
    found = events(event_type)
    assert found, f"no {event_type} event was written"
    return found[-1]


def check_in(client, tracking=UPS_TRACKING, **fields):
    return client.post("/checkin/confirm", data={
        "tracking": tracking, "carrier": "UPS", "box_number": "1815", **fields,
    })


def jpeg_bytes():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, "JPEG")
    return out.getvalue()


# ---- Model and helper ----


def test_every_documented_event_type_exists():
    assert set(EVENT_TYPES) == {
        "login_success", "login_failure", "account_locked", "logout", "password_changed",
        "account_created", "account_deactivated", "account_reactivated", "account_unlocked",
        "role_changed", "password_reset", "package_checked_in", "package_checked_out", "photo_uploaded",
    }


def test_log_event_records_time_in_utc_and_clips_long_text(app):
    log_event("login_failure", "t" * 500, "d" * 500)
    event = last_event("login_failure")
    assert len(event.target) == TARGET_MAX_LENGTH
    assert len(event.details) == DETAILS_MAX_LENGTH
    # Stored like Package timestamps: UTC, read back without a zone
    assert abs(event.timestamp - utc_now().replace(tzinfo=None)) < timedelta(minutes=1)
    assert event.staff_id is None and event.staff_username is None and event.ip_address is None


def test_log_event_inside_a_request_records_who_and_from_where(app):
    client = signed_in(app, "clerk")
    client.get("/checkin", environ_base={"REMOTE_ADDR": "10.1.2.3"})
    log_event_via_logout = client.post("/logout", environ_base={"REMOTE_ADDR": "10.1.2.3"})
    assert log_event_via_logout.status_code == 302
    event = last_event("logout")
    assert event.staff_username == "clerk" and event.staff_id == fetch("clerk").id
    assert event.ip_address == "10.1.2.3"


def test_the_username_copy_survives_a_rename(app):
    signed_in(app, "clerk")
    clerk = fetch("clerk")
    clerk.username = "renamed"
    db.session.commit()
    assert last_event("login_success").staff_username in (None, "clerk")  # a copy, not a join
    assert last_event("login_success").target == "clerk"


def test_log_event_never_raises(app, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(audit, "AuditEvent", boom)
    log_event("logout", "x")  # must not raise
    assert events() == []


def test_log_event_survives_a_failed_commit(app):
    log_event(None, "x")  # event_type is NOT NULL, so the commit fails
    assert events() == []
    log_event("logout", "x")  # and the session still works afterwards
    assert len(events()) == 1


# ---- Events are written ----


def test_login_success_and_failure(app):
    sign_in(app, "clerk")
    assert last_event("login_success").target == "clerk"
    assert last_event("login_success").staff_username == "clerk"

    sign_in(app, "clerk", "wrong password here")
    failure = last_event("login_failure")
    assert failure.target == "clerk" and failure.details == "wrong password"
    assert failure.staff_username is None  # not signed in

    sign_in(app, "nobody")
    assert last_event("login_failure").details == "unknown user"


def test_login_failure_cuts_the_attempted_username_to_50_characters(app):
    sign_in(app, "x" * 200)
    assert last_event("login_failure").target == "x" * 50


def test_account_locked_is_logged_on_the_fifth_failure(app):
    for _ in range(4):
        sign_in(app, "clerk", "wrong password here")
    assert events("account_locked") == []
    sign_in(app, "clerk", "wrong password here")
    assert last_event("account_locked").target == "clerk"
    assert len(events("login_failure")) == 5

    sign_in(app, "clerk", PASSWORD)  # refused while locked, and noted
    assert last_event("login_failure").details == "locked"
    assert len(events("account_locked")) == 1


def test_logout(app):
    client = signed_in(app, "clerk")
    client.post("/logout")
    event = last_event("logout")
    assert event.target == "clerk" and event.staff_username == "clerk"


def test_password_changed(app):
    client = signed_in(app, "clerk")
    client.post("/account/password", data={
        "current_password": PASSWORD, "new_password": "a brand new passphrase",
        "confirm_password": "a brand new passphrase",
    })
    assert last_event("password_changed").staff_username == "clerk"

    before = len(events())
    client.post("/account/password", data={  # refused: nothing to log
        "current_password": "wrong", "new_password": "a brand new passphrase 2",
        "confirm_password": "a brand new passphrase 2",
    })
    assert len(events("password_changed")) == 1 and len(events()) == before


def test_admin_actions_are_logged_with_the_acting_admin(app):
    admin = signed_in(app, "boss")
    clerk_id = fetch("clerk").id

    admin.post("/admin/staff/create", data={"username": "newhire", "role": "admin"})
    created = last_event("account_created")
    assert created.target == "newhire" and created.staff_username == "boss"
    assert created.details == "role: admin"

    admin.post(f"/admin/staff/{clerk_id}/deactivate")
    assert last_event("account_deactivated").target == "clerk"
    admin.post(f"/admin/staff/{clerk_id}/reactivate")
    assert last_event("account_reactivated").target == "clerk"

    clerk = fetch("clerk")
    clerk.failed_login_count = 2
    db.session.commit()
    admin.post(f"/admin/staff/{clerk_id}/unlock")
    assert last_event("account_unlocked").target == "clerk"

    admin.post(f"/admin/staff/{clerk_id}/promote")
    assert last_event("role_changed").details == "now admin"
    admin.post(f"/admin/staff/{clerk_id}/demote")
    assert last_event("role_changed").details == "now staff"
    assert len(events("role_changed")) == 2

    admin.post(f"/admin/staff/{clerk_id}/reset-password")
    reset = last_event("password_reset")
    assert reset.target == "clerk" and reset.staff_username == "boss"
    assert all(e.staff_username == "boss" for e in events() if e.event_type.startswith(("account_", "role_", "password_r")))


def test_refused_admin_actions_are_not_logged(app):
    admin = signed_in(app, "boss")
    before = len(events())
    admin.post(f"/admin/staff/{fetch('boss').id}/deactivate")  # can't deactivate yourself
    admin.post(f"/admin/staff/{fetch('boss').id}/demote")
    assert len(events()) == before


def test_command_line_account_creation_is_logged(app):
    result = app.test_cli_runner().invoke(create_user, ["cliuser"], input=f"{PASSWORD}\n{PASSWORD}\n")
    assert result.exit_code == 0, result.output
    event = last_event("account_created")
    assert event.target == "cliuser" and "command line" in event.details
    assert event.staff_username is None and event.ip_address is None


def test_check_in_check_out_and_photo_are_logged(app):
    client = signed_in(app, "clerk")
    check_in(client)
    checked_in = last_event("package_checked_in")
    assert checked_in.target == UPS_TRACKING[-8:]
    assert checked_in.details == "UPS"
    assert checked_in.staff_username == "clerk"

    package = db.session.scalar(db.select(Package))
    response = client.post(
        f"/checkin/photo/{package.id}",
        data={"photo": (io.BytesIO(jpeg_bytes()), "shelf.jpg")},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert last_event("photo_uploaded").target == UPS_TRACKING[-8:]
    assert last_event("photo_uploaded").details == "new"

    client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": "4321"})
    checked_out = last_event("package_checked_out")
    assert checked_out.target == UPS_TRACKING[-8:] and checked_out.staff_username == "clerk"
    assert checked_out.details is None


def test_failed_check_ins_and_pickups_log_nothing(app):
    client = signed_in(app, "clerk")
    check_in(client)
    before = len(events("package_checked_in"))
    check_in(client)  # duplicate: refused
    assert len(events("package_checked_in")) == before

    client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": "12"})  # bad ID
    assert events("package_checked_out") == []


def test_a_logging_failure_does_not_break_check_in(app, monkeypatch):
    client = signed_in(app, "clerk")

    def boom(*args, **kwargs):
        raise RuntimeError("audit table missing")

    monkeypatch.setattr(audit, "AuditEvent", boom)
    response = check_in(client)
    assert response.status_code == 302 and "/checkin/photo/" in response.headers["Location"]
    assert db.session.scalar(db.select(db.func.count()).select_from(Package)) == 1

    response = client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": "1234"})
    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.scalar(db.select(Package)).status == "checked_out"


# ---- Nothing sensitive in the log ----


def test_the_log_never_holds_passwords_student_ids_names_or_notes(app):
    client = signed_in(app, "clerk")
    check_in(client, first_name=RECIPIENT_FIRST, last_name=RECIPIENT_LAST, notes=NOTE)
    package = db.session.scalar(db.select(Package))
    client.post(f"/checkin/photo/{package.id}", data={"photo": (io.BytesIO(jpeg_bytes()), "p.jpg")},
                headers={"Accept": "application/json"})
    client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": "4321"})
    client.post("/account/password", data={
        "current_password": PASSWORD, "new_password": "my secret new passphrase",
        "confirm_password": "my secret new passphrase",
    })
    sign_in(app, "clerk", "guess-wrong-password-1")
    admin = signed_in(app, "boss")
    admin.post(f"/admin/staff/{fetch('clerk').id}/reset-password")
    admin.post("/admin/staff/create", data={"username": "newhire"})
    temp = fetch("newhire")
    client.post("/logout")

    assert len(events()) >= 8
    dump = " | ".join(
        str(value)
        for event in events()
        for value in (event.staff_username, event.event_type, event.target, event.details, event.ip_address)
    )
    for secret in ("4321", PASSWORD, "my secret new passphrase", "guess-wrong-password-1",
                   RECIPIENT_FIRST, RECIPIENT_LAST, NOTE, temp.password_hash):
        assert secret not in dump, secret


# ---- The admin page ----


def seed_events(count, event_type="package_checked_in", start=None):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    for number in range(count):
        db.session.add(AuditEvent(
            timestamp=start + timedelta(minutes=number), event_type=event_type,
            target=f"T{number:07d}", staff_username="clerk",
        ))
    db.session.commit()


def audit_page(client, **query):
    return client.get("/admin/audit", query_string=query)


def test_audit_page_is_admin_only(app):
    assert app.test_client().get("/admin/audit").status_code == 302  # to sign in
    assert audit_page(signed_in(app, "clerk")).status_code == 403
    assert audit_page(signed_in(app, "boss")).status_code == 200


def test_audit_page_is_linked_from_the_staff_page(app):
    html = signed_in(app, "boss").get("/admin/staff").get_data(as_text=True)
    assert 'href="/admin/audit"' in html
    html = signed_in(app, "clerk").get("/account").get_data(as_text=True)
    assert "/admin/audit" not in html


def test_audit_page_lists_newest_first_with_all_columns(app):
    db.session.add(AuditEvent(timestamp=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc), event_type="logout",
                              target="older", staff_username="clerk"))
    db.session.add(AuditEvent(timestamp=datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc),
                              event_type="package_checked_in", target="newer12", details="UPS",
                              staff_username="clerk"))
    db.session.commit()
    html = audit_page(signed_in(app, "boss")).get_data(as_text=True)
    assert html.index("newer12") < html.index("older")
    for text in ("Package checked in", "Sign out", "clerk", "UPS"):
        assert text in html


def test_audit_page_shows_times_in_the_configured_time_zone(app):
    db.session.add(AuditEvent(timestamp=datetime(2026, 1, 15, 17, 30, 5, tzinfo=timezone.utc),
                              event_type="logout", target="x", staff_username="clerk"))
    db.session.add(AuditEvent(timestamp=datetime(2026, 7, 15, 17, 30, 5, tzinfo=timezone.utc),
                              event_type="logout", target="y", staff_username="clerk"))
    db.session.commit()
    html = audit_page(signed_in(app, "boss")).get_data(as_text=True)
    assert "Jan 15, 2026, 12:30:05" in html  # UTC-5 in winter
    assert "Jul 15, 2026, 13:30:05" in html  # UTC-4 in summer
    assert "America/New_York" in html


def test_audit_page_pages_by_50(app):
    seed_events(119)  # plus boss's own sign-in event makes 120
    client = signed_in(app, "boss")
    first = audit_page(client).get_data(as_text=True)
    assert first.count('class="inset-row audit-row"') == 50
    assert "Page 1 of 3" in first and "120 events" in first
    assert "T0000118" in first and "T0000070" in first and "T0000069" not in first  # boss's sign-in is newest
    assert 'href="/admin/audit?page=2"' in first and "Newer" not in first

    third = audit_page(client, page=3).get_data(as_text=True)
    assert third.count('class="inset-row audit-row"') == 20
    assert "T0000000" in third and "Older" not in third


def test_audit_page_clamps_bad_page_numbers(app):
    seed_events(60)
    client = signed_in(app, "boss")
    assert "Page 2 of 2" in audit_page(client, page=99).get_data(as_text=True)
    assert "Page 1 of 2" in audit_page(client, page="abc").get_data(as_text=True)
    assert "Page 1 of 2" in audit_page(client, page=-4).get_data(as_text=True)


def test_audit_page_filters_by_event_type_and_keeps_the_filter_while_paging(app):
    seed_events(70, "package_checked_in")
    seed_events(3, "logout")
    client = signed_in(app, "boss")  # also logs one login_success

    only_logouts = audit_page(client, type="logout").get_data(as_text=True)
    assert only_logouts.count('class="inset-row audit-row"') == 3
    assert "3 events" in only_logouts
    assert '<option value="logout" selected>' in only_logouts

    filtered = audit_page(client, type="package_checked_in").get_data(as_text=True)
    assert "70 events" in filtered and "Page 1 of 2" in filtered
    assert 'href="/admin/audit?type=package_checked_in&amp;page=2"' in filtered

    unknown = audit_page(client, type="nonsense").get_data(as_text=True)
    assert "74 events" in unknown  # an unknown type means no filter


def test_audit_page_empty_state(app):
    html = audit_page(signed_in(app, "boss"), type="role_changed").get_data(as_text=True)
    assert "No events" in html


def test_audit_page_escapes_everything(app):
    sign_in(app, '<script>alert(1)</script>"><img src=x>')
    db.session.add(AuditEvent(event_type="logout", target="<b>t</b>", details="<i>d</i>",
                              staff_username="<u>name</u>"))
    db.session.commit()
    html = audit_page(signed_in(app, "boss")).get_data(as_text=True)
    for raw in ("<script>alert(1)", "<img src=x>", "<b>t</b>", "<i>d</i>", "<u>name</u>"):
        assert raw not in html
    assert "&lt;b&gt;t&lt;/b&gt;" in html and "&lt;u&gt;name&lt;/u&gt;" in html
    assert "&lt;script&gt;alert(1)" in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)  # no inline scripts


# ---- purge-audit ----


def run(app, command, args, input_text=None):
    return app.test_cli_runner().invoke(command, args, input=input_text)


def seed_old_and_new():
    now = utc_now()
    db.session.add_all([
        AuditEvent(timestamp=now - timedelta(days=400), event_type="logout", target="old1"),
        AuditEvent(timestamp=now - timedelta(days=366), event_type="logout", target="old2"),
        AuditEvent(timestamp=now - timedelta(days=364), event_type="logout", target="recent"),
        AuditEvent(timestamp=now, event_type="logout", target="today"),
    ])
    db.session.commit()


def test_purge_audit_asks_first_and_deletes_only_old_events(app):
    seed_old_and_new()
    declined = run(app, purge_audit, [], "n\n")
    assert declined.exit_code != 0
    assert len(events()) == 4  # nothing deleted

    result = run(app, purge_audit, [], "y\n")
    assert result.exit_code == 0, result.output
    assert "Permanently delete 2 audit event(s) older than 365 days" in result.output
    assert sorted(e.target for e in events()) == ["recent", "today"]


def test_purge_audit_takes_days_and_yes(app):
    seed_old_and_new()
    result = run(app, purge_audit, ["--days", "30", "--yes"])
    assert result.exit_code == 0, result.output
    assert [e.target for e in events()] == ["today"]


def test_purge_audit_with_nothing_old_does_not_ask(app):
    seed_old_and_new()
    result = run(app, purge_audit, ["--days", "1000"])
    assert result.exit_code == 0 and "No audit events are older" in result.output
    assert len(events()) == 4


@pytest.mark.parametrize("days", ["0", "-5", "abc"])
def test_purge_audit_rejects_bad_days(app, days):
    seed_old_and_new()
    assert run(app, purge_audit, ["--days", days, "--yes"]).exit_code != 0
    assert len(events()) == 4


# ---- The 4-digit student ID ----


def waiting_package(client):
    check_in(client)
    return db.session.scalar(db.select(Package))


@pytest.mark.parametrize("student_id", ["0000", "0912", "4321"])
def test_exactly_four_digits_are_accepted_and_stored(app, student_id):
    client = signed_in(app, "clerk")
    waiting_package(client)
    response = client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": student_id})
    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.scalar(db.select(Package)).checked_out_to == student_id


@pytest.mark.parametrize("student_id", ["1", "123", "12345", STUDENT_ID, "12a4", "abcd", "12.4", "１２３４", "12 34"])
def test_anything_but_four_digits_is_refused(app, student_id):
    client = signed_in(app, "clerk")
    waiting_package(client)
    response = client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": student_id})
    assert response.status_code == 200
    assert "exactly 4 digits" in response.get_data(as_text=True)
    db.session.expire_all()
    package = db.session.scalar(db.select(Package))
    assert package.status == "checked_in" and package.checked_out_to is None


def test_blank_student_id_is_still_allowed(app):
    client = signed_in(app, "clerk")
    waiting_package(client)
    assert client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": "  "}).status_code == 302


def test_checkout_form_asks_for_the_last_four_digits(app):
    client = signed_in(app, "clerk")
    waiting_package(client)
    html = client.get("/checkout/confirm", query_string={"tracking": UPS_TRACKING}).get_data(as_text=True)
    assert "Student ID, last 4 digits (optional)" in html
    assert 'maxlength="4"' in html


def test_student_id_never_reaches_the_audit_log(app):
    client = signed_in(app, "clerk")
    waiting_package(client)
    client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": "4321"})
    client.post("/checkout/confirm", data={"tracking": UPS_TRACKING, "student_id": STUDENT_ID})  # refused
    assert "4321" not in str([(e.target, e.details) for e in events()])
    assert STUDENT_ID not in str([(e.target, e.details) for e in events()])


# ---- trim-student-ids ----


def seed_student_ids():
    staff_id = fetch("clerk").id
    values = {"A1": STUDENT_ID, "A2": "00912345", "A3": "1234", "A4": "12", "A5": None, "A6": " 55556666 "}
    for tracking, value in values.items():
        db.session.add(Package(tracking_number=tracking, carrier="UPS", status="checked_out",
                               checked_in_by=staff_id, checked_out_by=staff_id, checked_out_to=value))
    db.session.commit()


def student_ids():
    db.session.expire_all()
    return {p.tracking_number: p.checked_out_to for p in db.session.scalars(db.select(Package))}


def test_trim_student_ids_is_a_dry_run_by_default(app):
    seed_student_ids()
    before = student_ids()
    result = run(app, trim_student_ids, [])
    assert result.exit_code == 0, result.output
    assert "5 package(s) have a student ID; 3 would be shortened" in result.output
    assert "Dry run" in result.output
    assert student_ids() == before


def test_trim_student_ids_apply_shortens_to_the_last_four(app):
    seed_student_ids()
    result = run(app, trim_student_ids, ["--apply"])
    assert result.exit_code == 0, result.output
    assert "Shortened 3 student ID(s)" in result.output
    assert student_ids() == {"A1": "3091", "A2": "2345", "A3": "1234", "A4": "12", "A5": None, "A6": "6666"}


def test_trim_student_ids_is_safe_to_repeat(app):
    seed_student_ids()
    run(app, trim_student_ids, ["--apply"])
    result = run(app, trim_student_ids, ["--apply"])
    assert "0 would be shortened" in result.output and "Shortened 0" in result.output


def test_trim_student_ids_registered(app):
    assert {"purge-audit", "trim-student-ids"} <= set(app.cli.commands)
