import pytest
from flask_login import AnonymousUserMixin

from app import create_app, db
from app.routes.checkin import guess_carrier


@pytest.fixture
def client():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://", "LOGIN_DISABLED": True})

    # Templates read current_user.username, which anonymous users lack
    class TestUser(AnonymousUserMixin):
        username = "tester"

    app.login_manager.anonymous_user = TestUser
    with app.app_context():
        yield app.test_client()
        db.drop_all()


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
    html = client.get("/checkin/confirm?tracking=1Z999AA10123456784").get_data(as_text=True)
    assert '<option value="UPS" selected>' in html


def test_confirm_leaves_carrier_blank_when_unknown(client):
    html = client.get("/checkin/confirm?tracking=HELLO").get_data(as_text=True)
    assert '<option value="" selected>' in html
    assert "<option value=\"UPS\" >" in html


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
