# Backups for `flask backup`: one zip holding a consistent copy of the SQLite
# database and the photo folder, written to instance/backups/ (never under
# static/, so it can't be served) with only the newest few kept.
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from app import db

KEEP_BACKUPS = 5
BACKUP_NAME = re.compile(r"captains_mail_\d{8}_\d{6}\.zip")
DATABASE_NAME_IN_ZIP = "captains_mail.db"
UPLOADS_NAME_IN_ZIP = "uploads"


class BackupError(Exception):
    """Something stops a backup from being made. The message is safe to show."""


def backups_folder():
    return Path(current_app.instance_path) / "backups"


def database_file():
    url = db.engine.url
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        raise BackupError("The database isn't a SQLite file, so there is nothing to copy.")
    path = Path(url.database)
    if not path.is_file():
        raise BackupError(f"The database file {path} doesn't exist.")
    return path


def copy_database(source, destination):
    # sqlite3's backup API takes a consistent snapshot even while the app is
    # writing; copying the file itself could catch it half-written
    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        destination_conn = sqlite3.connect(destination)
        try:
            source_conn.backup(destination_conn)
        finally:
            destination_conn.close()
    finally:
        source_conn.close()


def add_uploads(archive):
    uploads = Path(current_app.config["UPLOAD_FOLDER"])
    if not uploads.is_dir():
        return 0
    count = 0
    for path in sorted(uploads.rglob("*")):
        # Half-written temp files from an upload in progress aren't photos
        if path.is_file() and path.suffix != ".tmp":
            archive.write(path, f"{UPLOADS_NAME_IN_ZIP}/{path.relative_to(uploads).as_posix()}")
            count += 1
    return count


def prune(folder, keep=KEEP_BACKUPS):
    """Delete all but the newest `keep` backups and return the names removed.
    Only files named exactly like our own backups are ever touched."""
    names = sorted(entry.name for entry in folder.iterdir() if entry.is_file() and BACKUP_NAME.fullmatch(entry.name))
    removed = names[:-keep] if keep else names
    for name in removed:
        (folder / name).unlink()
    return removed


def create_backup(now=None):
    """Write a new backup zip. Returns (path, photo_count, removed_names)."""
    source = database_file()
    folder = backups_folder()
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(folder, 0o700)

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    final = folder / f"captains_mail_{stamp}.zip"
    if final.exists():
        raise BackupError(f"{final.name} already exists. Wait a second and try again.")

    partial = folder / f"{final.name}.part"
    try:
        with tempfile.TemporaryDirectory(dir=folder) as scratch:
            snapshot = Path(scratch) / DATABASE_NAME_IN_ZIP
            copy_database(source, snapshot)
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, DATABASE_NAME_IN_ZIP)
                photos = add_uploads(archive)
        # Renamed only once complete, so a failed backup never looks like a good one
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return final, photos, prune(folder)
