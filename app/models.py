from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db, login_manager


def utc_now():
    return datetime.now(timezone.utc)


class Staff(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<Staff {self.username}>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Staff, int(user_id))


class Recipient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Names are optional: a label may only show a box number
    first_name = db.Column(db.String(60), nullable=True)
    last_name = db.Column(db.String(60), nullable=True)
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
    tracking_number = db.Column(db.String(64), unique=True, nullable=False)
    carrier = db.Column(db.String(20), nullable=False)
    # Optional until Save decides how form fields map onto Recipient rows
    recipient_id = db.Column(db.Integer, db.ForeignKey("recipient.id"), nullable=True)
    checked_in_by = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    checked_out_by = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="checked_in")
    location_photo_path = db.Column(db.String(255), nullable=True)
    checked_in_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    checked_out_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    recipient = db.relationship("Recipient", back_populates="packages")
    # Two FKs point at Staff, so each relationship names the column it uses
    checked_in_staff = db.relationship("Staff", foreign_keys=[checked_in_by])
    checked_out_staff = db.relationship("Staff", foreign_keys=[checked_out_by])

    def __repr__(self):
        return f"<Package {self.carrier} {self.tracking_number} ({self.status})>"
