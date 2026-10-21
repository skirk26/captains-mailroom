# Captain's Mail project structure

This document describes each file in the Captain's Mail repository and how the
files work together. Use it to find where a feature lives before you change it.

Captain's Mail is a Flask web app for a college mailroom. Staff use their phones
to scan shipping labels, check packages in, and record who each package is for.

For setup and run instructions, see [`README.md`](README.md).

## Directory layout

```text
captains-mail/
├── .env.example
├── .gitignore
├── README.md
├── project-structure.md
├── requirements.txt
├── run.py
├── app/
│   ├── __init__.py
│   ├── cli.py
│   ├── models.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── checkin.py
│   │   ├── checkout.py
│   │   ├── dashboard.py
│   │   └── search.py
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css
│   │   ├── js/
│   │   │   ├── checkin_confirm.js
│   │   │   ├── manual_entry.js
│   │   │   ├── recipient_search.js
│   │   │   └── scanner.js
│   │   └── uploads/
│   │       └── .gitkeep
│   └── templates/
│       ├── checkin.html
│       ├── checkin_confirm.html
│       ├── dashboard.html
│       └── login.html
└── tests/
    ├── test_db.py
    ├── test_models.py
    └── test_routes.py
```

## How a check-in works

The following steps show how the files work together when a staff member checks
in a package:

1.  The staff member signs in on the login page (`login.html`), which
    `routes/auth.py` handles.
1.  On the dashboard (`dashboard.html`), they select **Check In Package**.
1.  The check-in page (`checkin.html`) opens the camera. `scanner.js` decodes the
    barcode and opens the confirmation page with the tracking number in the URL.
    If the label can't be scanned, `manual_entry.js` lets them type the number
    instead.
1.  The confirmation page (`checkin_confirm.html`) shows the tracking number and a
    suggested carrier. As they type a last name or box number,
    `recipient_search.js` suggests recipients entered before.
1.  When they select **Save Check-In**, `routes/checkin.py` validates the form,
    finds or creates the recipient, and saves the package to the database.
1.  The app returns them to the check-in page with a confirmation message, ready
    for the next package.

## Root files

### `.env.example`

A template for the `.env` file that holds local settings. Copy it to `.env` and
set `SECRET_KEY` to a long random string. Flask uses the secret key to sign
session cookies.

Note: Git ignores `.env`, so your real secret key never gets committed.

### `.gitignore`

Lists the files that Git doesn't track: the virtual environment (`venv/`), the
`instance/` folder that holds the database, Python cache files, `.env`, any
`.db` file, and uploaded photos in `app/static/uploads/`.

### `README.md`

The project's introduction. It explains what the app does and how to set it up,
create a staff account, and run it.

### `project-structure.md`

This document.

### `requirements.txt`

The Python packages the app needs: Flask, Flask-SQLAlchemy, Flask-Login, and
python-dotenv. To install them, run the following command:

```bash
venv\Scripts\python.exe -m pip install -r requirements.txt
```

Note: Two development-only packages aren't listed here. `pytest` runs the tests,
and `cryptography` lets Flask serve HTTPS for phone testing. Install them
separately if you need them.

### `run.py`

The entry point that starts the app. It does the following:

*   Creates the app by calling `create_app()`.
*   Redirects the home page (`/`) to the dashboard. Signed-out visitors go on to
    the login page.
*   Starts the development server on port 5000 and listens on all network
    addresses, so phones on the same Wi-Fi can connect.

To start the app, run `venv\Scripts\python.exe run.py`.

## The `app` package

### `app/__init__.py`

Builds and configures the Flask app. It defines the following:

*   `db`: the SQLAlchemy database object that the rest of the app imports.
*   `login_manager`: the Flask-Login object that tracks who's signed in.
*   `create_app(test_config=None)`: the app factory. It loads settings from
    `.env`, stores the SQLite database at `instance/captains_mail.db`, limits
    uploads to 16 MB, creates any missing tables, registers the route
    blueprints, and adds the `create-staff` command.

Tests pass `test_config` to use a temporary in-memory database instead of your
real one.

Note: `db.create_all()` creates missing tables but doesn't change existing ones.
After you change a model, reset your local database so the new columns appear.

### `app/cli.py`

Defines the `create-staff` command, which adds a staff account. The command
prompts for the password twice and hides what you type:

```bash
venv\Scripts\python.exe -m flask --app run create-staff USERNAME
```

Replace `USERNAME` with the name the staff member signs in with.

### `app/models.py`

Defines the database tables:

*   `Staff`: a mailroom staff account. Stores a username and a hashed password,
    never the password itself. `set_password()` and `check_password()` handle
    the hashing.
*   `Recipient`: a person who receives packages. First name, last name, and box
    number are all optional, because a label might show only one of them.
    `full_name` joins whichever names exist.
*   `Package`: a checked-in package. Stores the tracking number, carrier,
    recipient (optional), notes (optional), status, the staff member who checked
    it in, and the check-in time. Tracking numbers are unique and stored without
    spaces in uppercase, so a scanned number and a typed number match.

The file also holds `load_user()`, which Flask-Login uses to look up the
signed-in staff member.

## Routes

Each file in `app/routes/` is a Flask blueprint: a group of related pages.

### `app/routes/__init__.py`

An empty file that makes `app/routes/` a Python package.

### `app/routes/auth.py`

Handles signing in and out:

*   `/login`: shows the login form and checks the username and password. After a
    failed attempt, it keeps the typed username so only the password needs
    retyping. After a successful one, it returns the staff member to the page they
    were trying to reach, but only if that page is on this site.
*   `/logout`: signs the staff member out and returns them to the login page.

### `app/routes/dashboard.py`

Serves `/dashboard`, the home screen after sign-in. Requires sign-in.

### `app/routes/checkin.py`

Handles everything about checking packages in. All routes require sign-in.

| Route | Method | Purpose |
| --- | --- | --- |
| `/checkin` | GET | Shows the camera scanner page. |
| `/checkin/confirm` | GET | Shows the confirmation form for a tracking number. Warns right away if the package is already checked in. |
| `/checkin/confirm` | POST | Validates and saves the check-in. |
| `/api/recipients/search` | GET | Returns up to 8 recipients as JSON, for autofill. |

The file also contains these helper functions:

*   `normalize_tracking()`: removes spaces and converts a tracking number to
    uppercase.
*   `guess_carrier()`: suggests a carrier from the tracking number's pattern, for
    example `1Z…` for UPS or `TBA…` for Amazon. To support a new tracking number
    format, add it to `CARRIER_PATTERNS`. The scanner uses the same list.
*   `validate_checkin_form()`: checks the form and returns cleaned values and a
    list of error messages. It doesn't touch the database, so you can test it on
    its own.
*   `find_or_create_recipient()`: reuses a recipient with the same name and box
    number, ignoring case, or creates a new one.

### `app/routes/checkout.py`

A placeholder for package check-out routes. It doesn't contain any code yet.

### `app/routes/search.py`

A placeholder for package search routes. It doesn't contain any code yet.

## Templates

The files in `app/templates/` are Jinja templates that Flask turns into HTML
pages. Each page loads Bootstrap 5 and `style.css`.

### `app/templates/login.html`

The sign-in page. Shows the username and password fields and any error message.

### `app/templates/dashboard.html`

The home screen. Shows who's signed in, a **Sign out** link, and the
**Check In Package** button.

### `app/templates/checkin.html`

The scanner page. Contains the camera viewfinder with gold corner brackets, the
scanning instructions, any messages (for example, "Package … checked in"), and
the **Can't scan? Enter tracking number manually** form.

It loads the html5-qrcode library, version 2.3.8, from a CDN. An integrity hash
stops the page from running a tampered copy.

### `app/templates/checkin_confirm.html`

The confirmation form. Shows a chip that says whether the number was scanned or
typed, and these fields:

*   Tracking number and carrier, both required.
*   First name, last name, and box number, all optional. A box number must be
    from 1000 to 5199.
*   Notes, optional, up to 500 characters.

At least one of name, box number, or notes must be filled in. If saving fails,
the page shows the errors in a red box and keeps everything the staff member
typed.

## Static files

Flask serves the files in `app/static/` to the browser as-is.

### `app/static/css/style.css`

All custom styles. It defines the color palette (navy `#1C2B4A`, gold
`#D9A441`, and cream `#FAF6EE`) and styles the following:

*   Buttons and the page header.
*   The camera viewfinder and its corner brackets.
*   Invalid-field highlighting.
*   The recipient suggestion list.

### `app/static/js/scanner.js`

Runs the barcode scanner on the check-in page:

*   Opens the back camera and fills the viewfinder with the video.
*   Decodes only what's inside the gold brackets.
*   Reads these formats: Code 128, the long barcodes most carriers use; DataMatrix,
    the square codes on Amazon labels; and a few test formats. The list is in
    `SUPPORTED_FORMATS` at the top of the file.
*   Ignores DataMatrix codes that don't look like a tracking number, such as
    Amazon's internal sorting codes.
*   Shows a clear message if the camera is blocked, missing, or in use, or if
    the page isn't on HTTPS.
*   Turns the camera off when you leave the page.

When it reads a code, it opens the confirmation page with the tracking number.

### `app/static/js/manual_entry.js`

Shows and hides the manual tracking-number form on the check-in page, and trims
spaces before submitting. It's separate from `scanner.js` so that manual entry
still works if the scanner fails to load.

### `app/static/js/checkin_confirm.js`

Prevents double submits on the confirmation page. When you select
**Save Check-In**, the button changes to **Saving…** and is disabled.

### `app/static/js/recipient_search.js`

Adds autofill to the confirmation page. As you type at least 2 characters in the
last name or box number field, it waits 250 milliseconds, searches past
recipients, and shows matches like "Rivera, Alex (Box 1234)". Selecting a match
fills in the first name, last name, and box number.

The form works the same with or without autofill. If the search finds nothing
or fails, no list appears.

### `app/static/uploads/.gitkeep`

An empty file that keeps the `uploads` folder in Git. The folder is for package
location photos in a later version. Git ignores everything else in it.

## Tests

The tests use pytest and a temporary in-memory database, so they never change
your real data. To run all of them, use the following command:

```bash
venv\Scripts\python.exe -m pytest
```

### `tests/test_db.py`

Tests the database models: optional recipient names, readable `repr()` output
when fields are missing, packages without a recipient, the required carrier, and
unique tracking numbers.

### `tests/test_routes.py`

Tests the pages and helpers in `routes/checkin.py`:

*   Saving a check-in, including the success message and the recipient reuse.
*   Validation: box number range, field lengths, the at-least-one rule, and
    duplicate tracking numbers.
*   That a failed save keeps what was typed.
*   Carrier guessing and the recipient search API.
*   That signed-out visitors are sent to the login page.

### `tests/test_models.py`

A placeholder. It doesn't contain any tests yet; the model tests are in
`test_db.py`.

## Files that aren't in Git

These files exist on your computer but aren't committed:

*   `venv/`: the Python virtual environment with the installed packages.
*   `instance/captains_mail.db`: the SQLite database with your staff accounts
    and packages. To inspect it, open it in DB Browser for SQLite.
*   `.env`: your local settings, created from `.env.example`.
