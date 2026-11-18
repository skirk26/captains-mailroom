import io
import re

import pytest
from PIL import Image

from app import create_app, db
from app.models import Package, Staff

PASSWORD = "test-only-password"
UPS_TRACKING = "1Z999AA10123456784"
JSON = {"Accept": "application/json"}


@pytest.fixture
def app(tmp_path):
    # In-memory database; photos go to a temporary folder
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-only-secret-key",
        "WTF_CSRF_ENABLED": False,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
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


def check_in(client, tracking=UPS_TRACKING, **fields):
    data = {"tracking": tracking, "carrier": "UPS", "box_number": "1815", **fields}
    return client.post("/checkin/confirm", data=data)


def get_package(tracking=UPS_TRACKING):
    return db.session.scalar(db.select(Package).filter_by(tracking_number=tracking))


def page(client, url, **params):
    response = client.get(url, query_string=params or None)
    assert response.status_code == 200, url
    return response.get_data(as_text=True)


def upload(client, package_id):
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "teal").save(buffer, "JPEG")
    return client.post(f"/checkin/photo/{package_id}", data={"photo": (io.BytesIO(buffer.getvalue()), "p.jpg")},
                       headers=JSON, content_type="multipart/form-data")


def toast_texts(html):
    return [t.strip() for t in re.findall(r'<span class="app-toast-text">([^<]*)</span>', html)]


# ---- Scan screens ----


@pytest.mark.parametrize("url, title, action", [("/checkin", "Check In", "/checkin/confirm"),
                                                ("/checkout", "Check Out", "/checkout/confirm")])
def test_scan_screen_layout(client, url, title, action):
    html = page(client, url)

    assert '<main id="' in html and 'class="scan-page" data-state="starting"' in html
    assert f'<h1 class="scan-topbar-title">{title}</h1>' in html
    assert 'aria-label="Back to home"' in html
    # Flashlight: hidden until scanner.js finds the camera supports it
    assert re.search(r'<button type="button" id="torch-toggle"[^>]*aria-pressed="false" hidden>', html)
    assert '<span class="visually-hidden">Flashlight</span>' in html
    # Camera problems: an alert with Try again, in a live region
    assert re.search(r'<div aria-live="assertive">\s*<div id="camera-error" hidden>\s*<div class="alert alert-warning', html)
    assert '<button type="button" id="camera-retry" class="btn btn-secondary">Try again</button>' in html
    # The success check icon is in the frame, shown by data-state="success"
    assert '<span class="scan-success">' in html
    assert f'<div id="reader" data-confirm-url="{action}"' in html


@pytest.mark.parametrize("url, action", [("/checkin", "/checkin/confirm"), ("/checkout", "/checkout/confirm")])
def test_manual_entry_is_a_bottom_sheet(client, url, action):
    html = page(client, url)

    assert re.search(r'<button type="button" id="manual-entry-toggle"[^>]*aria-haspopup="dialog"'
                     r'[^>]*aria-controls="manual-entry-sheet" aria-expanded="false">Can\'t scan\? Enter manually</button>', html)
    sheet = re.search(r'<dialog id="manual-entry-sheet" class="sheet"[^>]*>(.*?)</dialog>', html, re.S).group(1)
    assert 'class="sheet-handle"' in sheet
    assert '<button type="button" id="manual-entry-close" class="sheet-close" aria-label="Close">' in sheet
    assert f'<form id="manual-entry" action="{action}" method="get">' in sheet
    assert '<label for="manual-tracking" class="form-label">Tracking number</label>' in sheet
    assert 'name="tracking"' in sheet and 'name="source" value="manual"' in sheet
    assert '<button type="submit" class="btn btn-primary btn-lg w-100">Continue</button>' in sheet


@pytest.mark.parametrize("url", ["/checkin", "/checkout", "/checkin/confirm?tracking=1Z999AA10100000009",
                                 "/checkout/confirm?tracking=TBA123456789012"])
def test_no_inline_scripts(client, url):
    html = page(client, url)
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
    assert scripts
    for attrs, body in scripts:
        assert 'src="' in attrs and body.strip() == ""


def test_expect_banner_and_mismatch_still_on_the_new_layout(client):
    check_in(client, first_name="Alex", last_name="Rivera")
    html = page(client, "/checkout", expect=get_package().id)

    overlay = html.split('class="scan-overlay"', 1)[1].split('class="scan-bottom"', 1)[0]
    assert "Scan this package: ends" in overlay
    assert '<div id="scan-mismatch" hidden>' in overlay
    assert f'data-expected-tracking="{UPS_TRACKING}"' in html


# ---- Toasts ----


def test_pickup_returns_to_the_scanner_with_a_toast(client):
    check_in(client)
    response = client.post("/checkout/confirm", data={"tracking": UPS_TRACKING})
    assert response.headers["Location"] == "/checkout"

    html = page(client, "/checkout")
    assert toast_texts(html) == ["Picked up: …23456784."]
    assert '<div class="toast-region" role="status" aria-live="polite">' in html
    assert 'class="app-toast-close" aria-label="Dismiss"' in html
    assert "js/toasts.js" in html
    # Shown once only, and never also as an inline alert
    assert "alert-toast" not in html
    assert toast_texts(page(client, "/checkout")) == []


def test_skip_photo_toast(client):
    check_in(client)
    response = client.get(f"/checkin/photo/{get_package().id}/skip")
    assert response.headers["Location"] == "/checkin"
    assert toast_texts(page(client, "/checkin")) == ["Checked in: …23456784. No photo."]


@pytest.mark.parametrize("package_id", ["999", "9" * 40])
def test_skip_with_an_unknown_package_still_returns(client, package_id):
    response = client.get(f"/checkin/photo/{package_id}/skip")
    assert response.status_code == 302
    assert toast_texts(page(client, "/checkin")) == ["Checked in. No photo."]


def test_photo_toasts_first_photo_and_replacement(client):
    check_in(client)
    package = get_package()

    assert upload(client, package.id).get_json() == {"redirect": "/checkin"}
    assert toast_texts(page(client, "/checkin")) == ["Checked in: …23456784. Photo saved."]

    upload(client, package.id)
    assert toast_texts(page(client, "/checkin")) == ["Photo updated: …23456784."]


def test_other_flash_messages_stay_inline_alerts(client):
    client.get("/checkin/confirm")  # no tracking number: warning back on the scanner
    html = page(client, "/checkin")
    assert "Scan a barcode or enter a tracking number to continue." in html
    assert re.search(r'class="alert alert-warning[^"]*" role="alert">', html)
    assert toast_texts(html) == []


def test_toast_names_are_escaped(client):
    check_in(client, tracking="1Z999<b>A&1234")
    client.get(f"/checkin/photo/{get_package('1Z999<B>A&1234').id}/skip")
    html = page(client, "/checkin")
    # Last 8 characters of 1Z999<B>A&1234, escaped
    assert toast_texts(html) == ["Checked in: …B&gt;A&amp;1234. No photo."]
    assert "<B>A&1234" not in html


# ---- Check-in confirm ----


def test_duplicate_shows_a_button_to_the_existing_record(client):
    check_in(client)
    existing = get_package()

    for html in (page(client, "/checkin/confirm", tracking=UPS_TRACKING.lower()),
                 check_in(client).get_data(as_text=True)):
        alert = re.search(r'<div class="alert alert-danger[^"]*" role="alert">(.*?)</div>\s*</div>', html, re.S).group(1)
        assert "This package has already been checked in." in alert
        assert f'<a href="/packages/{existing.id}" class="btn btn-secondary">Open the existing record</a>' in alert


def test_field_errors_show_under_their_fields(client):
    html = check_in(client, carrier="", box_number="999", first_name="x" * 51).get_data(as_text=True)

    # Summary at the top
    assert "Fix these 3 things:" in html
    # Each message under its field, linked with aria-describedby
    assert re.search(r'id="box-number" name="box_number"[^>]*aria-invalid="true" aria-describedby="box_number-error"', html, re.S)
    assert re.search(r'<p class="field-error" id="box_number-error">.*?<span>Box number must be a number from 1000 to 5199\.</span></p>', html, re.S)
    assert re.search(r'<p class="field-error" id="carrier-error">.*?<span>Choose the carrier\.</span>', html, re.S)
    assert re.search(r'<p class="field-error" id="first_name-error">.*?First name can', html, re.S)
    assert 'id="last-name" name="last_name"' in html and 'aria-describedby="last_name-error"' not in html


def test_recipient_group_error(client):
    html = check_in(client, box_number="").get_data(as_text=True)
    assert re.search(r'<p class="field-error" id="recipient-error">.*?Fill in at least a name, a box number, or a note\.', html, re.S)


def test_clean_form_has_no_field_errors(client):
    html = page(client, "/checkin/confirm", tracking="1Z999AA10100000009")
    assert "field-error" not in html and "aria-invalid" not in html and "alert-danger" not in html


def test_check_in_confirm_layout(client):
    html = page(client, "/checkin/confirm", tracking="1Z999AA10100000009", source="manual")

    assert re.search(r'<div class="confirm-header\s*">\s*<span class="status-chip status-neutral', html)
    assert '<label for="tracking" class="confirm-header-label">Tracking number</label>' in html
    assert re.search(r'<label for="carrier" class="inset-label">Carrier</label>\s*<select class="inset-select" id="carrier" name="carrier" required>', html)
    for field_id, label in [("first-name", "First name"), ("last-name", "Last name"), ("box-number", "Box number"),
                            ("notes", "Notes (optional)")]:
        assert f'<label for="{field_id}" class="inset-field-label">{label}</label>' in html
    assert re.search(r'<div class="action-bar">\s*<div class="action-bar-inner">\s*<button type="submit" id="save-button"', html)
    assert 'class="tab-bar"' not in html
    assert 'data-search-url="/api/recipients/search"' in html


# ---- Check-out confirm ----


def test_check_out_confirm_layout(client):
    check_in(client, first_name="Ada", last_name="Lovelace")
    html = page(client, "/checkout/confirm", tracking=UPS_TRACKING)

    assert html.index("Where it's stored") < html.index("status-chip") < html.index('<dl class="inset-list')
    assert re.search(r'<dt class="inset-label">Recipient</dt>\s*<dd class="inset-value">Ada Lovelace</dd>', html)
    assert '<label for="student-id" class="inset-field-label">Student ID (optional)</label>' in html
    assert re.search(r'<div class="action-bar">\s*<div class="action-bar-inner">\s*<button type="submit" id="pickup-button"', html)
    assert 'class="tab-bar"' not in html


def test_not_found_has_a_check_in_button(client):
    html = page(client, "/checkout/confirm", tracking="TBA123456789012")
    assert re.search(r'role="alert">.*?No package found with this tracking number\..*?'
                     r'<a href="/checkin/confirm\?tracking=TBA123456789012" class="btn btn-secondary">Check it in now</a>', html, re.S)


# ---- Photo screen ----


def test_photo_screen_has_the_sticky_action_bar(client):
    check_in(client)
    package = get_package()
    html = page(client, f"/checkin/photo/{package.id}")

    bar = html.split('<div class="action-bar">', 1)[1].split("</section>", 1)[0]
    assert "<span>Take Photo</span>" in bar and "<span>Use Photo</span>" in bar and "<span>Retake</span>" in bar
    assert f'href="/checkin/photo/{package.id}/skip" class="app-link photo-skip">Skip for now</a>' in bar
    assert 'class="nav-bar"' in html and 'class="tab-bar"' not in html
