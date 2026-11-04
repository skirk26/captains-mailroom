# Storage-location photos: checks and re-encodes uploads, stores them in
# instance/uploads/ (not publicly served), and serves them to signed-in staff.
import contextlib
import os
import re
import tempfile
import uuid

from flask import Blueprint, abort, current_app, send_from_directory
from flask_login import login_required
from PIL import Image, ImageOps, UnidentifiedImageError

photos_bp = Blueprint("photos", __name__)

# Pillow format names. Only these decoders are tried; the upload's file name
# and Content-Type are never looked at.
ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
MAX_PIXELS = 50_000_000
MAX_SIDE = 1600  # same as checkin_photo.js
JPEG_QUALITY = 82
# Every stored photo is named <32 hex characters>.jpg; nothing else is served or deleted
PHOTO_NAME = re.compile(r"[0-9a-f]{32}\.jpg")

NOT_AN_IMAGE = "That file isn't a JPEG, PNG or WebP photo. Take the photo again."
TOO_MANY_PIXELS = "That photo is too large to use. Take the photo again."


class PhotoRejected(ValueError):
    """The upload isn't an image we accept. The message is safe to show staff."""


def prepare_photo(stream):
    """Decode an untrusted upload and return a clean, upright RGB image with
    its longest side at most MAX_SIDE. Raises PhotoRejected."""
    try:
        # open() only reads the header; no pixels are decoded yet
        with Image.open(stream, formats=ALLOWED_FORMATS) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise PhotoRejected(TOO_MANY_PIXELS)
            upright = ImageOps.exif_transpose(img)  # decodes the pixels
        rgb = flatten_to_rgb(upright)
        rgb.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    except PhotoRejected:
        raise
    except Image.DecompressionBombError as err:
        raise PhotoRejected(TOO_MANY_PIXELS) from err
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as err:
        # Not one of the allowed formats, or a damaged or truncated file
        raise PhotoRejected(NOT_AN_IMAGE) from err

    # Drop everything read from the original (EXIF, GPS, XMP, ICC, comments),
    # so the saved JPEG carries no metadata from the upload
    rgb.info = {}
    return rgb


def flatten_to_rgb(img):
    # Transparent areas become white rather than black
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def store_photo(img):
    """Save img as a new JPEG in UPLOAD_FOLDER and return its file name."""
    folder = current_app.config["UPLOAD_FOLDER"]
    filename = f"{uuid.uuid4().hex}.jpg"
    # Written to a temp file beside it and renamed, so a failed save never
    # leaves a partial photo under a real name
    fd, temp_path = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            img.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
        os.replace(temp_path, os.path.join(folder, filename))
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temp_path)
        raise
    return filename


def delete_photo(filename):
    # Only ever removes a file this module named; a missing file is fine
    if not filename or not PHOTO_NAME.fullmatch(filename):
        return
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
    except FileNotFoundError:
        pass
    except OSError:
        current_app.logger.warning("Couldn't delete old storage photo %s", filename, exc_info=True)


@photos_bp.route("/photos/<filename>")
@login_required
def photo(filename):
    # The strict name check means ../ and other path tricks can't reach any other file
    if not PHOTO_NAME.fullmatch(filename):
        abort(404)
    # 404 if the file doesn't exist
    response = send_from_directory(current_app.config["UPLOAD_FOLDER"], filename, mimetype="image/jpeg")
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "private"
    return response
