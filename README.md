# Captain's Mail

A mobile-friendly Flask web app for the college mailroom. It replaces the old
laptop-and-barcode-scanner setup. Staff use their phones to scan shipping labels
when checking packages in and out, and photograph where each package is stored
so it can be found quickly later.

**Stack:** Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF, Flask-Limiter,
SQLite, Pillow, Jinja2 + Bootstrap, html5-qrcode for in-browser barcode scanning.

## Features

- **Check in:** scan a label (or type the number), confirm the carrier, and
  record the recipient's name, box number, and notes.
- **Storage photo:** after check-in, photograph the shelf or bin. The phone
  shrinks the photo before uploading; the server checks it is a real image,
  strips its metadata (including GPS), and stores it privately.
- **Check out:** scan the label to find the package, see where it's stored,
  optionally record the last 4 digits of the student ID, and mark it picked up.
- **Home and tabs:** a home screen with Check In / Check Out tiles, today's
  counts, and recent activity, and a bottom tab bar (Home, Check In, Check
  Out, Find, Account) on the main screens.
- **Search:** find packages by tracking number (the last few digits are
  enough), recipient name, or box number, filtered by status. Each result has
  a thumbnail and opens a detail page with the full photo and history.
- **Accounts:** staff and admin roles, an admin-only Manage Staff page
  (create, deactivate, unlock, promote, reset passwords), a Change Password
  page, password rules, and a 15-minute lockout after 5 wrong passwords.
- **Audit log:** an admin-only page (Manage staff → Audit log) listing sign-ins,
  account changes, and package check-ins, pickups and photos, newest first.
- **Security:** staff sign-in, CSRF protection, session timeouts, security
  headers and a strict Content Security Policy, rate limits on sign-in and
  search, and friendly error pages. All libraries are served from this site.

The look and components are described in
[`docs/design-system.md`](docs/design-system.md).

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
copy .env.example .env         # then set a real SECRET_KEY in .env
```

### Settings (`.env`)

| Setting | Values | What it does |
|---|---|---|
| `APP_ENV` | `production` (default) or `development` | `production` turns on Secure cookies and HSTS, and refuses to start without a real `SECRET_KEY`. Use `development` for local testing over plain HTTP. |
| `SECRET_KEY` | long random string | Signs session cookies. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `FLASK_DEBUG` | `0` (default) or `1` | Debug mode: auto-reload and the in-browser debugger. Never `1` on a real server. |
| `APP_TIMEZONE` | a time zone name, default `America/New_York` | Where "today" starts for the home screen's counts. |
| `TRUST_PROXY` | `0` (default) or `1` | Set to `1` behind a host that terminates HTTPS and forwards requests (PythonAnywhere). The app then trusts one proxy's `X-Forwarded-For`, `-Proto` and `-Host` headers, so each user's real IP address is used for the sign-in rate limit and the audit log. Leave it `0` when nothing is in front of the app, because the headers could be faked. |

In production the app refuses to start if `SECRET_KEY` is missing or a
placeholder, if debug mode is on, or if secure cookies are off, and it logs
which checks passed (never the key). Settings are read from the environment
first, then from the project's `.env`, wherever the app is started from.

Staff are signed out after 30 minutes of inactivity, or 12 hours after signing in.

## Accounts

Create the first admin from the command line, then add everyone else on the
Manage Staff page (Account → Manage staff):

```bash
flask --app run create-user <username> --admin   # an admin
flask --app run create-user <username>           # ordinary staff
```

You'll be prompted for the password (typed twice, hidden). Passwords need at
least 12 characters, can't be one of about 100 common passwords, and can't
contain the username. There are no symbol or digit requirements. (`create-staff`
still works as the old name for `create-user`.)

On Manage Staff, admins create accounts, deactivate and reactivate them, unlock
locked ones, promote and demote, and reset passwords. A new or reset account
gets a random temporary password, shown once; the person must choose their own
at first sign-in. You can't demote or deactivate yourself, and the last active
admin can never be removed. After 5 wrong passwords in a row an account locks
for 15 minutes (an admin can unlock it sooner). Every sign-in failure shows the
same message, so the page doesn't reveal which usernames exist.

### When the models change: reset the local database

`db.create_all()` only creates missing tables; it never adds a column to a
table that already exists. After pulling a change to `app/models.py` (this
one added columns to `Staff`), reset the local database:

```bash
flask --app run reset-db
flask --app run create-user <username> --admin
```

`reset-db` shows which database it will erase and asks you to confirm. It
**deletes every account and package record**; photos in `instance/uploads/` are
not touched. If you want to keep the old data, copy `instance/captains_mail.db`
somewhere safe first. Use `--yes` to skip the question.

### Tables and backups

The app creates any missing tables each time it starts. To do it explicitly and
see what happened:

```bash
flask --app run init-db
```

To back up the database and the photos:

```bash
flask --app run backup
```

This writes `instance/backups/captains_mail_YYYYMMDD_HHMMSS.zip` (UTC time)
with a consistent copy of the SQLite database, made with SQLite's own backup
API so it's safe while the app runs, plus the photo folder. The 5 newest
backups are kept and older ones are deleted. Download a backup to keep a copy
off the server.

## Deploying

See [`docs/deployment.md`](docs/deployment.md) for PythonAnywhere.

## Audit log

Admins can read the log at Account → Manage staff → Audit log: newest first,
50 per page, filtered by event type, with times in `APP_TIMEZONE`. It records
who did what (sign-ins and failures, lockouts, password changes, account and
role changes, and package check-ins, pickups and photo uploads), the account's
name at the time, the IP address, and a short target such as the last 8
characters of a tracking number. It never records passwords, student IDs,
recipient names or notes.

Delete old events (365 days by default) with a confirmation question:

```bash
flask --app run purge-audit --days 365
```

Add `--yes` to skip the question.

## Student IDs

Check-out stores only the **last 4 digits** of a student ID (exactly 4 digits,
or blank). To shorten IDs saved before this change, preview first, then apply:

```bash
flask --app run trim-student-ids           # dry run: shows how many would change
flask --app run trim-student-ids --apply   # shortens them
```

The shortening can't be undone, so copy `instance/captains_mail.db` first if
you want a backup.

## Run

```bash
python run.py
```

Then open http://localhost:5000.

To test from a phone on the same Wi-Fi, use HTTPS (phones only allow the camera
on secure pages). Install `cryptography` once, then start the server with a
temporary certificate:

```bash
venv\Scripts\python.exe -m pip install cryptography
venv\Scripts\python.exe -m flask --app run run --host 0.0.0.0 --port 5000 --cert adhoc
```

Either `APP_ENV` works here, since the page is served over HTTPS. Set
`FLASK_DEBUG=1` if you want auto-reload.

Open the `https://<your-computer's-IP>:5000` address it prints on your phone,
and accept the certificate warning (Advanced → Proceed).

## Tests

```bash
venv\Scripts\python.exe -m pytest
```

The tests use an in-memory database and temporary folders, so they never touch
your real data.

## Screen audit (development only)

`scripts/audit_screens.py` opens every signed-in page in a headless browser at
five phone and tablet sizes, in light and dark mode. It reports horizontal
scrolling, tap targets under 44 x 44 px, content stuck behind the bottom bars,
clipped text, and accessibility problems (axe-core), and saves screenshots.
It uses its own temporary database, so it never touches `instance/`.

The tools it needs are in `requirements-dev.txt`, not `requirements.txt`:

```bash
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m playwright install chromium
venv\Scripts\python.exe scripts/audit_screens.py
```

Add `--quick` for one phone size in light mode. Screenshots and the report
(`report.md`) go to `audit-screenshots/`, which is git-ignored. The camera
can't be tested this way; on the scan pages the audit checks that the "no
camera" message appears instead.

## Data

Everything the app stores lives in `instance/`, which is not tracked by git:

- `instance/captains_mail.db`: the SQLite database, created on first run.
- `instance/uploads/`: storage photos, with random file names.
- `instance/uploads/thumbs/`: thumbnails for search results, made on first view.

Photos are only served to signed-in staff, through `/photos/...`, never as
public static files.
