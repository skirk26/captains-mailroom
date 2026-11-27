# Captain's Mail project structure

This document describes each file in the Captain's Mail repository and how the
files work together. Use it to find where a feature lives before you change it.

Captain's Mail is a Flask web app for a college mailroom. Staff use their phones
to scan shipping labels, check packages in and out, photograph where each package
is stored, and search for packages later. Admins manage staff accounts and read
an audit log.

For setup, settings, and the command-line tools, see [`README.md`](README.md).
For the look of the app, see [`docs/design-system.md`](docs/design-system.md).

## Directory layout

```text
captains-mail/
├── .env.example
├── .gitignore
├── README.md
├── project-structure.md
├── requirements.txt
├── requirements-dev.txt
├── run.py
├── app/
│   ├── __init__.py
│   ├── audit.py
│   ├── backup.py
│   ├── cli.py
│   ├── common_passwords.txt
│   ├── icons.py
│   ├── models.py
│   ├── passwords.py
│   ├── photos.py
│   ├── security.py
│   ├── routes/
│   │   ├── admin.py
│   │   ├── auth.py
│   │   ├── checkin.py
│   │   ├── checkout.py
│   │   ├── dashboard.py
│   │   ├── errors.py
│   │   └── search.py
│   ├── static/
│   │   ├── css/        tokens.css, components.css, style.css
│   │   ├── js/         one script per screen (see below)
│   │   ├── uploads/    an unused folder kept from the first version
│   │   └── vendor/     self-hosted Bootstrap, Bootstrap Icons, html5-qrcode
│   └── templates/      18 Jinja templates (see below)
├── docs/
│   ├── deployment.md
│   ├── design-system.md
│   └── cleanup_report.md
├── scripts/
│   └── audit_screens.py
└── tests/              one test file per area (see below)
```

At run time the app also creates `instance/` (git-ignored). It holds the SQLite
database, `captains_mail.db`, and the storage photos in `instance/uploads/`.

## How a check-in works

1.  A staff member signs in on `login.html`, handled by `routes/auth.py`.
1.  On the home screen (`dashboard.html`), they open the Check In tab.
1.  `checkin.html` (built on `_scan_page.html`) opens the camera. `scanner.js`
    decodes the barcode and opens the confirm page with the tracking number in
    the URL. If the label can't be scanned, `manual_entry.js` lets them type
    the number instead.
1.  `checkin_confirm.html` shows the tracking number and a suggested carrier.
    As they type a name or box number, `recipient_search.js` calls
    `/api/recipients/search` to suggest recipients entered before.
1.  On **Save**, `routes/checkin.py` validates the form, finds or creates the
    recipient, saves the package, and writes a `package_checked_in` audit event.
1.  `checkin_photo.html` asks for a photo of the storage spot. `checkin_photo.js`
    shrinks it in the browser and uploads it; `photos.py` checks that it is a
    real image, strips its metadata, and stores it privately. Staff can skip it.
1.  The app returns to the scanner with a brief toast, ready for the next package.

Check-out is similar: `checkout.html` scans the label, `checkout_confirm.html`
shows the package and where it is stored, and the POST in `routes/checkout.py`
marks it picked up.

## Root files

| File | What it is |
|---|---|
| `.env.example` | Template for `.env`: `APP_ENV`, `APP_TIMEZONE`, `SECRET_KEY`, `FLASK_DEBUG`, `TRUST_PROXY`. Copy it to `.env`. Git ignores `.env`. |
| `.gitignore` | Ignores `venv/`, `instance/`, caches, `.env`, `*.db`, uploads in `app/static/uploads/`, and `audit-screenshots/`. |
| `README.md` | Setup, settings, accounts, audit log, student IDs, running, and tests. |
| `requirements.txt` | The app's Python packages, pinned to exact versions: Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF, Flask-Limiter, python-dotenv, Pillow, and tzdata. |
| `requirements-dev.txt` | Development-only tools (pytest, playwright, axe-playwright-python). It includes `requirements.txt`. |
| `run.py` | Creates the app with `create_app()` and starts the development server on port 5000, with debug mode only if `FLASK_DEBUG=1`. It is also the target of `flask --app run`. |

## The `app` package

| File | What it does |
|---|---|
| `__init__.py` | The app factory, `create_app(test_config=None)`. It defines the shared `db`, `login_manager`, `csrf`, and `limiter` objects; sets the cookie, upload-size, and timeout settings; loads `.env` from the project root; refuses to start in production without a real `SECRET_KEY`, with debug on, or with insecure cookies; trusts proxy headers when `TRUST_PROXY=1`; adds the HSTS header in production; enforces the 30-minute idle and 12-hour absolute session limits; ends the session of a deactivated account and redirects to Change Password when one is required; registers the blueprints and CLI commands; and creates missing tables. Tests pass `test_config` to use an in-memory database. |
| `models.py` | The tables: `Staff`, `Recipient`, `Package`, and `AuditEvent`. It also holds the `load_user` callback for Flask-Login, the lockout rules, and `utc_now()`. Times are stored as UTC. |
| `cli.py` | The `flask` commands: `create-user` (alias `create-staff`), `init-db`, `backup`, `reset-db`, `purge-audit`, and `trim-student-ids`. |
| `backup.py` | What `flask backup` does: a zip of a consistent SQLite copy and the photos in `instance/backups/`, keeping the newest 5. |
| `audit.py` | `log_event()` writes one audit row and never raises, so a logging problem can't break an action. It also defines the event types shown on the audit page. Pass only short, non-sensitive text. |
| `passwords.py` | Password and username rules, the common-password check (`common_passwords.txt`), and the temporary-password generator. |
| `photos.py` | Storage photos: validates and re-encodes uploads, stores them in `instance/uploads/`, makes thumbnails, and serves both at `/photos/...` to signed-in staff only. |
| `security.py` | Adds the Content-Security-Policy and other security headers to every response. |
| `icons.py` | The `icon()` template function. It inlines Bootstrap Icons SVGs from `static/vendor/`. |
| `common_passwords.txt` | The list of common passwords that are refused. |

### `app/models.py`

*   `Staff`: a staff account. It stores a username and a hashed password, the
    role (`is_admin`), whether the account is active, failed sign-ins and the
    lockout time, the last sign-in, and whether a new password is required.
*   `Recipient`: a person who receives packages. First name, last name, and box
    number are optional, because a label might show only one of them.
*   `Package`: a checked-in package. It stores the tracking number (uppercase,
    no spaces, unique), carrier, optional recipient, status (`checked_in` or
    `checked_out`), who checked it in and out and when, the storage photo's file
    name, notes, and the last 4 digits of the student ID given at pickup.
*   `AuditEvent`: one line of the audit log (time, account, event type, a short
    target and details, and the IP address).

`db.create_all()` creates missing tables but never changes existing ones. See
the README for resetting a local database after a model change.

## Routes

Each file in `app/routes/` is a Flask blueprint. All pages require sign-in
unless noted.

| File | Routes |
|---|---|
| `auth.py` | `/login` (rate limited, public), `POST /logout`, and `/account/password` (Change Password). Redirects after sign-in go only to pages on this site. |
| `dashboard.py` | `/` (redirects to the home screen), `/dashboard` (today's counts and recent activity), and `/account`. |
| `checkin.py` | `/checkin` (scanner), `/checkin/confirm` (form and save), `/checkin/photo/<id>` (photo screen and upload), `/checkin/photo/<id>/skip`, and `/api/recipients/search` (JSON). Also holds `normalize_tracking()`, `guess_carrier()` (the carrier patterns the scanner also uses), `validate_checkin_form()`, and `find_or_create_recipient()`. |
| `checkout.py` | `/checkout` (scanner) and `/checkout/confirm` (shows the package; the POST marks it picked up and can record a 4-digit student ID). |
| `search.py` | `/search` (by tracking number, recipient name, or box number, with a status filter; rate limited) and `/packages/<id>` (detail page). |
| `admin.py` | Admin only: `/admin/staff` and its POST actions (create, deactivate, reactivate, unlock, promote, demote, reset password), and `/admin/audit` (the audit log). |
| `errors.py` | The friendly error pages (400, 403, 404, 405, 413, 429, 500). They show only fixed text. |

`photos.py` also registers the `/photos/<file>` and `/photos/thumb/<file>`
routes. `routes/__init__.py` is empty and makes the folder a package.

## Templates

All templates are in `app/templates/`. Files starting with `_` are shared pieces.

| File | What it shows |
|---|---|
| `base.html` | The base layout: viewport, safe areas, flash messages, toasts, and the bottom tab bar. |
| `_scan_page.html` | The full-screen camera layout shared by the two scan pages. |
| `_macros.html` | Shared pieces: alerts, flash messages, toasts, status chips, and the back button. |
| `_package.html` | Pieces shared by pages that show one package: the storage-photo card, photo viewer, and local-time helper. |
| `login.html`, `account.html`, `change_password.html` | Sign-in, the Account screen, and Change Password. |
| `dashboard.html` | The home screen. |
| `checkin.html`, `checkin_confirm.html`, `checkin_photo.html` | The three check-in steps. |
| `checkout.html`, `checkout_confirm.html` | The two check-out steps. |
| `search.html`, `package_detail.html` | Package search and one package's detail page. |
| `admin_staff.html`, `admin_audit.html` | Manage Staff and the audit log. |
| `error.html` | The shared error page. |

## Static files

*   **CSS** (`app/static/css/`): `tokens.css` holds every color, size, and
    timing; `components.css` holds shared components; `style.css` holds
    page-specific layout. See the design system document.
*   **JavaScript** (`app/static/js/`): each file belongs to one screen or
    component. `scanner.js` (barcode scanning), `manual_entry.js` ("Can't scan?"
    sheet), `recipient_search.js` (autofill), `checkin_confirm.js` and
    `checkout_confirm.js` (stop double submits), `checkin_photo.js` (camera,
    shrinking, and upload), `photo_viewer.js` (full-screen photo),
    `local_time.js` (times in the phone's time zone), and `toasts.js` (brief
    confirmations).
*   **Vendor** (`app/static/vendor/`): Bootstrap CSS, the Bootstrap Icons that
    are used, and html5-qrcode, all served from this site so the
    Content-Security-Policy can allow only its own files. Its README lists
    versions and checksums.

## Tests and tools

Run the tests with `python -m pytest`. Each test file builds its own app with an
in-memory database.

| File | Covers |
|---|---|
| `test_db.py` | The models: optional fields, relationships, and constraints. |
| `test_routes.py`, `test_app_routes.py` | Check-in routes and the app when started without `run.py`. |
| `test_scan_screens.py` | The scan, confirm, and photo screens. |
| `test_checkout.py` | Check-out. |
| `test_search.py` | Search and the detail page. |
| `test_photos.py` | Photo validation, storage, and serving. |
| `test_home.py` | The home screen and its counts. |
| `test_accounts.py` | Roles, Manage Staff, password rules, forced password change, and lockout. |
| `test_audit.py` | The audit log, `purge-audit`, and student ID shortening. |
| `test_deployment.py` | Importing from another working directory, `.env` loading, `TRUST_PROXY`, production startup checks, `init-db`, and `backup`. |
| `test_security.py` | CSRF, headers, session timeouts, rate limits, and production startup checks. |
| `test_design.py` | Design system: colors only in `tokens.css`, the nav bar, icons, and status chips. |

`scripts/audit_screens.py` is a development tool. It starts the app against a
temporary database, opens every page in Chromium at phone sizes, and reports
layout and accessibility problems. See the notes at the top of the script.
