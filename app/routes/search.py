# Package search: one box that finds packages by tracking number, recipient
# name, or box number, with a status filter; and each package's detail page.
# Student IDs are never searched or shown.
import math
import re

from flask import Blueprint, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import and_, false, func, or_
from sqlalchemy.orm import contains_eager

from app import db, limiter
from app.models import Package, Recipient
from app.photos import photo_exists
from app.routes.checkin import normalize_tracking
from app.routes.checkout import CHECKED_IN_STATUS, CHECKED_OUT_STATUS, storage_photo_state

search_bp = Blueprint("search", __name__)

SEARCH_RATE_LIMIT = "60 per minute"
PER_PAGE = 25
QUERY_MAX_LENGTH = 100
TRACKING_MIN_LENGTH = 4  # enough for "the last few digits"
NAME_WORD_MIN_LENGTH = 2
BOX_NUMBER = re.compile(r"\d{4}")

# ?status= value -> Package.status (None means any)
STATUS_FILTERS = {
    "all": None,
    "waiting": CHECKED_IN_STATUS,
    "picked_up": CHECKED_OUT_STATUS,
}
DEFAULT_STATUS = "all"


def clean_query(raw):
    # "Rivera,  Alex " -> "Rivera Alex"
    text = " ".join(raw.replace(",", " ").split())
    return text[:QUERY_MAX_LENGTH].strip()


def like_escape(text):
    # The user's %, _ and \ are plain characters, not LIKE wildcards
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def name_condition(words):
    # Every word must start the first or last name, in any order
    words = [word for word in words if len(word) >= NAME_WORD_MIN_LENGTH]
    if not words:
        return None
    return and_(*(
        or_(
            Recipient.first_name.ilike(f"{like_escape(word)}%", escape="\\"),
            Recipient.last_name.ilike(f"{like_escape(word)}%", escape="\\"),
        )
        for word in words
    ))


def search_condition(q):
    """What q matches, combined with OR. None for an empty search."""
    if not q:
        return None

    matches = []
    tracking = normalize_tracking(q)  # same form check-in stores
    if len(tracking) >= TRACKING_MIN_LENGTH:
        matches.append(Package.tracking_number.like(f"%{like_escape(tracking)}%", escape="\\"))
    if BOX_NUMBER.fullmatch(q):
        matches.append(Recipient.mailbox_number == q)
    names = name_condition(q.split(" "))
    if names is not None:
        matches.append(names)

    # Too short to match anything (e.g. a single letter)
    return or_(*matches) if matches else false()


def page_number(raw):
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(page, 1)


def search_params(args):
    """(q, status, page) from a query string; anything invalid becomes the default."""
    status = args.get("status", DEFAULT_STATUS)
    if status not in STATUS_FILTERS:
        status = DEFAULT_STATUS
    return clean_query(args.get("q", "")), status, page_number(args.get("page"))


def search_link_args(q, status, page):
    # Leaves defaults out of the URL: /search rather than /search?status=all&page=1
    return {
        "q": q or None,
        "status": status if status != DEFAULT_STATUS else None,
        "page": page if page > 1 else None,
    }


@search_bp.route("/search")
@limiter.limit(SEARCH_RATE_LIMIT)
@login_required
def search():
    q, status, page = search_params(request.args)

    conditions = []
    match = search_condition(q)
    if match is not None:
        conditions.append(match)
    if STATUS_FILTERS[status]:
        conditions.append(Package.status == STATUS_FILTERS[status])

    # Outer join: a package can have no recipient
    total = db.session.scalar(
        db.select(func.count(Package.id)).outerjoin(Package.recipient).where(*conditions)
    )
    page_count = max(1, math.ceil(total / PER_PAGE))
    page = min(page, page_count)

    packages = db.session.scalars(
        db.select(Package)
        .outerjoin(Package.recipient)
        # Loads each row's recipient from the same join: one query per page
        .options(contains_eager(Package.recipient))
        .where(*conditions)
        .order_by(Package.checked_in_at.desc(), Package.id.desc())
        .limit(PER_PAGE)
        .offset((page - 1) * PER_PAGE)
    ).all()

    # Only needed to choose the empty state
    any_packages = bool(packages) or db.session.scalar(db.select(Package.id).limit(1)) is not None

    return render_template(
        "search.html",
        q=q,
        status=status,
        packages=packages,
        # Rows whose photo file is really there; the rest show the placeholder
        with_photo={package.id for package in packages if photo_exists(package.location_photo_path)},
        total=total,
        page=page,
        page_count=page_count,
        first_shown=(page - 1) * PER_PAGE + 1,
        last_shown=(page - 1) * PER_PAGE + len(packages),
        any_packages=any_packages,
        # Each row's link carries the search, so the detail page can come back to it
        detail_args=search_link_args(q, status, page),
    )


@search_bp.route("/packages/<int:package_id>")
@login_required
def package_detail(package_id):
    package = db.get_or_404(Package, package_id)
    # The back button returns to the same search. Only q, status and page are
    # accepted, cleaned like the search page does, and the link is built here.
    q, status, page = search_params(request.args)
    return render_template(
        "package_detail.html",
        package=package,
        waiting=package.status == CHECKED_IN_STATUS,
        storage_photo=storage_photo_state(package),
        back_url=url_for("search.search", **search_link_args(q, status, page)),
    )
