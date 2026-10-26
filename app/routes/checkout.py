# Package check-out routes (scan label, look up package, mark picked up).
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from app.models import Package
from app.routes.checkin import CARRIER_PATTERNS, normalize_tracking

checkout_bp = Blueprint("checkout", __name__)

# Package.status once someone has collected it (set by the pickup step, next task)
PICKED_UP_STATUS = "picked_up"


@checkout_bp.route("/checkout")
@login_required
def checkout():
    # scanner.js uses these to skip 2D codes that aren't tracking numbers
    tracking_patterns = [pattern.pattern for _, pattern in CARRIER_PATTERNS]
    return render_template("checkout.html", tracking_patterns=tracking_patterns)


@checkout_bp.route("/checkout/confirm")
@login_required
def confirm():
    # Normalized the same way check-in stores it, so a typed number with spaces
    # or lowercase letters still finds the scanned package
    tracking = normalize_tracking(request.args.get("tracking", ""))
    if not tracking:
        flash("Scan a barcode or enter a tracking number to continue.", "warning")
        return redirect(url_for("checkout.checkout"))

    package = db.session.scalar(db.select(Package).filter_by(tracking_number=tracking))
    if package is None:
        state = "not_found"
    elif package.status == PICKED_UP_STATUS:
        state = "picked_up"
    else:
        state = "checked_in"

    return render_template(
        "checkout_confirm.html",
        state=state,
        package=package,
        tracking=tracking,
        # Passed on to check-in if the package isn't found, so its chip stays right
        source=request.args.get("source", ""),
    )
