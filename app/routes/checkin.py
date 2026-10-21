# Package check-in routes (scan label, assign recipient, photo location) go here.
import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Package, Recipient

checkin_bp = Blueprint("checkin", __name__)

CARRIERS = ["UPS", "FedEx", "USPS", "Amazon", "DHL", "Other"]

# Common tracking number shapes, checked in order. A best guess only: staff can
# always change the carrier on the confirm page.
CARRIER_PATTERNS = [
    ("UPS", re.compile(r"1Z[0-9A-Z]{16}")),
    ("Amazon", re.compile(r"TBA\d{12}")),
    ("USPS", re.compile(r"9[2-5]\d{20}|[A-Z]{2}\d{9}US")),
    ("FedEx", re.compile(r"\d{12}|\d{15}|96\d{20}")),
    ("DHL", re.compile(r"\d{10}")),
]

TRACKING_MAX_LENGTH = 100
NAME_MAX_LENGTH = 50
NOTES_MAX_LENGTH = 500
BOX_MIN, BOX_MAX = 1000, 5199
DUPLICATE_MESSAGE = "This package has already been checked in."
SEARCH_MIN_LENGTH = 2
SEARCH_LIMIT = 8

# Every field the confirm form posts, so a failed save can refill all of them
FORM_FIELDS = ("tracking", "carrier", "first_name", "last_name", "box_number", "notes", "source")


def normalize_tracking(tracking):
    # USPS prints numbers with spaces and people type in lowercase; barcodes have
    # neither. One stored form lets the duplicate check catch both.
    return re.sub(r"\s+", "", tracking).upper()


def guess_carrier(tracking):
    compact = normalize_tracking(tracking)
    for carrier, pattern in CARRIER_PATTERNS:
        if pattern.fullmatch(compact):
            return carrier
    return None


def validate_checkin_form(form):
    """Check the confirm form without touching the database.

    Returns (cleaned, errors). Cleaned text is trimmed, blank optional fields
    are None, and the tracking number is normalized. Errors is a list of
    messages to show staff; empty means the form is valid.
    """

    def text(name):
        return (form.get(name) or "").strip()

    cleaned = {
        "tracking": normalize_tracking(text("tracking")),
        "carrier": text("carrier"),
        "first_name": text("first_name") or None,
        "last_name": text("last_name") or None,
        "box_number": text("box_number") or None,
        # Browsers send textarea line breaks as \r\n but count them as one character
        "notes": text("notes").replace("\r\n", "\n") or None,
    }
    errors = []

    if not cleaned["tracking"]:
        errors.append("Enter the tracking number.")
    elif len(cleaned["tracking"]) > TRACKING_MAX_LENGTH:
        errors.append(f"Tracking number can't be longer than {TRACKING_MAX_LENGTH} characters.")

    if not cleaned["carrier"]:
        errors.append("Choose the carrier.")
    elif cleaned["carrier"] not in CARRIERS:
        errors.append("Choose a carrier from the list.")

    for field, label in (("first_name", "First name"), ("last_name", "Last name")):
        if cleaned[field] and len(cleaned[field]) > NAME_MAX_LENGTH:
            errors.append(f"{label} can't be longer than {NAME_MAX_LENGTH} characters.")

    box = cleaned["box_number"]
    # [0-9] rather than isdigit(), which also accepts characters like "²"
    if box is not None and not (re.fullmatch(r"[0-9]{4}", box) and BOX_MIN <= int(box) <= BOX_MAX):
        errors.append(f"Box number must be a number from {BOX_MIN} to {BOX_MAX}.")

    if cleaned["notes"] and len(cleaned["notes"]) > NOTES_MAX_LENGTH:
        errors.append(f"Notes can't be longer than {NOTES_MAX_LENGTH} characters.")

    if not any(cleaned[field] for field in ("first_name", "last_name", "box_number", "notes")):
        errors.append("Fill in at least a name, a box number, or a note.")

    return cleaned, errors


def tracking_exists(tracking):
    return db.session.scalar(db.select(Package.id).filter_by(tracking_number=tracking)) is not None


def matches_text(column, value):
    # Case-insensitive, and a blank field only matches a blank column
    if value is None:
        return or_(column.is_(None), column == "")
    return func.lower(column) == value.lower()


def find_or_create_recipient(first_name, last_name, box_number):
    recipient = db.session.scalar(
        db.select(Recipient)
        .where(
            matches_text(Recipient.first_name, first_name),
            matches_text(Recipient.last_name, last_name),
            matches_text(Recipient.mailbox_number, box_number),
        )
        .limit(1)
    )
    if recipient is None:
        recipient = Recipient(first_name=first_name, last_name=last_name, mailbox_number=box_number)
        db.session.add(recipient)
    return recipient


def render_confirm(form, errors=()):
    return render_template(
        "checkin_confirm.html",
        form=form,
        errors=errors,
        carriers=CARRIERS,
        entered_manually=form.get("source") == "manual",
    )


@checkin_bp.route("/checkin")
@login_required
def checkin():
    # scanner.js uses these to skip 2D codes that aren't tracking numbers
    tracking_patterns = [pattern.pattern for _, pattern in CARRIER_PATTERNS]
    return render_template("checkin.html", tracking_patterns=tracking_patterns)


@checkin_bp.route("/checkin/confirm", methods=["GET", "POST"])
@login_required
def confirm():
    if request.method == "POST":
        return save_checkin()

    # Scanning and manual entry both arrive here with ?tracking=<number>
    tracking = request.args.get("tracking", "").strip()
    if not tracking:
        flash("Scan a barcode or enter a tracking number to continue.", "warning")
        return redirect(url_for("checkin.checkin"))

    # The manual-entry form adds source=manual so the page doesn't claim a scan
    form = {
        "tracking": tracking,
        "carrier": guess_carrier(tracking) or "",
        "source": request.args.get("source", ""),
    }
    # Warn straight after the scan, not only once staff have filled the form in
    errors = [DUPLICATE_MESSAGE] if tracking_exists(normalize_tracking(tracking)) else []
    return render_confirm(form, errors)


def save_checkin():
    # Exactly what staff typed, to refill the form if the save fails
    submitted = {name: request.form.get(name, "") for name in FORM_FIELDS}

    cleaned, errors = validate_checkin_form(request.form)
    if cleaned["tracking"] and tracking_exists(cleaned["tracking"]):
        errors.append(DUPLICATE_MESSAGE)
    if errors:
        return render_confirm(submitted, errors)

    recipient = None
    if cleaned["first_name"] or cleaned["last_name"] or cleaned["box_number"]:
        recipient = find_or_create_recipient(
            cleaned["first_name"], cleaned["last_name"], cleaned["box_number"]
        )

    db.session.add(
        Package(
            tracking_number=cleaned["tracking"],
            carrier=cleaned["carrier"],
            recipient=recipient,
            notes=cleaned["notes"],
            status="checked_in",
            checked_in_by=current_user.id,
        )
    )
    try:
        db.session.commit()
    except IntegrityError:
        # A double-tapped Save can get past tracking_exists() in both requests;
        # the unique constraint stops the second one here
        db.session.rollback()
        return render_confirm(submitted, [DUPLICATE_MESSAGE])

    flash(f"Package {cleaned['tracking']} checked in", "success")
    return redirect(url_for("checkin.checkin"))


@checkin_bp.route("/api/recipients/search")
@login_required
def search_recipients():
    """Autofill suggestions for the confirm page: recipients whose first name,
    last name, or box number starts with ?q= (case-insensitive)."""
    q = request.args.get("q", "").strip()
    if len(q) < SEARCH_MIN_LENGTH:
        return jsonify([])

    # autoescape stops % and _ in the query acting as LIKE wildcards
    recipients = db.session.scalars(
        db.select(Recipient)
        .where(
            or_(
                Recipient.last_name.istartswith(q, autoescape=True),
                Recipient.first_name.istartswith(q, autoescape=True),
                Recipient.mailbox_number.istartswith(q, autoescape=True),
            )
        )
        .order_by(
            Recipient.last_name.is_(None),  # people without a last name go last
            func.lower(Recipient.last_name),
            func.lower(Recipient.first_name),
            Recipient.mailbox_number,
        )
        .limit(SEARCH_LIMIT)
    )
    return jsonify(
        [
            {
                "first_name": recipient.first_name,
                "last_name": recipient.last_name,
                "mailbox_number": recipient.mailbox_number,
            }
            for recipient in recipients
        ]
    )
