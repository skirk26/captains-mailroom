from datetime import timedelta

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import inspect as sa_inspect

from app import create_missing_tables, db
from app.backup import KEEP_BACKUPS, BackupError, create_backup
from app.audit import log_event
from app.models import AuditEvent, Package, Staff, utc_now
from app.passwords import password_problems, username_problem

PASSWORD_ATTEMPTS = 3


def ask_for_password(username):
    """Prompt twice with hidden input, asking again if a password breaks the rules."""
    for _ in range(PASSWORD_ATTEMPTS):
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        problems = password_problems(password, username)
        if not problems:
            return password
        for problem in problems:
            click.echo(f"  {problem}", err=True)
    raise click.ClickException("No acceptable password entered; nothing was created.")


@click.command("create-user")
@click.argument("username")
@click.option("--admin", is_flag=True, help="Make this account an admin.")
@with_appcontext
def create_user(username, admin):
    """Create a staff account (or an admin with --admin)."""
    problem = username_problem(username)
    if problem:
        raise click.ClickException(problem)
    if db.session.scalar(db.select(Staff).filter_by(username=username)):
        raise click.ClickException(f"Staff user '{username}' already exists.")

    staff = Staff(username=username, is_admin=admin)
    staff.set_password(ask_for_password(username))
    db.session.add(staff)
    db.session.commit()
    log_event("account_created", username, f"role: {staff.role.lower()}, from the command line")
    click.echo(f"Created {'admin' if admin else 'staff'} user '{username}'.")


@click.command("reset-db")
@click.option("--yes", is_flag=True, help="Skip the confirmation question.")
@with_appcontext
def reset_db(yes):
    """Erase the database and recreate empty tables from the current models.

    db.create_all() never alters an existing table, so run this after the
    models gain a column. Every account and package record is lost; photo
    files in instance/uploads/ are not touched.
    """
    click.echo(f"Database: {current_app.config['SQLALCHEMY_DATABASE_URI']}")
    if not yes:
        click.confirm("This ERASES every account and package record. Continue?", abort=True)
    db.drop_all()
    db.create_all()
    click.echo("Database reset. Create an admin with: flask --app run create-user <username> --admin")


@click.command("purge-audit")
@click.option("--days", default=365, show_default=True, type=click.IntRange(min=1),
              help="Delete audit events older than this many days.")
@click.option("--yes", is_flag=True, help="Skip the confirmation question.")
@with_appcontext
def purge_audit(days, yes):
    """Delete audit events older than --days (default 365), after a confirmation."""
    cutoff = utc_now() - timedelta(days=days)
    old = AuditEvent.timestamp < cutoff
    count = db.session.scalar(db.select(db.func.count()).select_from(AuditEvent).where(old))
    if not count:
        click.echo(f"No audit events are older than {days} days.")
        return
    if not yes:
        click.confirm(f"Permanently delete {count} audit event(s) older than {days} days?", abort=True)
    db.session.execute(db.delete(AuditEvent).where(old))
    db.session.commit()
    click.echo(f"Deleted {count} audit event(s).")


@click.command("trim-student-ids")
@click.option("--apply", "apply_changes", is_flag=True,
              help="Actually change the data. Without it, only report what would change.")
@with_appcontext
def trim_student_ids(apply_changes):
    """Shorten stored student IDs to their last 4 digits. A dry run unless --apply is given."""
    rows = db.session.scalars(db.select(Package).where(Package.checked_out_to.is_not(None))).all()
    to_change = []
    for package in rows:
        value = package.checked_out_to.strip()
        trimmed = value[-4:] if len(value) > 4 else value
        if trimmed != package.checked_out_to:
            to_change.append((package, trimmed))

    click.echo(f"{len(rows)} package(s) have a student ID; {len(to_change)} would be shortened to the last 4 digits.")
    if not apply_changes:
        click.echo("Dry run: nothing was changed. Run again with --apply to shorten them.")
        return
    for package, trimmed in to_change:
        package.checked_out_to = trimmed
    db.session.commit()
    click.echo(f"Shortened {len(to_change)} student ID(s).")


@click.command("init-db")
@with_appcontext
def init_db():
    """Create any missing database tables and say what was done.

    The app also does this every time it starts. Existing tables are never
    changed; after a model gains a column, use reset-db (which erases data).
    """
    created = list(current_app.config.get("TABLES_CREATED_AT_STARTUP", []))
    created += create_missing_tables()
    tables = sorted(sa_inspect(db.engine).get_table_names())
    click.echo(f"Database: {current_app.config['SQLALCHEMY_DATABASE_URI']}")
    if created:
        click.echo(f"Created table(s): {', '.join(sorted(created))}.")
    else:
        click.echo("No tables were created: they all already existed.")
    click.echo(f"Tables now: {', '.join(tables)}.")
    click.echo("Existing tables are never altered.")


@click.command("backup")
@with_appcontext
def backup():
    """Back up the database and photos to instance/backups/ (keeps the newest 5)."""
    try:
        path, photos, removed = create_backup()
    except BackupError as err:
        raise click.ClickException(str(err)) from err
    click.echo(f"Backup written: {path} ({path.stat().st_size / 1024:.0f} KB, {photos} photo file(s)).")
    if removed:
        click.echo(f"Removed {len(removed)} older backup(s); the newest {KEEP_BACKUPS} are kept.")
