# Package check-out routes (scan label, look up package, mark picked up).
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import Package, utc_now
from app.photos import photo_exists
from app.routes.checkin import CARRIER_PATTERNS, normalize_tracking

checkout_bp = Blueprint("checkout", __name__)

CHECKED_IN_STATUS = "checked_in"
CHECKED_OUT_STATUS = "checked_out"
STUDENT_ID_MAX_LENGTH = 20  # matches Package.checked_out_to


@checkout_bp.route("/checkout")
@login_required
def checkout():
    # scanner.js uses these to skip 2D codes that aren't tracking numbers
    tracking_patterns = [pattern.pattern for _, pattern in CARRIER_PATTERNS]
    return render_template("checkout.html", tracking_patterns=tracking_patterns)


def find_package(tracking):
    return db.session.scalar(db.select(Package).filter_by(tracking_number=tracking))


def storage_photo_state(package):
    # "photo", "none", or "missing" (the database names a file that isn't on disk)
    filename = package.location_photo_path
    if not filename:
        return "none"
    if not photo_exists(filename):
        current_app.logger.warning(
            "Storage photo %r for package %s is missing from the upload folder", filename, package.id
        )
        return "missing"
    return "photo"


def render_confirm(package, tracking, errors=(), student_id=""):
    if package is None:
        state = "not_found"
    elif package.status == CHECKED_OUT_STATUS:
        state = "picked_up"
    else:
        state = "checked_in"

    return render_template(
        "checkout_confirm.html",
        state=state,
        package=package,
        tracking=tracking,
        errors=errors,
        student_id=student_id,
        # Passed on to check-in if the package isn't found, so its chip stays right
        source=request.args.get("source", ""),
        # Only while it's on the shelf; once picked up the spot no longer matters
        storage_photo=storage_photo_state(package) if state == "checked_in" else None,
    )


@checkout_bp.route("/checkout/confirm", methods=["GET", "POST"])
@login_required
def confirm():
    # Normalized the same way check-in stores it, so a typed number with spaces
    # or lowercase letters still finds the scanned package
    tracking = normalize_tracking(request.values.get("tracking", ""))
    if not tracking:
        flash("Scan a barcode or enter a tracking number to continue.", "warning")
        return redirect(url_for("checkout.checkout"))

    package = find_package(tracking)
    if request.method == "GET":
        return render_confirm(package, tracking)
    return mark_picked_up(package, tracking)


def mark_picked_up(package, tracking):
    if package is None:
        flash("No package found with this tracking number", "danger")
        return redirect(url_for("checkout.checkout"))
    if package.status == CHECKED_OUT_STATUS:
        return render_confirm(package, tracking)

    student_id = request.form.get("student_id", "").strip()
    if len(student_id) > STUDENT_ID_MAX_LENGTH:
        errors = [f"Student ID can be at most {STUDENT_ID_MAX_LENGTH} characters."]
        return render_confirm(package, tracking, errors, student_id)

    # Only applies while the package is still checked in, so a double tap or two
    # staff checking out the same package at once can't both succeed
    try:
        result = db.session.execute(
            db.update(Package)
            .where(Package.id == package.id, Package.status == CHECKED_IN_STATUS)
            .values(
                status=CHECKED_OUT_STATUS,
                checked_out_by=current_user.id,
                checked_out_at=utc_now(),
                checked_out_to=student_id or None,
            )
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        errors = ["Couldn't save the pickup. Please try again."]
        return render_confirm(package, tracking, errors, student_id)

    if result.rowcount == 0:
        # Lost the race: someone else checked it out first
        db.session.refresh(package)
        return render_confirm(package, tracking)

    flash(f"Package {tracking} marked as picked up", "success")
    return redirect(url_for("checkout.checkout"))
