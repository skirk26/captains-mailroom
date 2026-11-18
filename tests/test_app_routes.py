import io

import pytest
from PIL import Image

from app import create_app, db
from app.models import SQLITE_MAX_INTEGER, Staff

PASSWORD = "test-only-password"
HUGE_ID = "9" * 40  # far past what SQLite can store


@pytest.fixture
def app(tmp_path):
    # Built with create_app() alone, not run.py, like a production server would
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


# ---- "/" lives in the app, not run.py ----


def test_root_redirects_to_the_dashboard(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"


def test_sign_in_lands_on_the_dashboard(app):
    client = app.test_client()
    response = client.post("/login", data={"username": "tester", "password": PASSWORD}, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/dashboard"


def test_root_when_signed_out_goes_to_sign_in(app):
    response = app.test_client().get("/", follow_redirects=True)
    assert response.request.path == "/login"


# ---- Package ids too big for SQLite get a 404, not a 500 ----


@pytest.mark.parametrize("method, url", [
    ("get", f"/packages/{HUGE_ID}"),
    ("get", f"/checkin/photo/{HUGE_ID}"),
    ("post", f"/checkin/photo/{HUGE_ID}"),
    ("get", f"/packages/{SQLITE_MAX_INTEGER + 1}"),
])
def test_ids_too_big_for_sqlite_are_404(client, method, url):
    kwargs = {}
    if method == "post":
        buffer = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buffer, "JPEG")
        kwargs = {"data": {"photo": (buffer, "p.jpg")}, "content_type": "multipart/form-data"}
    response = getattr(client, method)(url, **kwargs)
    if method == "post":
        # Werkzeug's matcher answers 405 when an id fails a route's limit and
        # another route on the same path takes a different method. Either way
        # it's the friendly error page, not a crash.
        assert response.status_code in (404, 405)
    else:
        assert response.status_code == 404


@pytest.mark.parametrize("url", [f"/packages/{SQLITE_MAX_INTEGER}", f"/checkin/photo/{SQLITE_MAX_INTEGER}"])
def test_largest_storable_id_is_an_ordinary_not_found(client, url):
    # Still a valid number for SQLite: looked up, not found, 404
    assert client.get(url).status_code == 404


def test_huge_ids_show_the_friendly_404_page(client):
    html = client.get(f"/packages/{HUGE_ID}").get_data(as_text=True)
    assert "Page not found" in html
