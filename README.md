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
  optionally record the student ID, and mark it picked up.
- **Home and tabs:** a home screen with Check In / Check Out tiles, today's
  counts, and recent activity, and a bottom tab bar (Home, Check In, Check
  Out, Find, Account) on the main screens.
- **Search:** find packages by tracking number (the last few digits are
  enough), recipient name, or box number, filtered by status. Each result has
  a thumbnail and opens a detail page with the full photo and history.
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

Staff are signed out after 30 minutes of inactivity, or 12 hours after signing in.

## Create a staff account

```bash
flask --app run create-staff <username>
```

You'll be prompted for the password (typed twice, hidden).

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
