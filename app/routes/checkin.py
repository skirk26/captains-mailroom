# Package check-in routes (scan label, assign recipient, photo location) go here.
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

checkin_bp = Blueprint("checkin", __name__)


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
        "checkin_confirm.html", tracking=tracking, entered_manually=entered_manually
    )
