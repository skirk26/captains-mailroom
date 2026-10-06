# Package check-in routes (scan label, assign recipient, photo location) go here.
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

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


def guess_carrier(tracking):
    compact = re.sub(r"\s+", "", tracking).upper()  # USPS prints numbers with spaces
    for carrier, pattern in CARRIER_PATTERNS:
        if pattern.fullmatch(compact):
            return carrier
    return None


@checkin_bp.route("/checkin")
@login_required
def checkin():
    return render_template("checkin.html")


@checkin_bp.route("/checkin/confirm")
@login_required
def confirm():
    # Scanning and manual entry both arrive here with ?tracking=<number>
    tracking = request.args.get("tracking", "").strip()
    if not tracking:
        flash("Scan a barcode or enter a tracking number to continue.", "warning")
        return redirect(url_for("checkin.checkin"))

    # The manual-entry form adds source=manual so the page doesn't claim a scan
    entered_manually = request.args.get("source") == "manual"
    return render_template(
        "checkin_confirm.html",
        tracking=tracking,
        entered_manually=entered_manually,
        carriers=CARRIERS,
        guessed_carrier=guess_carrier(tracking),
    )
