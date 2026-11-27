import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app import create_app, db
from app.models import Package, Recipient, Staff
from app.routes import dashboard
from app.routes.dashboard import relative_time, today_bounds, today_counts

PASSWORD = "test-only-password"
STUDENT_ID = "86753091"

# 10:00 in New York (EST, UTC-5): today there began at 05:00 UTC
NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
MIDNIGHT_NY = datetime(2026, 11, 18, 5, 0)  # stored form: naive UTC


def make_app(**config):
    return create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-only-secret-key",
        "WTF_CSRF_ENABLED": False,
        **config,
    })


@pytest.fixture
def app(tmp_path):
    app = make_app(UPLOAD_FOLDER=str(tmp_path / "uploads"))
    with app.app_context():
        staff = Staff(username="tester")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setattr(dashboard, "utc_now", lambda: NOW)
    client = app.test_client()
    client.post("/login", data={"username": "tester", "password": PASSWORD})
    return client


def staff_id():
    return db.session.scalar(db.select(Staff.id))


def add_package(tracking, checked_in_at, checked_out_at=None, first=None, last=None, box=None, **fields):
    recipient = Recipient(first_name=first, last_name=last, mailbox_number=box) if (first or last or box) else None
    package = Package(
        tracking_number=tracking, carrier=fields.pop("carrier", "UPS"), recipient=recipient,
        checked_in_by=staff_id(), checked_in_at=checked_in_at,
        status="checked_out" if checked_out_at else "checked_in",
        checked_out_at=checked_out_at, checked_out_by=staff_id() if checked_out_at else None,
        **fields,
    )
    db.session.add(package)
    db.session.commit()
    return package


def home(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def stats(page_html):
    return dict(re.findall(r"<dt>([^<]+)</dt>\s*<dd>(\d+)</dd>", page_html))


# ---- Today's counts ----


def test_counts_use_the_configured_day(client):
    second = timedelta(seconds=1)
    add_package("1Z999AA10100000001", MIDNIGHT_NY - second)                # 23:59:59 yesterday in NY
    add_package("1Z999AA10100000002", MIDNIGHT_NY)                         # 00:00 today in NY
    add_package("1Z999AA10100000003", MIDNIGHT_NY + timedelta(hours=9))    # 09:00 today
    # Checked in yesterday, picked up today: a pickup today, not a check-in today
    add_package("1Z999AA10100000004", MIDNIGHT_NY - timedelta(hours=3), MIDNIGHT_NY + timedelta(hours=1))
    # Both yesterday
    add_package("1Z999AA10100000005", MIDNIGHT_NY - timedelta(hours=5), MIDNIGHT_NY - second)
    # Both today
    add_package("1Z999AA10100000006", MIDNIGHT_NY + timedelta(hours=2), MIDNIGHT_NY + timedelta(hours=4))

    assert today_counts(NOW) == {"checked_in": 3, "picked_up": 2, "waiting": 3}
    assert stats(home(client)) == {"Checked in": "3", "Picked up": "2", "Waiting": "3"}


def test_waiting_counts_every_unpicked_package_whatever_its_day(client):
    add_package("1Z999AA10100000001", MIDNIGHT_NY - timedelta(days=30))
    add_package("1Z999AA10100000002", MIDNIGHT_NY + timedelta(hours=1))
    add_package("1Z999AA10100000003", MIDNIGHT_NY - timedelta(days=2), MIDNIGHT_NY - timedelta(days=1))

    assert today_counts(NOW) == {"checked_in": 1, "picked_up": 0, "waiting": 2}


def test_today_bounds_in_new_york(app):
    assert today_bounds(NOW) == (MIDNIGHT_NY, MIDNIGHT_NY + timedelta(days=1))
    # 03:00 UTC is still the previous evening in New York
    early = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)
    assert today_bounds(early) == (MIDNIGHT_NY - timedelta(days=1), MIDNIGHT_NY)


def test_today_bounds_on_daylight_saving_days(app):
    # Clocks go forward on 2026-03-08: that day is 23 hours long
    start, end = today_bounds(datetime(2026, 3, 8, 18, 0, tzinfo=timezone.utc))
    assert (start, end) == (datetime(2026, 3, 8, 5, 0), datetime(2026, 3, 9, 4, 0))
    # Clocks go back on 2026-11-01: that day is 25 hours long
    start, end = today_bounds(datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc))
    assert (start, end) == (datetime(2026, 11, 1, 4, 0), datetime(2026, 11, 2, 5, 0))


def test_other_time_zones(tmp_path):
    app = make_app(UPLOAD_FOLDER=str(tmp_path), APP_TIMEZONE="America/Los_Angeles")
    with app.app_context():
        # 07:00 in Los Angeles (PST, UTC-8)
        assert today_bounds(NOW) == (datetime(2026, 11, 18, 8, 0), datetime(2026, 11, 19, 8, 0))

    app = make_app(UPLOAD_FOLDER=str(tmp_path), APP_TIMEZONE="UTC")
    with app.app_context():
        assert today_bounds(NOW) == (datetime(2026, 11, 18), datetime(2026, 11, 19))


def test_unknown_time_zone_stops_startup(tmp_path):
    with pytest.raises(RuntimeError, match="APP_TIMEZONE"):
        make_app(UPLOAD_FOLDER=str(tmp_path), APP_TIMEZONE="Mars/Olympus_Mons")


def test_default_time_zone(app):
    assert app.config["APP_TIMEZONE"] == "America/New_York"


def test_counts_are_one_query(app):
    for i in range(5):
        add_package(f"1Z999AA1010000000{i}", MIDNIGHT_NY + timedelta(hours=i))
    statements = []
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        today_counts(NOW)
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)

    assert len(statements) == 1
    assert statements[0].count("count(") == 3


# ---- Recent activity ----


def activity(page_html):
    """(event word, tracking ending, link) for each activity row, in order."""
    rows = re.findall(r'<a href="(/packages/\d+)" class="inset-row activity-row">(.*?)</a>', page_html, re.S)
    result = []
    for href, body in rows:
        ending = re.search(r'<span class="mono">…(\w+)</span>', body).group(1)
        word = re.search(r"<span>(Checked in|Picked up)</span>", body).group(1)
        result.append((word, ending, href))
    return result


def test_recent_activity_is_the_newest_8_events(client):
    at = lambda hours: MIDNIGHT_NY + timedelta(hours=hours)  # noqa: E731
    a = add_package("1Z999AA10100000001", at(1), at(9))   # check-in 1:00, pickup 9:00
    b = add_package("1Z999AA10100000002", at(2))
    c = add_package("1Z999AA10100000003", at(3), at(4))
    d = add_package("1Z999AA10100000004", at(5))
    e = add_package("1Z999AA10100000005", at(6))
    f = add_package("1Z999AA10100000006", at(7))
    g = add_package("1Z999AA10100000007", at(0.5))
    add_package("1Z999AA10100000008", at(-30))             # oldest: pushed out

    rows = activity(home(client))

    expected = [
        ("Picked up", a, 9), ("Checked in", f, 7), ("Checked in", e, 6), ("Checked in", d, 5),
        ("Picked up", c, 4), ("Checked in", c, 3), ("Checked in", b, 2), ("Checked in", a, 1),
    ]
    assert rows == [(word, p.tracking_number[-8:], f"/packages/{p.id}") for word, p, _ in expected]
    assert len(rows) == 8
    assert g.tracking_number[-8:] not in [ending for _, ending, _ in rows]


def test_activity_rows_show_package_details_and_times(client):
    add_package("1Z999AA10123456784", MIDNIGHT_NY + timedelta(hours=9, minutes=48), first="Alex", last="Rivera", box="1815")
    add_package("TBA123456789012", MIDNIGHT_NY + timedelta(hours=8), box="2001", carrier="Amazon")
    add_package("1Z999AA10100000003", MIDNIGHT_NY - timedelta(days=3))

    page = home(client)

    assert "…23456784" in page and "UPS" in page and "Alex Rivera" in page
    assert "Box 2001" in page and "Amazon" in page
    assert "12 min ago" in page and "2 hr ago" in page and "3 days ago" in page
    # Full time in the mailroom's time zone as the title; UTC in datetime
    assert '<time class="footnote text-secondary-label" datetime="2026-11-18T14:48:00Z"' in page
    assert 'title="Nov 18, 2026, 9:48 AM">12 min ago</time>' in page


def test_activity_never_shows_student_ids(client):
    add_package("1Z999AA10100000001", MIDNIGHT_NY, MIDNIGHT_NY + timedelta(hours=1), first="Sam", last="Lee",
                checked_out_to=STUDENT_ID)
    page = home(client)
    assert "Picked up" in page
    assert STUDENT_ID not in page


def test_activity_escapes_names(client):
    add_package("1Z999AA10100000001", MIDNIGHT_NY, first="<script>alert(1)</script>", last="Lee")
    page = home(client)
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; Lee" in page


def test_empty_home(client):
    page = home(client)
    assert stats(page) == {"Checked in": "0", "Picked up": "0", "Waiting": "0"}
    assert "No activity yet" in page
    assert re.search(r'<a href="/checkin" class="btn btn-primary">.*?Check in a package', page, re.S)


@pytest.mark.parametrize("seconds, text", [
    (0, "Just now"), (59, "Just now"), (60, "1 min ago"), (12 * 60, "12 min ago"), (3599, "59 min ago"),
    (3600, "1 hr ago"), (23 * 3600, "23 hr ago"), (24 * 3600, "1 day ago"), (5 * 86400, "5 days ago"),
    (-30, "Just now"),  # a clock slightly ahead never shows "in the future"
])
def test_relative_time(seconds, text):
    now = datetime(2026, 11, 18, 12, 0)
    assert relative_time(now - timedelta(seconds=seconds), now) == text


# ---- Home screen layout ----


def test_home_heading_search_and_tiles(client):
    page = home(client)

    assert '<h2 class="large-title mb-1">Mailroom</h2>' in page
    assert "Signed in as tester" in page
    form = re.search(r'<form method="get" action="/search"[^>]*role="search">(.*?)</form>', page, re.S)
    assert form, "home search form"
    assert 'type="search"' in form.group(1) and 'name="q"' in form.group(1)
    assert re.search(r'<a href="/checkin" class="home-tile">.*?Check In', page, re.S)
    assert re.search(r'<a href="/checkout" class="home-tile">.*?Check Out', page, re.S)


def test_home_search_goes_to_search_page(client):
    add_package("1Z999AA10123456784", MIDNIGHT_NY, first="Alex", last="Rivera")
    response = client.get("/search", query_string={"q": "rivera"})
    assert "Alex Rivera" in response.get_data(as_text=True)


# ---- Tab bar ----


def tab_bar(page_html):
    match = re.search(r'<nav class="tab-bar" aria-label="Main">(.*?)</nav>', page_html, re.S)
    return match.group(1) if match else None


def current_tab(page_html):
    bar = tab_bar(page_html)
    return re.search(r'aria-current="page">\s*<svg.*?</svg><span>([^<]+)</span>', bar, re.S).group(1)


def test_tab_bar_items(client):
    bar = tab_bar(home(client))
    links = re.findall(r'<a href="([^"]+)" class="tab-bar-item"[^>]*>\s*<svg.*?</svg><span>([^<]+)</span>', bar, re.S)
    assert links == [("/dashboard", "Home"), ("/checkin", "Check In"), ("/checkout", "Check Out"),
                     ("/search", "Find"), ("/account", "Account")]
    assert bar.count('aria-current="page"') == 1


def test_tab_bar_shows_and_highlights_the_right_tab(client):
    package = add_package("1Z999AA10123456784", MIDNIGHT_NY)
    pages = {
        "/dashboard": "Home",
        "/search": "Find",
        "/search?q=alex&status=waiting": "Find",
        f"/packages/{package.id}": "Find",
        "/account": "Account",
    }
    for url, tab in pages.items():
        page = client.get(url).get_data(as_text=True)
        assert tab_bar(page) is not None, url
        assert current_tab(page) == tab, url
        assert '<body class="has-tab-bar">' in page, url


def test_tab_bar_is_hidden_on_focused_flows(client):
    package = add_package("1Z999AA10123456784", MIDNIGHT_NY)
    urls = [
        "/checkin", "/checkout", f"/checkout?expect={package.id}",
        "/checkin/confirm?tracking=1Z999AA10100000009",
        "/checkout/confirm?tracking=1Z999AA10123456784",
        f"/checkin/photo/{package.id}",
        "/packages/999999",  # error page
    ]
    for url in urls:
        page = client.get(url).get_data(as_text=True)
        assert 'class="tab-bar"' not in page, url
        assert "has-tab-bar" not in page, url


def test_tab_bar_is_hidden_when_signed_out(app):
    page = app.test_client().get("/login").get_data(as_text=True)
    assert 'class="tab-bar"' not in page


# ---- Account ----


def test_account_screen(client):
    page = client.get("/account").get_data(as_text=True)

    assert '<h1 class="large-title mb-4">Account</h1>' in page
    assert re.search(r'<dt class="inset-label">Name</dt>\s*<dd class="inset-value">tester</dd>', page)
    form = re.search(r'<form method="post" action="/logout">(.*?)</form>', page, re.S).group(1)
    assert 'name="csrf_token"' in form and "Sign out" in form
    assert re.search(r'<dt class="inset-label">Role</dt>\s*<dd class="inset-value">Staff</dd>', page)
    assert 'href="/account/password"' in page and "Change password" in page
    assert "Manage staff" not in page     # admins only; tests/test_accounts.py covers it


def test_account_sign_out_works(client):
    response = client.post("/logout")
    assert response.status_code == 302
    assert client.get("/account").status_code == 302


def test_account_escapes_the_name(app, monkeypatch):
    monkeypatch.setattr(dashboard, "utc_now", lambda: NOW)
    staff = Staff(username="<b>evil</b>")
    staff.set_password(PASSWORD)
    db.session.add(staff)
    db.session.commit()
    client = app.test_client()
    client.post("/login", data={"username": "<b>evil</b>", "password": PASSWORD})

    for url in ("/account", "/dashboard"):
        page = client.get(url).get_data(as_text=True)
        assert "<b>evil</b>" not in page and "&lt;b&gt;evil&lt;/b&gt;" in page, url


# ---- Login protection ----


@pytest.mark.parametrize("url", ["/dashboard", "/account"])
def test_new_screens_need_login(app, url):
    response = app.test_client().get(url)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
