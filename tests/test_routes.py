import pytest

from app import create_app, db
from app.models import Package, Recipient, Staff
from app.routes import checkin, checkout
from app.routes.checkin import guess_carrier, validate_checkin_form

PASSWORD = "test-only-password"
UPS_TRACKING = "1Z999AA10123456784"


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


# ---- Recipient autofill search ----


def add_recipients(*rows):
    for first, last, box in rows:
        db.session.add(Recipient(first_name=first, last_name=last, mailbox_number=box))
    db.session.commit()


def search(client, q):
    response = client.get("/api/recipients/search", query_string={"q": q})
    assert response.status_code == 200
    return response.get_json()


def test_search_needs_two_characters(client):
    add_recipients(("Alex", "Rivera", "1234"))
    assert search(client, "") == []
    assert search(client, "r") == []
    assert search(client, " r ") == []


def test_search_returns_name_and_box_fields(client):
    add_recipients(("Alex", "Rivera", "1234"))
    assert search(client, "riv") == [{"first_name": "Alex", "last_name": "Rivera", "mailbox_number": "1234"}]


@pytest.mark.parametrize("q", ["RIV", "rivera", "al", "ALEX", "12", "1234"])
def test_search_matches_start_of_last_first_or_box(client, q):
    add_recipients(("Alex", "Rivera", "1234"), ("Sam", "Lee", "5000"))
    assert [r["last_name"] for r in search(client, q)] == ["Rivera"]


@pytest.mark.parametrize("q", ["ver", "lex", "34", "Rivera Alex"])
def test_search_does_not_match_the_middle(client, q):
    add_recipients(("Alex", "Rivera", "1234"))
    assert search(client, q) == []


def test_search_treats_wildcards_literally(client):
    add_recipients(("Alex", "Rivera", "1234"), ("Pat", "O_Neil", "2000"))
    assert search(client, "%%") == []
    assert search(client, "__") == []
    assert [r["last_name"] for r in search(client, "o_")] == ["O_Neil"]


def test_search_returns_at_most_eight(client):
    add_recipients(*[(f"Person{i}", f"Smith{i}", str(1000 + i)) for i in range(12)])
    assert len(search(client, "smith")) == 8


def test_search_sorts_by_last_name_with_blanks_last(client):
    add_recipients(("Ann", None, "1100"), ("Ann", "Zane", "1200"), ("Ann", "Adams", "1300"))
    assert [r["mailbox_number"] for r in search(client, "ann")] == ["1300", "1200", "1100"]


def test_search_requires_login(app):
    response = app.test_client().get("/api/recipients/search?q=riv")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_confirm_page_loads_autofill_script(client):
    html = client.get(f"/checkin/confirm?tracking={UPS_TRACKING}").get_data(as_text=True)
    assert 'data-search-url="/api/recipients/search"' in html
    assert "js/recipient_search.js" in html


# ---- Check-out scan and lookup ----


def checkout_lookup(client, tracking, **params):
    return client.get("/checkout/confirm", query_string={"tracking": tracking, **params})


def test_scan_pages_point_the_scanner_at_their_own_confirm_page(client):
    checkin_html = client.get("/checkin").get_data(as_text=True)
    checkout_html = client.get("/checkout").get_data(as_text=True)
    assert '<div id="reader" data-confirm-url="/checkin/confirm"' in checkin_html
    assert '<div id="reader" data-confirm-url="/checkout/confirm"' in checkout_html
    assert 'action="/checkout/confirm"' in checkout_html  # manual entry form
    assert "data-tracking-patterns=" in checkout_html


def test_dashboard_links_to_check_in_and_check_out(client):
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'href="/checkin"' in html
    assert 'href="/checkout"' in html


def test_checkout_lookup_redirects_without_tracking(client):
    response = checkout_lookup(client, "  ")
    assert response.status_code == 302
    assert response.headers["Location"] == "/checkout"
    html = client.get("/checkout").get_data(as_text=True)
    assert "Scan a barcode or enter a tracking number to continue." in html


def test_checkout_lookup_shows_checked_in_package(client):
    post_checkin(client, first_name="Ada", last_name="Lovelace", box_number="1815", notes="Fragile")
    html = checkout_lookup(client, UPS_TRACKING).get_data(as_text=True)

    assert "Checked in" in html
    assert UPS_TRACKING in html
    assert "UPS" in html
    assert "Ada Lovelace" in html
    assert "1815" in html
    assert "Fragile" in html
    assert "data-local-time" in html
    assert "Mark as Picked Up</button>" in html
    assert "disabled>Mark as Picked Up" not in html
    assert 'action="/checkout/confirm"' in html
    assert f'name="tracking" value="{UPS_TRACKING}"' in html
    assert 'name="student_id"' in html
    assert "No package found" not in html


def test_checkout_lookup_hides_missing_recipient_fields(client):
    post_checkin(client, notes="Left at front desk")
    html = checkout_lookup(client, UPS_TRACKING).get_data(as_text=True)

    assert "<dt>Recipient</dt>" not in html
    assert "<dt>Box number</dt>" not in html
    assert "Left at front desk" in html


def test_checkout_lookup_normalizes_like_check_in(client):
    post_checkin(client, box_number="1815")
    html = checkout_lookup(client, "1z999 aa10 1234 5678 4").get_data(as_text=True)
    assert "Checked in" in html


def test_checkout_lookup_shows_picked_up_package(client):
    post_checkin(client, box_number="1815")
    package = all_packages()[0]
    package.status = "checked_out"
    package.checked_out_at = package.checked_in_at
    db.session.commit()

    html = checkout_lookup(client, UPS_TRACKING).get_data(as_text=True)
    assert "Picked up" in html
    assert "No further action needed." in html
    assert "Mark as Picked Up" not in html
    assert 'name="student_id"' not in html


def test_checkout_lookup_offers_check_in_when_not_found(client):
    response = checkout_lookup(client, "tba123456789012", source="manual")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No package found with this tracking number." in html
    assert "TBA123456789012" in html
    assert 'href="/checkin/confirm?tracking=TBA123456789012&amp;source=manual"' in html
    assert "Check it in now" in html


def test_checkout_pages_require_login(app):
    client = app.test_client()
    for url in ("/checkout", f"/checkout/confirm?tracking={UPS_TRACKING}"):
        response = client.get(url)
        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login")


# ---- Marking as picked up ----


def post_pickup(client, tracking=UPS_TRACKING, **fields):
    return client.post("/checkout/confirm", data={"tracking": tracking, **fields})


def test_pickup_checks_the_package_out(client):
    post_checkin(client, box_number="1815")
    response = post_pickup(client, student_id=" 00912345 ")

    assert response.status_code == 302
    assert response.headers["Location"] == "/checkout"
    package = all_packages()[0]
    staff = db.session.scalar(db.select(Staff).filter_by(username="tester"))
    assert package.status == "checked_out"
    assert package.checked_out_by == staff.id
    assert package.checked_out_at is not None
    assert package.checked_out_at >= package.checked_in_at
    assert package.checked_out_to == "00912345"


def test_pickup_success_message_shows_on_scanner_page(client):
    post_checkin(client, box_number="1815")
    post_pickup(client)
    html = client.get("/checkout").get_data(as_text=True)
    assert f"Package {UPS_TRACKING} marked as picked up" in html
    assert "alert-success" in html


def test_pickup_without_student_id_saves_none(client):
    post_checkin(client, box_number="1815")
    post_pickup(client, student_id="   ")
    package = all_packages()[0]
    assert package.status == "checked_out"
    assert package.checked_out_to is None


def test_pickup_normalizes_tracking(client):
    post_checkin(client, box_number="1815")
    post_pickup(client, tracking="1z999 aa10 1234 5678 4")
    assert all_packages()[0].status == "checked_out"


def test_pickup_rejects_overlong_student_id(client):
    post_checkin(client, box_number="1815")
    response = post_pickup(client, student_id="1" * 21)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Student ID can be at most 20 characters." in html
    assert all_packages()[0].status == "checked_in"


def test_pickup_of_unknown_package_redirects(client):
    response = post_pickup(client, tracking="TBA123456789012")
    assert response.status_code == 302
    assert response.headers["Location"] == "/checkout"
    html = client.get("/checkout").get_data(as_text=True)
    assert "No package found with this tracking number" in html


def test_pickup_twice_keeps_the_first_checkout(client):
    post_checkin(client, box_number="1815")
    post_pickup(client, student_id="111")
    first = all_packages()[0]
    first_time, first_staff = first.checked_out_at, first.checked_out_by

    response = post_pickup(client, student_id="222")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Picked up" in html
    assert "No further action needed." in html
    assert "Checked out by" in html
    assert "tester" in html
    assert "111" in html

    package = all_packages()[0]
    assert package.checked_out_at == first_time
    assert package.checked_out_by == first_staff
    assert package.checked_out_to == "111"


def test_pickup_that_loses_a_race_shows_picked_up(client, monkeypatch):
    post_checkin(client, box_number="1815")
    real_find = checkout.find_package

    def find_then_lose_race(tracking):
        # The page loaded while it was checked in, but another request checks it
        # out before this one's update runs
        package = real_find(tracking)
        db.session.execute(
            db.update(Package)
            .where(Package.id == package.id)
            .values(status="checked_out", checked_out_to="111")
            .execution_options(synchronize_session=False)
        )
        return package

    monkeypatch.setattr(checkout, "find_package", find_then_lose_race)
    response = post_pickup(client, student_id="222")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No further action needed." in html
    assert all_packages()[0].checked_out_to == "111"


def test_pickup_requires_login(app):
    response = app.test_client().post("/checkout/confirm", data={"tracking": UPS_TRACKING})
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
