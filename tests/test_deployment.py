# Running on a WSGI host (PythonAnywhere): safe import, .env loading, proxy
# headers, production startup checks, init-db and backup.
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import request
from werkzeug.middleware.proxy_fix import ProxyFix

from app import ENV_FILE, create_app, db, limiter, load_env
from app.backup import KEEP_BACKUPS, BackupError, backups_folder, create_backup
from app.cli import backup, init_db
from app.models import AuditEvent, Staff

SECRET_KEY = "test-only-secret-key"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "test-only-password"
PHOTO_NAME = "a" * 32 + ".jpg"


def make_app(**config):
    return create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SECRET_KEY": SECRET_KEY,
        "APP_ENV": "production",
        "WTF_CSRF_ENABLED": False,
        **config,
    })


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    # A developer's shell or .env shouldn't change what the production checks see
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("TRUST_PROXY", raising=False)


# ---- Importing the app on a WSGI host ----


@pytest.fixture
def project_copy(tmp_path):
    """A copy of the code in a temporary folder, so importing it can't touch the real instance/."""
    project = tmp_path / "project"
    project.mkdir()
    shutil.copytree(PROJECT_ROOT / "app", project / "app", ignore=shutil.ignore_patterns("__pycache__"))
    return project


def run_python(code, project, cwd, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "SECRET_KEY", "FLASK_DEBUG", "TRUST_PROXY", "APP_TIMEZONE")}
    env.update(env_extra or {})
    env["PYTHONPATH"] = str(project)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )


def test_import_from_another_working_directory(project_copy, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (project_copy / ".env").write_text("APP_ENV=production\nSECRET_KEY=from-the-dotenv-file-0123456789\n")
    result = run_python(
        "from app import create_app\n"
        "application = create_app()\n"
        "print(application.instance_path)\n"
        "print(application.config['SQLALCHEMY_DATABASE_URI'])\n"
        "print(application.config['UPLOAD_FOLDER'])\n"
        "print(application.config['THUMB_FOLDER'])\n",
        project_copy, elsewhere,
    )
    assert result.returncode == 0, result.stderr
    instance = str(project_copy / "instance")
    lines = result.stdout.splitlines()
    assert len(lines) == 4
    for line in lines:
        assert instance in line
    assert (project_copy / "instance" / "captains_mail.db").is_file()
    assert (project_copy / "instance" / "uploads" / "thumbs").is_dir()
    assert list(elsewhere.iterdir()) == []  # nothing written to the working directory


def test_importing_does_not_start_a_server(project_copy, tmp_path):
    (project_copy / "run.py").write_text((PROJECT_ROOT / "run.py").read_text())
    result = run_python(
        "import socket\n"
        "def refuse(*a, **k): raise AssertionError('a socket was opened on import')\n"
        "socket.socket.bind = refuse\n"
        "socket.socket.listen = refuse\n"
        "import run\n"
        "print('imported', run.app.name)\n",
        project_copy, tmp_path,
        env_extra={"APP_ENV": "development"},
    )
    assert result.returncode == 0, result.stderr
    assert "imported app" in result.stdout


def test_run_py_only_starts_a_server_under_main():
    code = (PROJECT_ROOT / "run.py").read_text()
    assert 'if __name__ == "__main__":' in code
    assert code.index('if __name__ == "__main__":') < code.index("app.run(")


# ---- .env loading ----


def test_env_file_is_the_project_roots_dotenv():
    assert ENV_FILE == PROJECT_ROOT / ".env"
    assert ENV_FILE.is_absolute()


def test_dotenv_is_found_from_the_code_not_the_working_directory(project_copy, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (project_copy / ".env").write_text("APP_ENV=development\nAPP_TIMEZONE=Europe/London\n")
    # A decoy .env in the working directory must not be read
    (elsewhere / ".env").write_text("APP_TIMEZONE=Asia/Tokyo\n")
    result = run_python(
        "from app import create_app\n"
        "print(create_app().config['APP_TIMEZONE'])\n",
        project_copy, elsewhere,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Europe/London"


def test_variables_already_set_win_over_dotenv(project_copy, tmp_path):
    (project_copy / ".env").write_text("APP_ENV=development\nAPP_TIMEZONE=Europe/London\n")
    result = run_python(
        "from app import create_app\n"
        "print(create_app().config['APP_TIMEZONE'])\n",
        project_copy, tmp_path,
        env_extra={"APP_TIMEZONE": "America/Chicago"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "America/Chicago"


def test_load_env_does_not_override_and_ignores_a_missing_file(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("DEPLOY_TEST_SET=from-file\nDEPLOY_TEST_NEW=from-file\n")
    monkeypatch.setenv("DEPLOY_TEST_SET", "from-environment")
    monkeypatch.delenv("DEPLOY_TEST_NEW", raising=False)
    try:
        load_env(dotenv)
        assert os.environ["DEPLOY_TEST_SET"] == "from-environment"
        assert os.environ["DEPLOY_TEST_NEW"] == "from-file"
    finally:
        os.environ.pop("DEPLOY_TEST_NEW", None)
    load_env(tmp_path / "missing.env")  # no error


# ---- Paths and folder permissions ----


def test_instance_paths_come_from_instance_path():
    app = create_app({"TESTING": True, "SECRET_KEY": SECRET_KEY, "APP_ENV": "production",
                      "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    instance = Path(app.instance_path)
    assert instance == PROJECT_ROOT / "instance"
    assert Path(app.config["UPLOAD_FOLDER"]) == instance / "uploads"
    assert Path(app.config["THUMB_FOLDER"]) == instance / "uploads" / "thumbs"


@pytest.mark.skipif(os.name != "posix", reason="Unix permissions only")
def test_instance_folder_is_owner_only(project_copy, tmp_path):
    result = run_python("from app import create_app\ncreate_app()\n", project_copy, tmp_path,
                        env_extra={"APP_ENV": "development"})
    assert result.returncode == 0, result.stderr
    assert (project_copy / "instance").stat().st_mode & 0o777 == 0o700


# ---- TRUST_PROXY ----


def add_probe(app):
    app.add_url_rule("/_probe", "probe", lambda: f"{request.remote_addr} {request.scheme} {request.host}")


def probe(app, **headers):
    client = app.test_client()
    return client.get("/_probe", headers=headers, base_url="http://internal.local").get_data(as_text=True)


FORWARDED = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "mail.example.com"}


def test_trust_proxy_is_off_by_default():
    app = make_app()
    assert app.config["TRUST_PROXY"] is False
    assert not isinstance(app.wsgi_app, ProxyFix)
    add_probe(app)
    assert probe(app, **FORWARDED) == "127.0.0.1 http internal.local"  # headers ignored


def test_trust_proxy_reads_the_forwarded_headers():
    app = make_app(TRUST_PROXY=True)
    assert isinstance(app.wsgi_app, ProxyFix)
    add_probe(app)
    assert probe(app, **FORWARDED) == "203.0.113.9 https mail.example.com"


@pytest.mark.parametrize("value, expected", [("1", True), (" 1 ", True), ("0", False), ("", False), ("true", False)])
def test_trust_proxy_setting_is_only_on_for_1(monkeypatch, value, expected):
    monkeypatch.setenv("TRUST_PROXY", value)
    assert make_app().config["TRUST_PROXY"] is expected


def failed_login(client, ip):
    return client.post(
        "/login", data={"username": "nobody", "password": "wrong password here"},
        headers={"X-Forwarded-For": ip},
    )


def test_rate_limit_is_per_client_behind_a_trusted_proxy():
    limiter.enabled = True
    limiter.reset()
    client = make_app(TRUST_PROXY=True).test_client()
    for _ in range(5):
        assert failed_login(client, "203.0.113.1").status_code == 200
    assert failed_login(client, "203.0.113.1").status_code == 429
    assert failed_login(client, "203.0.113.2").status_code == 200  # a different person
    limiter.reset()


def test_rate_limit_is_shared_without_the_proxy_setting():
    limiter.enabled = True
    limiter.reset()
    client = make_app().test_client()
    for _ in range(5):
        assert failed_login(client, "203.0.113.1").status_code == 200
    # The header is ignored, so everyone looks like one address
    assert failed_login(client, "203.0.113.2").status_code == 429
    limiter.reset()


def test_audit_log_records_the_real_client_address():
    limiter.enabled = True
    limiter.reset()
    app = make_app(TRUST_PROXY=True)
    with app.app_context():
        failed_login(app.test_client(), "203.0.113.7")
        event = db.session.scalar(db.select(AuditEvent).filter_by(event_type="login_failure"))
        assert event.ip_address == "203.0.113.7"
    limiter.reset()


# ---- Production startup checks ----


def test_production_refuses_debug_mode(monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    with pytest.raises(RuntimeError, match="[Dd]ebug"):
        make_app()


def test_production_refuses_debug_set_in_config():
    with pytest.raises(RuntimeError, match="[Dd]ebug"):
        make_app(DEBUG=True)


@pytest.mark.parametrize("setting", ["SESSION_COOKIE_SECURE", "REMEMBER_COOKIE_SECURE"])
def test_production_refuses_insecure_cookies(setting):
    with pytest.raises(RuntimeError, match="[Ss]ecure cookies"):
        make_app(**{setting: False})


def test_production_still_refuses_a_placeholder_secret_key():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        make_app(SECRET_KEY="change-me-to-a-long-random-string")


def test_development_allows_debug(monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    app = make_app(APP_ENV="development")
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_production_logs_the_checks_without_the_secret(caplog):
    with caplog.at_level(logging.INFO, logger="app"):
        make_app()
    passed = [r for r in caplog.records if r.levelno == logging.INFO and "startup checks passed" in r.getMessage()]
    assert len(passed) == 1
    text = passed[0].getMessage()
    for expected in ("SECRET_KEY set", "debug off", "secure cookies on", "proxy headers"):
        assert expected in text
    assert SECRET_KEY not in caplog.text


def test_production_logger_shows_info_by_default():
    assert make_app().logger.isEnabledFor(logging.INFO)


# ---- init-db ----


def test_init_db_reports_what_startup_created():
    app = make_app()
    assert {"staff", "package", "recipient", "audit_event"} <= set(app.config["TABLES_CREATED_AT_STARTUP"])
    result = app.test_cli_runner().invoke(init_db)
    assert result.exit_code == 0, result.output
    assert "Created table(s):" in result.output
    for table in ("staff", "package", "recipient", "audit_event"):
        assert table in result.output


def test_init_db_creates_a_dropped_table_and_keeps_data():
    app = make_app()
    with app.app_context():
        staff = Staff(username="keeper")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.commit()
        AuditEvent.__table__.drop(db.engine)
    result = app.test_cli_runner().invoke(init_db)
    assert result.exit_code == 0, result.output
    assert "audit_event" in result.output
    with app.app_context():
        assert db.session.scalar(db.select(Staff).filter_by(username="keeper")) is not None
        assert db.session.scalar(db.select(db.func.count()).select_from(AuditEvent)) == 0


def test_init_db_when_nothing_is_missing():
    app = make_app()
    app.config["TABLES_CREATED_AT_STARTUP"] = []  # as if an earlier start made them
    result = app.test_cli_runner().invoke(init_db)
    assert result.exit_code == 0, result.output
    assert "all already existed" in result.output


# ---- backup ----


@pytest.fixture
def file_app(tmp_path):
    """An app on a real SQLite file and a photo folder, like production."""
    uploads = tmp_path / "uploads"
    app = make_app(
        SQLALCHEMY_DATABASE_URI="sqlite:///" + str(tmp_path / "live.db"),
        UPLOAD_FOLDER=str(uploads),
        THUMB_FOLDER=str(uploads / "thumbs"),
    )
    app.instance_path = str(tmp_path / "instance")  # backups go here, not in the real instance/
    with app.app_context():
        staff = Staff(username="backed-up")
        staff.set_password(PASSWORD)
        db.session.add(staff)
        db.session.commit()
        (uploads / PHOTO_NAME).write_bytes(b"photo-bytes")
        (uploads / "thumbs" / PHOTO_NAME).write_bytes(b"thumb-bytes")
        (uploads / "half.tmp").write_bytes(b"unfinished")
        yield app
        db.session.remove()
        db.engine.dispose()


def test_backup_writes_a_zip_with_a_readable_database_and_the_photos(file_app, tmp_path):
    result = file_app.test_cli_runner().invoke(backup)
    assert result.exit_code == 0, result.output
    folder = tmp_path / "instance" / "backups"
    zips = list(folder.glob("captains_mail_*.zip"))
    assert len(zips) == 1
    assert zips[0].name in result.output
    assert [p.name for p in folder.iterdir()] == [zips[0].name]  # no partial file or scratch folder left

    with zipfile.ZipFile(zips[0]) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "captains_mail.db", f"uploads/{PHOTO_NAME}", f"uploads/thumbs/{PHOTO_NAME}",
        }
        assert archive.read(f"uploads/{PHOTO_NAME}") == b"photo-bytes"
        restored = tmp_path / "restored"
        archive.extract("captains_mail.db", restored)
    conn = sqlite3.connect(restored / "captains_mail.db")
    try:
        assert conn.execute("select username from staff").fetchall() == [("backed-up",)]
        assert conn.execute("pragma integrity_check").fetchone() == ("ok",)
    finally:
        conn.close()


def test_backup_name_has_a_timestamp(file_app):
    with file_app.app_context():
        path, _, _ = create_backup(datetime(2026, 11, 27, 9, 5, 3, tzinfo=timezone.utc))
    assert path.name == "captains_mail_20261127_090503.zip"
    assert path.parent == Path(file_app.instance_path) / "backups"


def test_backup_keeps_only_the_newest_five(file_app):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with file_app.app_context():
        for day in range(7):
            create_backup(start + timedelta(days=day))
        names = sorted(p.name for p in backups_folder().glob("*.zip"))
    assert KEEP_BACKUPS == 5
    assert names == [f"captains_mail_2026010{day}_000000.zip" for day in range(3, 8)]


def test_backup_prune_only_touches_its_own_files(file_app):
    with file_app.app_context():
        folder = backups_folder()
        folder.mkdir(parents=True)
        for name in ("notes.txt", "captains_mail_old.zip", "other_20200101_000000.zip"):
            (folder / name).write_text("keep me")
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for day in range(6):
            create_backup(start + timedelta(days=day))
        remaining = {p.name for p in folder.iterdir()}
    assert {"notes.txt", "captains_mail_old.zip", "other_20200101_000000.zip"} <= remaining
    assert len([n for n in remaining if n.startswith("captains_mail_2026")]) == 5


def test_backup_never_lands_in_static_and_is_gitignored():
    app = make_app()
    with app.app_context():
        assert Path(app.static_folder) not in backups_folder().parents
        assert backups_folder() == Path(app.instance_path) / "backups"
    assert "instance/backups/" in (PROJECT_ROOT / ".gitignore").read_text().splitlines()


def test_backup_refuses_an_in_memory_database():
    result = make_app().test_cli_runner().invoke(backup)
    assert result.exit_code != 0
    assert "SQLite file" in result.output


def test_backup_does_not_overwrite_the_same_second(file_app):
    when = datetime(2026, 5, 5, 5, 5, 5, tzinfo=timezone.utc)
    with file_app.app_context():
        create_backup(when)
        with pytest.raises(BackupError, match="already exists"):
            create_backup(when)


def test_failed_backup_leaves_no_partial_file(file_app, monkeypatch):
    def explode(archive):
        raise OSError("disk full")

    monkeypatch.setattr("app.backup.add_uploads", explode)
    with file_app.app_context():
        with pytest.raises(OSError):
            create_backup()
        assert list(backups_folder().iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="Unix permissions only")
def test_backups_folder_is_owner_only(file_app):
    with file_app.app_context():
        create_backup()
        assert backups_folder().stat().st_mode & 0o777 == 0o700
