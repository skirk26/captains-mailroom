import pytest

from app import create_app, db
from app.models import Package, Staff

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

    response = check_out(client, student_id="00912345")
    assert response.status_code == 302
    flashed = client.get("/checkout").get_data(as_text=True)
    assert f"Package {UPS_TRACKING} marked as picked up" in flashed

    package = get_package()
    assert package.status == "checked_out"
    assert package.checked_in_by == package.checked_out_by == logged_in_staff_id()
    assert package.checked_out_at >= package.checked_in_at
    assert package.checked_out_to == "00912345"

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
