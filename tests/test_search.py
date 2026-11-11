import html
import io
import re
from datetime import timedelta

import pytest
from PIL import Image

from app import create_app, db
from app.models import Package, Recipient, Staff, utc_now

PASSWORD = "test-only-password"
STUDENT_ID = "86753091"  # digits that appear in no tracking number or box
JSON = {"Accept": "application/json"}

# The seeded packages, newest first. Box numbers are chosen so they don't also
# appear inside any tracking number.
FULL = "1Z999AA10123456784"        # Alex Rivera, box 1815
BOX_ONLY = "TBA123456789012"       # box 2001, no name
NAME_ONLY = "9400100000000000000001"  # Jordan Smith, no box
NOTES_ONLY = "1ZROSS001234567890"  # no recipient, has notes
PICKED_UP = "1Z999AA10198765432"   # Sam Lee, box 3050, checked out
WITH_PHOTO = "1Z999AA10155555555"  # Pat Ross, box 4100, has a photo
PHOTO_NAME = "0123456789abcdef0123456789abcdef.jpg"


def make_app(uploads):
    # In-memory database; photos and thumbnails go to a temporary folder,
    # never instance/uploads/
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": "test-only-secret-key",
        "WTF_CSRF_ENABLED": False,
        "UPLOAD_FOLDER": str(uploads),
    })
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


def staff_id():
    return db.session.scalar(db.select(Staff.id))


def add_package(tracking, hours_ago=0, first=None, last=None, box=None, **fields):
    recipient = None
    if first or last or box:
        recipient = Recipient(first_name=first, last_name=last, mailbox_number=box)
    package = Package(
        tracking_number=tracking, carrier=fields.pop("carrier", "UPS"), recipient=recipient,
        checked_in_by=staff_id(), checked_in_at=utc_now() - timedelta(hours=hours_ago), **fields,
    )
    db.session.add(package)
    db.session.commit()
    return package


def jpeg_bytes(size=(1600, 1200), color="teal"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def seeded(app, uploads):
    (uploads / PHOTO_NAME).write_bytes(jpeg_bytes())
    return {
        "full": add_package(FULL, 0, "Alex", "Rivera", "1815"),
        "box_only": add_package(BOX_ONLY, 1, box="2001", carrier="Amazon"),
        "name_only": add_package(NAME_ONLY, 2, "Jordan", "Smith", carrier="USPS"),
        "notes_only": add_package(NOTES_ONLY, 3, notes="Leave at the front desk"),
        "picked_up": add_package(
            PICKED_UP, 4, "Sam", "Lee", "3050", status="checked_out",
            checked_out_by=staff_id(), checked_out_at=utc_now(), checked_out_to=STUDENT_ID,
        ),
        "with_photo": add_package(WITH_PHOTO, 5, "Pat", "Ross", "4100", location_photo_path=PHOTO_NAME),
    }


def search(client, **params):
    response = client.get("/search", query_string=params)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def found(page_html):
    """Tracking numbers of the result rows, in order (from each row's detail link)."""
    ids = [int(i) for i in re.findall(r'href="/packages/(\d+)', page_html)]
    return [db.session.get(Package, i).tracking_number for i in ids]


def hrefs(page_html, css_class):
    pattern = rf'<a href="([^"]*)" class="[^"]*{css_class}'
    return [html.unescape(h) for h in re.findall(pattern, page_html)]


# ---- Matching ----


@pytest.mark.parametrize("q", [FULL, FULL.lower(), "1z999 aa1 0123 456784", "6784", "23456784", " 2345 6784 "])
def test_tracking_number_full_last_digits_lowercase_and_spaced(client, seeded, q):
    assert found(search(client, q=q)) == [FULL]


@pytest.mark.parametrize("q", ["alex rivera", "rivera alex", "rivera, alex", "Rivera,Alex", "riv", "alex", "ALEX"])
def test_name_in_either_order_comma_prefix_and_first_name(client, seeded, q):
    assert found(search(client, q=q)) == [FULL]


def test_box_number(client, seeded):
    assert found(search(client, q="1815")) == [FULL]
    assert found(search(client, q="2001")) == [BOX_ONLY]


def test_box_number_that_matches_nobody(client, seeded):
    page = search(client, q="5199")
    assert found(page) == []
    assert "No packages found" in page


def test_name_and_tracking_matches_are_combined(client, seeded):
    # "ross" is Pat Ross's last name and part of another package's tracking number
    assert found(search(client, q="ross")) == [NOTES_ONLY, WITH_PHOTO]


def test_package_without_recipient_found_by_tracking(client, seeded):
    assert found(search(client, q="ss0012345")) == [NOTES_ONLY]


def test_name_search_handles_packages_without_names(client, seeded):
    # Every package without a first name, last name, or recipient is checked and skipped
    assert found(search(client, q="jordan")) == [NAME_ONLY]
    assert found(search(client, q="smith jordan")) == [NAME_ONLY]


@pytest.mark.parametrize("q", ["%", "%%%%", "_", "____", "\\", "\\\\\\\\", "a%", "%a", "r_vera", "1Z999%", "1Z_99"])
def test_like_wildcards_are_plain_characters(client, seeded, q):
    assert found(search(client, q=q)) == []


def test_wildcard_characters_match_when_really_in_the_data(client, seeded):
    under = add_package("1Z999AA10100000001", first="Pat", last="Under_Score", box="1501")
    percent = add_package("1Z999AA10100000002", first="Pat", last="Per%cent", box="1502")
    slash = add_package("1Z999AA10100000003", first="Pat", last="Back\\slash", box="1503")

    assert found(search(client, q="under_")) == [under.tracking_number]
    assert found(search(client, q="per%")) == [percent.tracking_number]
    assert found(search(client, q="back\\")) == [slash.tracking_number]
    assert found(search(client, q="unde_score")) == []  # _ isn't "any one character"
    assert found(search(client, q="p%t")) == []        # % isn't "anything"


@pytest.mark.parametrize("q", [
    "x" * 150,
    "1Z999AA10123456784" * 10,
    "     ",
    ",,, ,",
    "'; DROP TABLE package; --",
    "<>\"'&",
    "🙂📦 émile",
    "\x00",
    "a",
])
def test_odd_queries_dont_crash(client, seeded, q):
    search(client, q=q)
    assert db.session.scalar(db.select(db.func.count(Package.id))) == 6


def test_long_query_is_cut_to_100_characters(client, seeded):
    page = search(client, q=FULL + "x" * 200)
    value = re.search(r'id="search-q" name="q" value="([^"]*)"', page).group(1)
    assert len(html.unescape(value)) == 100


def test_empty_query_shows_recent_packages(client, seeded):
    assert found(search(client)) == [FULL, BOX_ONLY, NAME_ONLY, NOTES_ONLY, PICKED_UP, WITH_PHOTO]
    assert found(search(client, q="   ")) == found(search(client))


# ---- Filters and paging ----


def test_status_filters(client, seeded):
    waiting = [FULL, BOX_ONLY, NAME_ONLY, NOTES_ONLY, WITH_PHOTO]
    assert found(search(client, status="all")) == [FULL, BOX_ONLY, NAME_ONLY, NOTES_ONLY, PICKED_UP, WITH_PHOTO]
    assert found(search(client, status="waiting")) == waiting
    assert found(search(client, status="picked_up")) == [PICKED_UP]
    assert found(search(client, status="nonsense")) == found(search(client, status="all"))


def test_filter_combines_with_search(client, seeded):
    assert found(search(client, q="1Z999", status="waiting")) == [FULL, WITH_PHOTO]
    assert found(search(client, q="1Z999", status="picked_up")) == [PICKED_UP]


def test_active_filter_is_marked(client, seeded):
    page = search(client, q="alex", status="picked_up")
    assert re.search(r'href="/search\?q=alex&amp;status=picked_up" class="segmented-control-item"\s+aria-current="page"', page)
    assert page.count('aria-current="page"') == 1


@pytest.fixture
def sixty(app):
    # Package 0 is the newest
    return [add_package(f"1Z999AA1{i:010d}", hours_ago=i).tracking_number for i in range(60)]


def test_sixty_packages_page_as_25_25_10_newest_first(client, sixty):
    pages = [found(search(client, q="1z999", status="waiting", page=n)) for n in (1, 2, 3)]

    assert [len(p) for p in pages] == [25, 25, 10]
    assert pages[0] + pages[1] + pages[2] == sixty


def test_previous_and_next_keep_q_and_status(client, sixty):
    first = search(client, q="1z999", status="waiting")
    assert hrefs(first, "btn") == ["/search?q=1z999&status=waiting&page=2"]

    middle = search(client, q="1z999", status="waiting", page=2)
    assert hrefs(middle, "btn") == ["/search?q=1z999&status=waiting", "/search?q=1z999&status=waiting&page=3"]

    last = search(client, q="1z999", status="waiting", page=3)
    assert hrefs(last, "btn") == ["/search?q=1z999&status=waiting&page=2"]


@pytest.mark.parametrize("page, expected_page", [("0", 1), ("-5", 1), ("abc", 1), ("", 1), ("2.5", 1),
                                                 ("999999999999999999999999", 3), ("4", 3)])
def test_odd_page_numbers(client, sixty, page, expected_page):
    html_page = search(client, page=page)
    assert f"Page {expected_page} of 3" in html_page
    assert found(html_page) == sixty[(expected_page - 1) * 25:][:25]


def test_result_count_line(client, sixty):
    assert "Showing 1–25 of 60." in search(client)
    assert "Showing 26–50 of 60." in search(client, page=2)
    assert "Showing 51–60 of 60." in search(client, page=3)
    assert "Showing 1–1 of 1." in search(client, q="0000000042")


def test_count_line_is_in_a_live_region(client, seeded):
    page = search(client)
    assert re.search(r'<div aria-live="polite">\s*<p[^>]*>\s*Showing 1–6 of 6\.', page)


def test_no_packages_at_all(client):
    page = search(client)
    assert "No packages yet" in page
    assert "Showing" not in page


# ---- Display ----


def row_html(page_html, package):
    match = re.search(rf'<a href="/packages/{package.id}[^"]*" class="inset-row search-result">(.*?)</a>',
                      page_html, re.S)
    assert match, f"no row for {package.tracking_number}"
    return match.group(1)


def test_rows_show_details_status_and_thumbnails(client, seeded):
    page = search(client)

    full = row_html(page, seeded["full"])
    assert "…23456784" in full and "UPS" in full
    assert "Alex Rivera · Box 1815" in full
    assert "status-success" in full and "<span>Checked in</span>" in full
    assert "search-thumb-empty" in full

    photo = row_html(page, seeded["with_photo"])
    assert f'src="/photos/thumb/{PHOTO_NAME}" alt=""' in photo
    assert 'loading="lazy"' in photo

    assert "Box 2001" in row_html(page, seeded["box_only"])
    assert "Jordan Smith" in row_html(page, seeded["name_only"])
    assert "No name or box" in row_html(page, seeded["notes_only"])
    assert "Has notes" in row_html(page, seeded["notes_only"])
    assert "Has notes" not in full

    picked = row_html(page, seeded["picked_up"])
    assert "status-neutral" in picked and "Picked up" in picked
    assert picked.count("<time") == 2  # checked in and picked up times


def test_missing_photo_file_shows_the_placeholder(client, seeded, uploads):
    (uploads / PHOTO_NAME).unlink()

    page = search(client)

    assert "/photos/thumb/" not in page
    assert "search-thumb-empty" in row_html(page, seeded["with_photo"])


def detail(client, package, **params):
    return client.get(f"/packages/{package.id}", query_string=params)


def test_detail_page_shows_every_field(client, seeded):
    response = detail(client, seeded["full"])
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    for text in (FULL, "UPS", "Alex Rivera", "1815", "Checked in", "by tester", "None"):
        assert text in page
    assert "No storage photo" in page and "Add photo" in page
    assert "Picked up" not in page


def test_detail_page_fills_in_missing_values(client, seeded):
    page = detail(client, seeded["notes_only"]).get_data(as_text=True)
    assert page.count("Not provided") == 2  # recipient and box
    assert "Leave at the front desk" in page


def test_detail_page_for_a_picked_up_package(client, seeded):
    page = detail(client, seeded["picked_up"]).get_data(as_text=True)

    assert "Picked up" in page and page.count("by tester") == 2
    assert "Go to Check Out" not in page
    assert "Add photo" not in page


def test_checkout_button_only_for_waiting_packages(client, seeded):
    page = detail(client, seeded["full"]).get_data(as_text=True)
    assert f'href="/checkout/confirm?tracking={FULL}"' in page
    assert "Go to Check Out" in page


def test_detail_page_with_a_photo(client, seeded):
    page = detail(client, seeded["with_photo"]).get_data(as_text=True)
    assert f'src="/photos/{PHOTO_NAME}"' in page
    assert 'id="photo-viewer"' in page and "js/photo_viewer.js" in page
    assert "Retake photo" in page


def test_detail_page_with_a_missing_photo_file(client, seeded, uploads):
    (uploads / PHOTO_NAME).unlink()
    response = detail(client, seeded["with_photo"])
    assert response.status_code == 200
    assert "Photo file missing" in response.get_data(as_text=True)


def test_unknown_package_is_404(client, seeded):
    assert client.get("/packages/9999").status_code == 404


def back_link(page_html):
    return html.unescape(re.search(r'<div class="nav-bar-leading"><a href="([^"]*)"', page_html).group(1))


def test_back_link_keeps_the_search(client, seeded):
    page = detail(client, seeded["full"], q="alex", status="waiting", page="2").get_data(as_text=True)
    assert back_link(page) == "/search?q=alex&status=waiting&page=2"


def test_result_rows_link_to_details_with_the_search(client, seeded):
    page = search(client, q="alex", status="waiting")
    assert f'href="/packages/{seeded["full"].id}?q=alex&amp;status=waiting"' in page


@pytest.mark.parametrize("params, expected", [
    ({}, "/search"),
    ({"status": "nonsense", "page": "-1"}, "/search"),
    ({"q": "http://evil.com"}, "/search?q=http://evil.com"),
    ({"status": "http://evil.com", "page": "http://evil.com"}, "/search"),
    ({"next": "http://evil.com", "url": "//evil.com", "back": "http://evil.com"}, "/search"),
])
def test_back_link_is_rebuilt_on_the_server(client, seeded, params, expected):
    page = detail(client, seeded["full"], **params).get_data(as_text=True)

    link = back_link(page)
    assert link == expected
    assert link.startswith("/search")
    assert not re.search(r'href="(https?:)?//', page)


def test_student_ids_never_appear(client, seeded):
    for status in ("all", "waiting", "picked_up"):
        assert STUDENT_ID not in search(client, status=status)
    assert STUDENT_ID not in detail(client, seeded["picked_up"]).get_data(as_text=True)


def test_student_ids_are_not_searched(client, seeded):
    assert found(search(client, q=STUDENT_ID)) == []
    assert found(search(client, q=STUDENT_ID[-4:])) == []


def test_names_and_notes_are_escaped(client, seeded):
    evil = add_package(
        "1Z999AA10100000099", first="<script>alert(1)</script>", last="Smith", box="1600",
        notes="<script>alert(2)</script>",
    )

    for page in (search(client), search(client, q="smith"), detail(client, evil).get_data(as_text=True)):
        assert "<script>alert" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in detail(client, evil).get_data(as_text=True)


# ---- Thumbnails and access ----


def thumb_folder(app):
    from pathlib import Path
    return Path(app.config["THUMB_FOLDER"])


def test_thumbnail_is_made_once_and_fits_240px(app, client, seeded, monkeypatch):
    response = client.get(f"/photos/thumb/{PHOTO_NAME}")

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.headers["Cache-Control"] == "private"
    thumb = thumb_folder(app) / PHOTO_NAME
    with Image.open(thumb) as image:
        assert max(image.size) <= 240
        assert image.size == (240, 180)

    def no_second_thumbnail(filename):
        raise AssertionError("thumbnail was made again")

    monkeypatch.setattr("app.photos.make_thumbnail", no_second_thumbnail)
    second = client.get(f"/photos/thumb/{PHOTO_NAME}")
    assert second.status_code == 200
    assert second.data == thumb.read_bytes()


@pytest.mark.parametrize("path", [
    "../" + PHOTO_NAME,
    "..%2F" + PHOTO_NAME,
    "%2e%2e%2fuploads%2f" + PHOTO_NAME,
    PHOTO_NAME.upper(),
    PHOTO_NAME[1:],
    PHOTO_NAME.replace(".jpg", ".png"),
    PHOTO_NAME + "%0A",
    "fedcba9876543210fedcba9876543210.jpg",  # right shape, no such photo
    "x.jpg",
    "",
])
def test_bad_thumbnail_names_are_404(client, seeded, path):
    assert client.get(f"/photos/thumb/{path}").status_code == 404


def test_thumbnail_of_a_deleted_photo_is_404(app, client, seeded, uploads):
    client.get(f"/photos/thumb/{PHOTO_NAME}")
    (uploads / PHOTO_NAME).unlink()

    assert (thumb_folder(app) / PHOTO_NAME).exists()  # an old thumbnail is never served alone
    assert client.get(f"/photos/thumb/{PHOTO_NAME}").status_code == 404


def test_thumbnail_is_deleted_when_the_photo_is_replaced(app, client, seeded, uploads):
    package = seeded["with_photo"]
    client.get(f"/photos/thumb/{PHOTO_NAME}")
    assert (thumb_folder(app) / PHOTO_NAME).exists()

    response = client.post(
        f"/checkin/photo/{package.id}",
        data={"photo": (io.BytesIO(jpeg_bytes(color="navy")), "new.jpg")},
        headers=JSON, content_type="multipart/form-data",
    )

    assert response.status_code == 200
    db.session.refresh(package)
    assert package.location_photo_path != PHOTO_NAME
    assert not (uploads / PHOTO_NAME).exists()
    assert not (thumb_folder(app) / PHOTO_NAME).exists()


def test_logged_out_users_are_sent_to_login(app, seeded):
    client = app.test_client()
    for url in ("/search", "/search?q=alex", f"/packages/{seeded['full'].id}", f"/photos/thumb/{PHOTO_NAME}"):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response.headers["Location"].startswith("/login"), url


def test_search_is_limited_to_60_requests_a_minute(client, seeded):
    for _ in range(60):
        assert client.get("/search").status_code == 200

    response = client.get("/search?q=alex")
    assert response.status_code == 429
    assert "Too many attempts" in response.get_data(as_text=True)
