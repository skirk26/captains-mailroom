import pytest
from sqlalchemy.exc import IntegrityError

from app import create_app, db
from app.models import Package, Recipient, Staff


@pytest.fixture
def app():
    # In-memory database, so tests never touch instance/captains_mail.db
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def staff(app):
    staff = Staff(username="tester")
    staff.set_password("not-a-real-password")
    db.session.add(staff)
    db.session.commit()
    return staff


def test_recipient_names_are_optional(app):
    recipient = Recipient(mailbox_number="2048")
    db.session.add(recipient)
    db.session.commit()

    assert recipient.first_name is None
    assert recipient.last_name is None
    assert recipient.full_name == ""


@pytest.mark.parametrize(
    "first, last, box, expected",
    [
        ("Ada", "Lovelace", "1815", "<Recipient Ada Lovelace (box 1815)>"),
        ("Ada", None, "1815", "<Recipient Ada (box 1815)>"),
        (None, "Lovelace", None, "<Recipient Lovelace (box none)>"),
        (None, None, "1815", "<Recipient (no name) (box 1815)>"),
        (None, None, None, "<Recipient (no name) (box none)>"),
    ],
)
def test_recipient_repr_handles_missing_fields(app, first, last, box, expected):
    recipient = Recipient(first_name=first, last_name=last, mailbox_number=box)
    assert repr(recipient) == expected


def test_package_without_recipient(app, staff):
    package = Package(
        tracking_number="1Z999AA10123456784",
        carrier="UPS",
        checked_in_by=staff.id,
        notes="Box is crushed on one corner",
    )
    db.session.add(package)
    db.session.commit()

    saved = db.session.get(Package, package.id)
    assert saved.recipient is None
    assert saved.notes == "Box is crushed on one corner"
    assert saved.status == "checked_in"
    assert repr(saved) == "<Package UPS 1Z999AA10123456784 (checked_in)>"


def test_package_notes_are_optional(app, staff):
    package = Package(tracking_number="TBA123456789012", carrier="Amazon", checked_in_by=staff.id)
    db.session.add(package)
    db.session.commit()

    assert package.notes is None


def test_package_links_to_recipient(app, staff):
    recipient = Recipient(first_name="Ada")
    package = Package(
        tracking_number="TBA123456789012",
        carrier="Amazon",
        checked_in_by=staff.id,
        recipient=recipient,
    )
    db.session.add(package)
    db.session.commit()

    assert recipient.packages == [package]


def test_package_requires_carrier(app, staff):
    db.session.add(Package(tracking_number="1Z999AA10123456784", checked_in_by=staff.id))
    with pytest.raises(IntegrityError):
        db.session.commit()


def test_tracking_number_is_unique(app, staff):
    for _ in range(2):
        db.session.add(Package(tracking_number="1Z999AA10123456784", carrier="UPS", checked_in_by=staff.id))
    with pytest.raises(IntegrityError):
        db.session.commit()
