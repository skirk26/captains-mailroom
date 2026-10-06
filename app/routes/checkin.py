# Package check-in routes (scan label, assign recipient, photo location) go here.
from flask import Blueprint, render_template
from flask_login import login_required

checkin_bp = Blueprint("checkin", __name__)


@checkin_bp.route("/checkin")
@login_required
def checkin():
    return render_template("checkin.html")
