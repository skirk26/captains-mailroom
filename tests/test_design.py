import glob
import re

import pytest

from app import create_app, db
from app.icons import icon
from app.models import Staff

PASSWORD = "test-only-password"


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-only-secret-key",
        "WTF_CSRF_ENABLED": False,
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


def read(path):
    with open(path, encoding="utf-8") as source:
        return source.read()


def test_old_palette_is_gone():
    files = glob.glob("app/templates/*.html") + glob.glob("app/static/css/*.css") + glob.glob("app/static/js/*.js")
    for path in files:
        text = read(path).lower()
        for old in ("navy", "gold", "cream", "#1c2b4a", "#d9a441", "#faf6ee"):
            assert old not in text, f"{old} in {path}"


def test_colors_live_only_in_tokens_css():
    for path in ("app/static/css/components.css", "app/static/css/style.css"):
        css = re.sub(r"url\([^)]*\)", "", read(path))  # the select arrow's data: SVG
        assert re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css) is None, path


def test_tokens_define_the_cnu_palette_and_dark_mode():
    tokens = read("app/static/css/tokens.css")
    assert "--color-brand: #1B386D;" in tokens
    assert "--color-silver: #84888B;" in tokens
    assert "--color-text-secondary: #5F656B;" in tokens
    assert "@media (prefers-color-scheme: dark)" in tokens
    assert "@media (prefers-reduced-motion: reduce)" in tokens


def test_base_layout_on_sign_in_page(app):
    html = app.test_client().get("/login").get_data(as_text=True)
    assert 'content="width=device-width, initial-scale=1, viewport-fit=cover"' in html
    assert "css/tokens.css" in html and "css/components.css" in html
    assert 'class="inset-list"' in html
    assert "btn btn-primary" in html


def test_dashboard_uses_nav_bar_and_icons(client):
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'class="nav-bar"' in html
    assert '<h1 class="nav-bar-title">Captain&#39;s Mail</h1>' in html or "nav-bar-title\">Captain's Mail" in html
    assert html.count('class="icon') >= 4


def test_flash_messages_come_with_an_icon(client):
    client.post("/logout")
    html = client.get("/login").get_data(as_text=True)
    assert "You have been signed out." in html
    assert re.search(r'class="alert alert-info[^"]*" role="alert">\s*<svg class="icon"', html)


def test_status_chips_pair_color_icon_and_word(client):
    client.post("/checkin/confirm", data={"tracking": "1Z999AA10123456784", "carrier": "UPS", "box_number": "1815"})
    html = client.get("/checkout/confirm?tracking=1Z999AA10123456784").get_data(as_text=True)
    assert re.search(r'class="status-chip status-success[^"]*"><svg class="icon".*?</svg><span>Checked in</span>', html)


def test_icon_helper_inlines_local_svg():
    svg = str(icon("chevron-left"))
    assert svg.startswith('<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"')
    assert "<path" in svg
    with pytest.raises(ValueError):
        icon("../../secrets")
