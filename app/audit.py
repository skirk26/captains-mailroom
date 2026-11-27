# The audit log: log_event() records who did what.
#
# Only ever pass short, non-sensitive text. Never a password, token, student
# ID, recipient name or note. The details column is for things like a carrier
# or a role, and a tracking number is shortened to its last 8 characters.
from flask import current_app, has_request_context, request
from flask_login import current_user

from app import db
from app.models import AuditEvent, Staff

TARGET_MAX_LENGTH = 100
DETAILS_MAX_LENGTH = 200
LOGIN_NAME_MAX_LENGTH = 50  # a mistyped username, cut short in the log

# Every event type, with the label the audit page shows
EVENT_TYPES = {
    "login_success": "Sign in",
    "login_failure": "Failed sign in",
    "account_locked": "Account locked",
    "logout": "Sign out",
    "password_changed": "Password changed",
    "account_created": "Account created",
    "account_deactivated": "Account deactivated",
    "account_reactivated": "Account reactivated",
    "account_unlocked": "Account unlocked",
    "role_changed": "Role changed",
    "password_reset": "Password reset",
    "package_checked_in": "Package checked in",
    "package_checked_out": "Package checked out",
    "photo_uploaded": "Photo uploaded",
}


def clip(text, limit):
    if text is None:
        return None
    text = str(text)
    return text[:limit]


def tracking_target(tracking_number):
    """The last 8 characters of a tracking number: enough to find it, not the whole label."""
    return tracking_number[-8:] if tracking_number else None


def log_event(event_type, target=None, details=None, *, staff=None):
    """Write one audit event. Never raises, so a logging problem can't break the action.

    Call it after the action's own commit. The acting account is the signed-in
    user unless `staff` is given. It uses its own commit; a failure is rolled
    back and written to the server log only.
    """
    try:
        user = staff
        if user is None and has_request_context():
            candidate = current_user._get_current_object()
            user = candidate if isinstance(candidate, Staff) else None
        event = AuditEvent(
            event_type=event_type,
            target=clip(target, TARGET_MAX_LENGTH),
            details=clip(details, DETAILS_MAX_LENGTH),
            staff_id=user.id if user else None,
            staff_username=user.username if user else None,
            ip_address=clip(request.remote_addr, 45) if has_request_context() else None,
        )
        db.session.add(event)
        db.session.commit()
    except Exception:  # noqa: BLE001 - logging must never break a check-in
        try:
            db.session.rollback()
            current_app.logger.exception("Couldn't write the %s audit event", event_type)
        except Exception:  # noqa: BLE001
            pass
