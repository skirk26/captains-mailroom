import io
import re

import pytest
from PIL import Image, ImageFile

from app import create_app, db
from app.models import Package, Staff

PASSWORD = "test-only-password"
UPS_TRACKING = "1Z999AA10123456784"
PHOTO_NAME = re.compile(r"^[0-9a-f]{32}\.jpg$")
JSON = {"Accept": "application/json"}  # what checkin_photo.js sends


def make_app(upload_folder, csrf=False):
    # In-memory database, and uploads go to a temporary folder, never instance/uploads/
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-only-secret-key",
        "WTF_CSRF_ENABLED": csrf,
        "UPLOAD_FOLDER": str(upload_folder),
    })
    with app.app_context():
        staff = Staff(username="tester")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.commit()
    return app


@pytest.fixture
def uploads(tmp_path):
    folder = tmp_path / "uploads"
    folder.mkdir()
    return folder


@pytest.fixture
def app(uploads):
    app = make_app(uploads)
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    client = app.test_client()
    client.post("/login", data={"username": "tester", "password": PASSWORD})
    return client


def check_in(client, tracking=UPS_TRACKING):
    client.post("/checkin/confirm", data={"tracking": tracking, "carrier": "UPS", "box_number": "1815"})
    return db.session.scalar(db.select(Package).filter_by(tracking_number=tracking))


def image_bytes(size=(800, 600), fmt="JPEG", mode="RGB", **save_options):
    buffer = io.BytesIO()
    Image.new(mode, size, "teal").save(buffer, fmt, **save_options)
    return buffer.getvalue()


def upload(client, package_id, data, filename="photo.jpg", headers=JSON):
    return client.post(
        f"/checkin/photo/{package_id}",
        data={"photo": (io.BytesIO(data), filename, "image/jpeg")},
        headers=headers,
        content_type="multipart/form-data",
    )


def stored_files(uploads):
    return sorted(path.name for path in uploads.iterdir())


def refreshed(package):
    db.session.refresh(package)
    return package


# ---- Uploading ----


def test_valid_jpeg_upload_is_stored_and_recorded(client, uploads):
    package = check_in(client)

    response = upload(client, package.id, image_bytes())

    assert response.status_code == 200
    assert response.get_json() == {"redirect": "/checkin"}
    [name] = stored_files(uploads)  # one file, no temp file left behind
    assert PHOTO_NAME.match(name)
    assert refreshed(package).location_photo_path == name
    assert "Photo saved." in client.get("/checkin").get_data(as_text=True)


def test_stored_photo_is_shrunk_upright_and_has_no_metadata(client, uploads):
    package = check_in(client)
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° to view
    exif[0x8825] = {1: "N", 2: (36.0, 58.0, 0.0), 3: "W", 4: (76.0, 29.0, 0.0)}  # GPS
    original = image_bytes((4000, 3000), exif=exif.tobytes())
    with Image.open(io.BytesIO(original)) as check:
        assert check.getexif().get_ifd(0x8825)  # the test image really has GPS

    upload(client, package.id, original)

    with Image.open(uploads / refreshed(package).location_photo_path) as stored:
        assert stored.format == "JPEG"
        assert max(stored.size) <= 1600
        assert stored.size == (1200, 1600)  # turned upright, then shrunk
        assert len(stored.getexif()) == 0
        assert "exif" not in stored.info


def test_client_file_name_is_ignored(client, uploads, tmp_path):
    package = check_in(client)

    upload(client, package.id, image_bytes(), filename="../../evil.jpg")

    [name] = stored_files(uploads)
    assert PHOTO_NAME.match(name)
    assert refreshed(package).location_photo_path == name
    assert not list(tmp_path.rglob("evil.jpg"))
    assert not (tmp_path.parent / "evil.jpg").exists()


@pytest.mark.parametrize("data", [
    b"This is a text file, not a photo.\n" * 20,
    b"",
    image_bytes(fmt="GIF", mode="P"),
    image_bytes()[:400],  # cut-off JPEG
])
def test_non_images_are_rejected_and_save_nothing(client, uploads, data):
    package = check_in(client)

    response = upload(client, package.id, data, filename="photo.jpg")

    assert response.status_code == 400
    assert "isn't a JPEG, PNG or WebP photo" in response.get_json()["error"]
    assert stored_files(uploads) == []
    assert refreshed(package).location_photo_path is None


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_are_accepted_and_stored_as_jpeg(client, uploads, fmt):
    package = check_in(client)

    assert upload(client, package.id, image_bytes(fmt=fmt), filename="photo.png").status_code == 200

    with Image.open(uploads / refreshed(package).location_photo_path) as stored:
        assert stored.format == "JPEG"


def test_oversized_request_gets_the_friendly_413(client, uploads):
    package = check_in(client)

    response = upload(client, package.id, b"\0" * (8 * 1024 * 1024 + 1))

    assert response.status_code == 413
    assert "too large to send (8 MB max)" in response.get_json()["error"]
    assert stored_files(uploads) == []
    assert refreshed(package).location_photo_path is None


def test_oversized_request_from_a_page_gets_the_413_page(client):
    response = client.post("/checkin/confirm", data={"notes": "x" * (8 * 1024 * 1024 + 1)})

    assert response.status_code == 413
    assert "That&#39;s too large to send" in response.get_data(as_text=True)


def test_image_over_50_megapixels_is_rejected_before_decoding(client, uploads, monkeypatch):
    package = check_in(client)
    huge = image_bytes((10_000, 5_001), fmt="PNG", mode="1")  # 50.01 MP, but a small file

    def no_decoding(self):
        raise AssertionError("pixels were decoded")

    monkeypatch.setattr(ImageFile.ImageFile, "load", no_decoding)
    response = upload(client, package.id, huge, filename="photo.png")

    assert response.status_code == 400
    assert "too large to use" in response.get_json()["error"]
    assert stored_files(uploads) == []
    assert refreshed(package).location_photo_path is None


def test_replacing_a_photo_deletes_the_old_file(client, uploads):
    package = check_in(client)
    upload(client, package.id, image_bytes())
    first = refreshed(package).location_photo_path

    upload(client, package.id, image_bytes((600, 800)))
    second = refreshed(package).location_photo_path

    assert second != first
    assert stored_files(uploads) == [second]


def test_picked_up_package_is_refused(client, uploads):
    package = check_in(client)
    upload(client, package.id, image_bytes())
    kept = refreshed(package).location_photo_path
    client.post("/checkout/confirm", data={"tracking": UPS_TRACKING})
    assert refreshed(package).status == "checked_out"

    response = upload(client, package.id, image_bytes())

    assert response.status_code == 409
    assert response.get_json()["error"] == "This package has already been picked up."
    assert stored_files(uploads) == [kept]  # check-out keeps the photo, and nothing new
    assert refreshed(package).location_photo_path == kept


def test_unknown_package_is_refused(client, uploads):
    response = upload(client, 999, image_bytes())

    assert response.status_code == 404
    assert stored_files(uploads) == []


# ---- Login and CSRF ----


def test_logged_out_users_are_sent_to_login(app, uploads):
    name = "0123456789abcdef0123456789abcdef.jpg"
    (uploads / name).write_bytes(image_bytes())
    client = app.test_client()

    for method, url in [("GET", "/checkin/photo/1"), ("POST", "/checkin/photo/1"), ("GET", f"/photos/{name}")]:
        response = client.open(url, method=method)
        assert response.status_code == 302, url
        assert response.headers["Location"].startswith("/login"), url


def test_upload_without_csrf_token_is_rejected(uploads):
    app = make_app(uploads, csrf=True)
    with app.app_context():
        client = app.test_client()
        login_page = client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', login_page).group(1)
        client.post("/login", data={"username": "tester", "password": PASSWORD, "csrf_token": token})
        client.post("/checkin/confirm", data={
            "tracking": UPS_TRACKING, "carrier": "UPS", "box_number": "1815", "csrf_token": token,
        })
        package = db.session.scalar(db.select(Package))

        response = upload(client, package.id, image_bytes())
        assert response.status_code == 400
        assert stored_files(uploads) == []

        # The photo page's token, sent in the header the way checkin_photo.js does
        page = client.get(f"/checkin/photo/{package.id}").get_data(as_text=True)
        page_token = re.search(r'data-csrf-token="([^"]+)"', page).group(1)
        response = upload(client, package.id, image_bytes(), headers={**JSON, "X-CSRFToken": page_token})
        assert response.status_code == 200
        db.session.remove()
        db.drop_all()


# ---- Serving ----


def test_photo_is_served_privately_and_inline(client, uploads):
    package = check_in(client)
    upload(client, package.id, image_bytes())

    response = client.get(f"/photos/{refreshed(package).location_photo_path}")

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.headers["Cache-Control"] == "private"
    assert response.headers["Content-Disposition"] == "inline"


@pytest.mark.parametrize("path", [
    "secret.jpg",                                   # exists, but not a stored-photo name
    "0123456789abcdef0123456789abcdef.jpg",         # right shape, no such file
    "0123456789ABCDEF0123456789ABCDEF.jpg",         # uppercase
    "0123456789abcdef0123456789abcde.jpg",          # 31 characters
    "0123456789abcdef0123456789abcdef.png",
    "0123456789abcdef0123456789abcdef.jpg%0A",      # trailing newline
    "../uploads/secret.jpg",
    "..%2Fsecret.jpg",
    "%2e%2e%2f%2e%2e%2fapp%2f__init__.py",
    "..\\secret.jpg",
    "",
])
def test_bad_names_and_path_tricks_are_404(client, uploads, path):
    (uploads / "secret.jpg").write_bytes(image_bytes())

    assert client.get(f"/photos/{path}").status_code == 404


# ---- Check-in flow ----


def test_check_in_goes_to_the_photo_page_and_skip_returns(client):
    response = client.post("/checkin/confirm", data={"tracking": UPS_TRACKING, "carrier": "UPS", "box_number": "1815"})
    package = db.session.scalar(db.select(Package))
    assert response.status_code == 302
    assert response.headers["Location"] == f"/checkin/photo/{package.id}"

    response = client.get(f"/checkin/photo/{package.id}/skip")
    assert response.status_code == 302
    assert response.headers["Location"] == "/checkin"
    assert "Checked in without a photo." in client.get("/checkin").get_data(as_text=True)


# ---- Check-out page ----


def checkout_page(client):
    response = client.get(f"/checkout/confirm?tracking={UPS_TRACKING}")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_checkout_page_shows_the_photo(client):
    package = check_in(client)
    upload(client, package.id, image_bytes())
    name = refreshed(package).location_photo_path

    html = checkout_page(client)

    assert "Where it's stored" in html
    assert f'src="/photos/{name}" loading="lazy"' in html
    assert 'alt="Photo of where this package is stored."' in html
    assert f'href="/checkin/photo/{package.id}"' in html and "Retake photo" in html
    assert 'id="photo-viewer"' in html
    assert "No storage photo" not in html


def test_checkout_page_without_a_photo_shows_the_placeholder(client):
    package = check_in(client)

    html = checkout_page(client)

    assert "No storage photo" in html
    assert f'href="/checkin/photo/{package.id}"' in html and "Add photo" in html
    assert "/photos/" not in html
    assert 'id="photo-viewer"' not in html


def test_checkout_page_with_a_missing_file_shows_the_placeholder(client, uploads, caplog):
    package = check_in(client)
    upload(client, package.id, image_bytes())
    name = refreshed(package).location_photo_path
    (uploads / name).unlink()

    html = checkout_page(client)

    assert "Photo file missing" in html
    assert "Add photo" in html
    assert "/photos/" not in html
    assert any(r.levelname == "WARNING" and name in r.getMessage() for r in caplog.records)


def test_checkout_page_hides_the_photo_once_picked_up(client, uploads):
    package = check_in(client)
    upload(client, package.id, image_bytes())
    name = refreshed(package).location_photo_path
    client.post("/checkout/confirm", data={"tracking": UPS_TRACKING})

    html = checkout_page(client)

    assert "Picked up" in html
    assert "Where it's stored" not in html
    assert "/photos/" not in html
    assert refreshed(package).location_photo_path == name
    assert stored_files(uploads) == [name]
