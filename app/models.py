from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db, login_manager


# The largest id SQLite can store. Routes that take a package id use
# <PACKAGE_ID:package_id>, so a longer number gets the 404 page instead of
# an OverflowError (500) from SQLite.
SQLITE_MAX_INTEGER = 2**63 - 1
PACKAGE_ID = f"int(max={SQLITE_MAX_INTEGER})"


def utc_now():
    return datetime.now(timezone.utc)


# Five wrong passwords in a row lock the account for 15 minutes
MAX_FAILED_LOGINS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

# Checked when the username doesn't exist, so an unknown name costs the same
# time as a wrong password and the response doesn't give accounts away
DUMMY_PASSWORD_HASH = generate_password_hash("not-a-real-password")


def as_utc(when):
    # SQLite hands datetimes back without a time zone; they were stored as UTC
    return when if when is None or when.tzinfo else when.replace(tzinfo=timezone.utc)


class Staff(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    # server_default keeps rows made outside the ORM valid too
    is_admin = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    # Replaces UserMixin's is_active, which is always True. Flask-Login refuses
    # to sign in an inactive user; enforce_account_state ends an open session.
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=sa.true())
    failed_login_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    password_changed_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.password_changed_at = utc_now()

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_locked(self):
        return self.locked_until is not None and as_utc(self.locked_until) > utc_now()

    @property
    def role(self):
        return "Admin" if self.is_admin else "Staff"

    def register_failed_login(self):
        """Count a wrong password; the fifth in a row starts the lockout."""
        if self.is_locked:
            return  # attempts during a lockout don't extend it
        self.failed_login_count = (self.failed_login_count or 0) + 1
        if self.failed_login_count >= MAX_FAILED_LOGINS:
            self.locked_until = utc_now() + LOCKOUT_DURATION
            self.failed_login_count = 0  # a fresh five once the lockout ends

    def register_successful_login(self):
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = utc_now()

    def unlock(self):
        self.failed_login_count = 0
        self.locked_until = None

    def __repr__(self):
        return f"<Staff {self.username}>"


def active_admin_count():
    return db.session.scalar(
        db.select(db.func.count()).select_from(Staff).where(Staff.is_admin.is_(True), Staff.is_active.is_(True))
    )


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Staff, int(user_id))


class Recipient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Names are optional: a label may only show a box number
    first_name = db.Column(db.String(50), nullable=True)
    last_name = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(120))
    mailbox_number = db.Column(db.String(20))

    packages = db.relationship("Package", back_populates="recipient")

    @property
    def full_name(self):
        return " ".join(part for part in (self.first_name, self.last_name) if part)

    def __repr__(self):
        return f"<Recipient {self.full_name or '(no name)'} (box {self.mailbox_number or 'none'})>"


class Package(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Stored without spaces and uppercased, so scanned and typed numbers match
    tracking_number = db.Column(db.String(100), unique=True, nullable=False)
    carrier = db.Column(db.String(20), nullable=False)
    # Optional until Save decides how form fields map onto Recipient rows
    recipient_id = db.Column(db.Integer, db.ForeignKey("recipient.id"), nullable=True)
    checked_in_by = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    checked_out_by = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="checked_in")
    location_photo_path = db.Column(db.String(255), nullable=True)
    checked_in_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    checked_out_at = db.Column(db.DateTime, nullable=True)
    # Student ID of whoever collected it, if staff typed one in
    checked_out_to = db.Column(db.String(20), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    recipient = db.relationship("Recipient", back_populates="packages")
    # Two FKs point at Staff, so each relationship names the column it uses
    checked_in_staff = db.relationship("Staff", foreign_keys=[checked_in_by])
    checked_out_staff = db.relationship("Staff", foreign_keys=[checked_out_by])

    def __repr__(self):
        return f"<Package {self.carrier} {self.tracking_number} ({self.status})>"
