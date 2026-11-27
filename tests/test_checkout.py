import pytest

from app import create_app, db
from app.models import Package, Recipient, Staff

PASSWORD = "test-only-password"
UPS_TRACKING = "1Z999AA10123456784"
AMAZON_TRACKING = "TBA123456789012"


@pytest.fixture
def app():
    # In-memory database: created fresh for each test, never touches instance/
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-only-secret-key",
        "WTF_CSRF_ENABLED": False,  # tests/test_security.py checks CSRF itself
    })
    with app.app_context():
        staff = Staff(username="tester")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    client = app.test_client()
    client.post("/login", data={"username": "tester", "password": PASSWORD})
    return client


def check_in(client, tracking=UPS_TRACKING, carrier="UPS", **fields):
    data = {"tracking": tracking, "carrier": carrier, "box_number": "1815", **fields}
    return client.post("/checkin/confirm", data=data)


def check_out(client, tracking=UPS_TRACKING, **fields):
    return client.post("/checkout/confirm", data={"tracking": tracking, **fields})


def get_package(tracking=UPS_TRACKING):
    return db.session.scalar(db.select(Package).filter_by(tracking_number=tracking))


def all_packages():
    return db.session.scalars(db.select(Package)).all()


def as_new_request(app, client, method, url, **kwargs):
    # The fixture keeps one app context open, and Flask reuses it for every request,
    # including the user Flask-Login cached there. A real request always gets a fresh
    # one, so switching between clients in one test needs its own context.
    with app.app_context():
        return getattr(client, method)(url, **kwargs)


def logged_in_staff_id():
    return db.session.scalar(db.select(Staff.id).filter_by(username="tester"))


def test_valid_checkout_records_status_staff_and_time(client):
    check_in(client)
    response = check_out(client)

    assert response.status_code == 302
    assert response.headers["Location"] == "/checkout"
    package = get_package()
    assert package.status == "checked_out"
    assert package.checked_out_by == logged_in_staff_id()
    assert package.checked_out_at is not None


@pytest.mark.parametrize("typed", ["1z999aa10123456784", "1Z999 AA10 1234 5678 4", " 1z999 aa10123456784 "])
def test_lowercase_or_spaced_tracking_still_finds_package(client, typed):
    check_in(client)
    check_out(client, tracking=typed)
    assert get_package().status == "checked_out"


def test_unknown_tracking_is_rejected_and_creates_nothing(client):
    check_in(client)
    response = check_out(client, tracking=AMAZON_TRACKING)

    assert response.status_code == 302
    assert response.headers["Location"] == "/checkout"
    assert "No package found with this tracking number" in client.get("/checkout").get_data(as_text=True)
    packages = all_packages()
    assert len(packages) == 1
    assert packages[0].tracking_number == UPS_TRACKING
    assert packages[0].status == "checked_in"


def test_second_checkout_keeps_first_time_and_staff(app, client):
    check_in(client)
    check_out(client)
    first = get_package()
    first_at, first_by = first.checked_out_at, first.checked_out_by

    # A different staff member tries again
    other = Staff(username="other")
    other.set_password(PASSWORD)
    db.session.add(other)
    db.session.commit()
    other_client = app.test_client()
    as_new_request(app, other_client, "post", "/login", data={"username": "other", "password": PASSWORD})
    response = as_new_request(app, other_client, "post", "/checkout/confirm", data={"tracking": UPS_TRACKING})

    assert response.status_code == 200
    assert "No further action needed." in response.get_data(as_text=True)
    package = get_package()
    assert package.status == "checked_out"
    assert package.checked_out_at == first_at
    assert package.checked_out_by == first_by == logged_in_staff_id()


@pytest.mark.parametrize("method, url", [
    ("get", "/checkout"),
    ("get", f"/checkout/confirm?tracking={UPS_TRACKING}"),
    ("post", "/checkout/confirm"),
])
def test_logged_out_user_is_sent_to_login(app, method, url):
    response = getattr(app.test_client(), method)(url, data={"tracking": UPS_TRACKING} if method == "post" else None)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_logged_out_post_changes_nothing(app, client):
    check_in(client)
    response = as_new_request(app, app.test_client(), "post", "/checkout/confirm", data={"tracking": UPS_TRACKING})
    assert response.headers["Location"].startswith("/login")
    assert get_package().status == "checked_in"


def test_round_trip_check_in_then_check_out(client):
    response = check_in(client, first_name="Ada", last_name="Lovelace", notes="Fragile")
    assert response.status_code == 302
    package = get_package()
    assert package.status == "checked_in"
    assert package.checked_out_at is None

    lookup = client.get("/checkout/confirm", query_string={"tracking": UPS_TRACKING}).get_data(as_text=True)
    assert "Checked in" in lookup
    assert "Ada Lovelace" in lookup

    response = check_out(client, student_id="2345")
    assert response.status_code == 302
    flashed = client.get("/checkout").get_data(as_text=True)
    assert f"Picked up: …{UPS_TRACKING[-8:]}." in flashed  # toast on the scanner

    package = get_package()
    assert package.status == "checked_out"
    assert package.checked_in_by == package.checked_out_by == logged_in_staff_id()
    assert package.checked_out_at >= package.checked_in_at
    assert package.checked_out_to == "2345"

    lookup = client.get("/checkout/confirm", query_string={"tracking": UPS_TRACKING}).get_data(as_text=True)
    assert "Picked up" in lookup
    assert "No further action needed." in lookup


def test_checkout_leaves_other_packages_untouched(client):
    others = ["TBA111111111111", "TBA222222222222", "TBA333333333333"]
    check_in(client)
    for tracking in others:
        check_in(client, tracking=tracking, carrier="Amazon")

    def snapshot(tracking):
        p = get_package(tracking)
        return (p.status, p.checked_out_by, p.checked_out_at, p.checked_out_to, p.checked_in_at, p.notes)

    before = {tracking: snapshot(tracking) for tracking in others}
    check_out(client)

    assert get_package().status == "checked_out"
    assert {tracking: snapshot(tracking) for tracking in others} == before
    assert all(status == "checked_in" for status, *_ in before.values())


# ---- From a package's page into the scanner (?expect=) ----


def scan_page(client, **params):
    response = client.get("/checkout", query_string=params)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def assert_plain_scan_page(html):
    assert "Scan this package" not in html
    assert "data-expected-tracking" not in html
    assert 'id="scan-mismatch"' not in html
    assert "Check out without scanning" not in html
    assert '<div id="reader" data-confirm-url="/checkout/confirm"' in html


def test_detail_page_check_out_button_goes_to_the_scanner(client):
    check_in(client, first_name="Alex", last_name="Rivera")
    package = get_package()

    html = client.get(f"/packages/{package.id}").get_data(as_text=True)
    assert f'href="/checkout?expect={package.id}"' in html

    check_out(client)
    html = client.get(f"/packages/{package.id}").get_data(as_text=True)
    assert "/checkout?expect=" not in html


def test_expect_shows_the_banner_and_expected_tracking(client):
    check_in(client, first_name="Alex", last_name="Rivera")
    package = get_package()

    html = scan_page(client, expect=package.id)

    assert "Scan this package: ends" in html
    assert f'<span class="mono">{UPS_TRACKING[-8:]}</span>' in html
    assert "Alex Rivera · Box 1815" in html
    assert f'data-expected-tracking="{UPS_TRACKING}"' in html
    # The warning for a different label is there, hidden until scanner.js needs it
    assert '<div aria-live="assertive">' in html
    assert '<div id="scan-mismatch" hidden>' in html
    assert "Scan again" in html and "Check out the scanned package" in html


def test_banner_without_a_name_or_box(client):
    db.session.add(Package(tracking_number=AMAZON_TRACKING, carrier="Amazon", checked_in_by=logged_in_staff_id()))
    db.session.commit()

    html = scan_page(client, expect=get_package(AMAZON_TRACKING).id)

    assert "Scan this package: ends" in html
    assert "Box" not in html.split("Scan this package")[1].split("</div>")[0]


def test_expected_package_offers_only_manual_entry(client):
    check_in(client)
    html = scan_page(client, expect=get_package().id)
    assert "Can't scan? Enter manually" in html
    assert "Check out without scanning" not in html


@pytest.mark.parametrize("expect", ["abc", "", "1.5", "-1", "0", "999", "1e3", "<script>", "9" * 40, " 1 "])
def test_bad_expect_values_are_ignored(client, expect):
    check_in(client)  # package 1 exists, so only the value's form matters
    if expect == " 1 ":
        # int() accepts surrounding spaces; that's still a valid id, not an error
        assert "Scan this package" in scan_page(client, expect=expect)
        return
    assert_plain_scan_page(scan_page(client, expect=expect))


def test_picked_up_expect_is_ignored(client):
    check_in(client)
    check_out(client)
    assert_plain_scan_page(scan_page(client, expect=get_package().id))


def test_scan_page_without_expect_is_unchanged(client):
    check_in(client)
    html = scan_page(client)
    assert_plain_scan_page(html)
    assert "Can't scan? Enter manually" in html
    assert 'data-tracking-patterns="' in html and "js/scanner.js" in html


def test_banner_escapes_names(client):
    recipient = Recipient(first_name="<script>alert(1)</script>", last_name="<b>Smith</b>", mailbox_number="1815")
    db.session.add(Package(
        tracking_number=UPS_TRACKING, carrier="UPS", recipient=recipient,
        checked_in_by=logged_in_staff_id(), notes="<script>alert(2)</script>",
    ))
    db.session.commit()

    html = scan_page(client, expect=get_package().id)

    assert "<script>alert" not in html and "<b>Smith" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &lt;b&gt;Smith&lt;/b&gt; · Box 1815" in html


def test_expect_never_checks_a_package_out(client):
    check_in(client)
    package = get_package()

    scan_page(client, expect=package.id)
    client.get("/checkout/confirm", query_string={"tracking": UPS_TRACKING})
    assert client.post(f"/checkout?expect={package.id}").status_code == 405
    db.session.refresh(package)
    assert package.status == "checked_in"

    # Only the confirm page's POST does it
    check_out(client)
    db.session.refresh(package)
    assert package.status == "checked_out"
