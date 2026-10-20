import pytest

from app import create_app, db
from app.models import Package, Recipient, Staff
from app.routes import checkin
from app.routes.checkin import guess_carrier, validate_checkin_form

PASSWORD = "test-only-password"
UPS_TRACKING = "1Z999AA10123456784"


@pytest.fixture
def app():
    # In-memory database: created fresh for each test, never touches instance/
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
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


def post_checkin(client, **fields):
    data = {"tracking": UPS_TRACKING, "carrier": "UPS", **fields}
    return client.post("/checkin/confirm", data=data)


def all_packages():
    return db.session.scalars(db.select(Package)).all()


def all_recipients():
    return db.session.scalars(db.select(Recipient)).all()


# ---- Saving ----


def test_valid_save(client):
    response = post_checkin(client, first_name=" Ada ", last_name="Lovelace", box_number="1815")

    assert response.status_code == 302
    assert response.headers["Location"] == "/checkin"

    [package] = all_packages()
    assert package.tracking_number == UPS_TRACKING
    assert package.carrier == "UPS"
    assert package.status == "checked_in"
    assert package.checked_in_staff.username == "tester"
    assert package.notes is None
    assert package.recipient.first_name == "Ada"  # trimmed
    assert package.recipient.last_name == "Lovelace"
    assert package.recipient.mailbox_number == "1815"


def test_success_message_shows_on_scanner_page(client):
    response = client.post(
        "/checkin/confirm",
        data={"tracking": UPS_TRACKING, "carrier": "UPS", "box_number": "1815"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert response.request.path == "/checkin"
    assert "alert-success" in html
    assert f"Package {UPS_TRACKING} checked in" in html


def test_notes_only_saves_without_recipient(client):
    response = post_checkin(client, notes="Left at front desk")

    assert response.status_code == 302
    [package] = all_packages()
    assert package.notes == "Left at front desk"
    assert package.recipient is None
    assert all_recipients() == []


def test_nothing_filled_in_is_rejected(client):
    response = post_checkin(client)

    assert response.status_code == 200
    assert "Fill in at least a name, a box number, or a note." in response.get_data(as_text=True)
    assert all_packages() == []


def test_whitespace_only_fields_count_as_blank(client):
    response = post_checkin(client, first_name="   ", box_number="  ", notes="\n ")

    assert response.status_code == 200
    assert all_packages() == []


@pytest.mark.parametrize("box", ["999", "5200", "0999", "12a4", "10000", "-1000", "1 000"])
def test_bad_box_number_is_rejected(client, box):
    response = post_checkin(client, box_number=box)

    assert response.status_code == 200
    assert "Box number must be a number from 1000 to 5199." in response.get_data(as_text=True)
    assert all_packages() == []


@pytest.mark.parametrize("box", ["1000", "5199", " 2048 "])
def test_good_box_number_is_accepted(client, box):
    response = post_checkin(client, box_number=box)

    assert response.status_code == 302
    assert all_packages()[0].recipient.mailbox_number == box.strip()


def test_duplicate_tracking_number_is_rejected(client):
    post_checkin(client, box_number="1815")
    response = post_checkin(client, box_number="2048")

    assert response.status_code == 200
    assert "This package has already been checked in." in response.get_data(as_text=True)
    assert len(all_packages()) == 1


def test_scanning_a_checked_in_package_warns_immediately(client):
    post_checkin(client, box_number="1815")

    html = client.get("/checkin/confirm?tracking=1z999aa10123456784").get_data(as_text=True)
    assert "alert-danger" in html
    assert "This package has already been checked in." in html


def test_scanning_a_new_package_shows_no_warning(client):
    html = client.get(f"/checkin/confirm?tracking={UPS_TRACKING}").get_data(as_text=True)
    assert "alert-danger" not in html


def test_duplicate_check_ignores_spaces_and_case(client):
    post_checkin(client, tracking="9400 1000 0000 0000 0000 00", carrier="USPS", box_number="1815")
    response = post_checkin(client, tracking="9400100000000000000000", carrier="USPS", box_number="1815")

    assert "This package has already been checked in." in response.get_data(as_text=True)
    assert all_packages()[0].tracking_number == "9400100000000000000000"


def test_double_submit_race_shows_duplicate_message(client, monkeypatch):
    # Simulate two Save taps that both pass the duplicate check before either commits
    post_checkin(client, box_number="1815")
    monkeypatch.setattr(checkin, "tracking_exists", lambda tracking: False)

    response = post_checkin(client, box_number="1815")

    assert response.status_code == 200
    assert "This package has already been checked in." in response.get_data(as_text=True)
    assert len(all_packages()) == 1
    # The rolled-back session still works for the next package
    assert post_checkin(client, tracking="TBA123456789012", carrier="Amazon", notes="ok").status_code == 302


def test_failed_save_keeps_what_was_typed(client):
    response = post_checkin(
        client,
        tracking="1z999aa10123456784",
        first_name="Ada",
        last_name="Lovelace",
        box_number="999",
        notes="Box is wet",
        source="manual",
    )
    html = response.get_data(as_text=True)

    assert 'value="1z999aa10123456784"' in html
    assert 'value="Ada"' in html
    assert 'value="Lovelace"' in html
    assert 'value="999"' in html
    assert ">Box is wet</textarea>" in html
    assert '<option value="UPS" selected>' in html
    assert "Entered manually" in html  # chip survives the round trip
    assert "alert-danger" in html


def test_recipient_is_reused_case_insensitively(client):
    post_checkin(client, first_name="Ada", last_name="Lovelace", box_number="1815")
    post_checkin(client, tracking="TBA123456789012", carrier="Amazon",
                 first_name="ADA", last_name="lovelace", box_number="1815")

    [recipient] = all_recipients()
    assert len(recipient.packages) == 2


def test_blank_fields_must_match_for_reuse(client):
    post_checkin(client, first_name="Ada", box_number="1815")
    post_checkin(client, tracking="TBA123456789012", carrier="Amazon",
                 first_name="Ada", last_name="Lovelace", box_number="1815")

    assert len(all_recipients()) == 2


def test_logged_out_user_is_sent_to_login(app):
    client = app.test_client()

    for response in (client.get(f"/checkin/confirm?tracking={UPS_TRACKING}"), post_checkin(client, box_number="1815")):
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login")
    assert all_packages() == []


# ---- validate_checkin_form on its own ----


def test_validate_cleans_values():
    cleaned, errors = validate_checkin_form(
        {"tracking": " 1z999 aa10123456784 ", "carrier": " UPS ", "first_name": " Ada ",
         "last_name": "", "box_number": " 1815 ", "notes": "  "}
    )
    assert errors == []
    assert cleaned == {
        "tracking": UPS_TRACKING,
        "carrier": "UPS",
        "first_name": "Ada",
        "last_name": None,
        "box_number": "1815",
        "notes": None,
    }


def test_validate_reports_every_problem_at_once():
    _, errors = validate_checkin_form({"tracking": "", "carrier": "Pigeon", "box_number": "5200"})
    assert errors == [
        "Enter the tracking number.",
        "Choose a carrier from the list.",
        "Box number must be a number from 1000 to 5199.",
    ]


@pytest.mark.parametrize(
    "field, value, ok",
    [
        ("tracking", "A" * 100, True),
        ("tracking", "A" * 101, False),
        ("first_name", "x" * 50, True),
        ("first_name", "x" * 51, False),
        ("last_name", "x" * 51, False),
        ("notes", "x" * 500, True),
        ("notes", "x" * 501, False),
        ("notes", "line\r\n" * 99 + "x" * 5, True),  # 599 sent, 500 once \r\n counts as one
        ("carrier", "", False),
        ("box_number", "١٨١٥", False),  # Arabic-Indic digits: isdigit() says yes, we say no
    ],
)
def test_validate_limits(field, value, ok):
    form = {"tracking": UPS_TRACKING, "carrier": "UPS", "box_number": "1815", field: value}
    _, errors = validate_checkin_form(form)
    assert (errors == []) == ok, errors


# ---- Confirm page (GET) and carrier guessing ----


@pytest.mark.parametrize(
    "tracking, carrier",
    [
        ("1Z999AA10123456784", "UPS"),
        ("1z999aa10123456784", "UPS"),
        ("TBA123456789012", "Amazon"),
        ("9400 1000 0000 0000 0000 00", "USPS"),
        ("EC123456789US", "USPS"),
        ("123456789012", "FedEx"),
        ("9612345678901234567890", "FedEx"),
        ("1234567890", "DHL"),
        ("HELLO", None),
    ],
)
def test_guess_carrier(tracking, carrier):
    assert guess_carrier(tracking) == carrier


def test_confirm_preselects_guessed_carrier(client):
    html = client.get(f"/checkin/confirm?tracking={UPS_TRACKING}").get_data(as_text=True)
    assert '<option value="UPS" selected>' in html


def test_confirm_leaves_carrier_blank_when_unknown(client):
    html = client.get("/checkin/confirm?tracking=HELLO").get_data(as_text=True)
    assert '<option value="" selected>' in html
    assert '<option value="UPS" >' in html


def test_confirm_requires_tracking_and_carrier_only(client):
    html = client.get("/checkin/confirm?tracking=ABC").get_data(as_text=True)
    for name in ("first_name", "last_name", "box_number"):
        tag = html[html.index(f'name="{name}"') :]
        assert "required" not in tag[: tag.index(">")]
    assert html.count(" required>") == 2  # tracking input and carrier select


def test_confirm_redirects_without_tracking(client):
    response = client.get("/checkin/confirm?tracking=%20")
    assert response.status_code == 302
    assert response.headers["Location"] == "/checkin"
