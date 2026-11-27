import click
from flask import current_app
from flask.cli import with_appcontext

from app import db
from app.models import Staff
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
