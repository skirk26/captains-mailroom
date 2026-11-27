# Deploying to PythonAnywhere

PythonAnywhere imports the app from a WSGI file and terminates HTTPS in front
of it. This guide is based on how this repository works. Anything I couldn't
check from the code or by running it is marked **(unverified)**: confirm it on
the PythonAnywhere site, because its screens and supported versions change.

Replace `YOURUSERNAME` with your PythonAnywhere username throughout.

## What the app needs from the host

| Need | How the app handles it |
|---|---|
| Imported by a WSGI file | `from app import create_app; application = create_app()` starts no server and writes only inside `instance/`. Tested from a different working directory. |
| Settings | Read from the real environment first, then from the project's `.env` (found from the code's location, not the working directory). |
| Data folders | The database (`instance/captains_mail.db`), photos (`instance/uploads/`) and backups (`instance/backups/`) are all under `instance/`, which is git-ignored and not served as static files. |
| HTTPS in front | Set `TRUST_PROXY=1` so the app reads the real client address and the https scheme from the proxy's headers. |

## 1. Get the code

Open a **Bash console** from the PythonAnywhere Consoles tab:

```bash
cd ~
git clone https://github.com/skirk26/captains-mailroom.git captains-mail
cd captains-mail
```

This uses the repository's existing remote. If it's private, you'll need a
GitHub access token or deploy key **(unverified: set up whichever you use)**.
The folder name `captains-mail` is used in the rest of this guide.

If you want the deployment-ready code and it's not on your default branch yet,
check out the right branch first (for example `git checkout <branch>`).

## 2. Create the virtualenv

Use the same Python version as local if PythonAnywhere offers it. This project
was developed and tested on **Python 3.14.0**. **(unverified: whether
PythonAnywhere offers 3.14.)** Check the versions available on the Web tab or
with `ls /usr/bin/python3*`. The pinned packages (Flask 3.1.3, Pillow 13.0.0 and
the others in `requirements.txt`) were only tested on 3.14.0; if `pip` refuses
one on an older Python, tell me which version you have and I'll adjust the pins
rather than guessing.

```bash
mkvirtualenv --python=/usr/bin/python3.XX captains-mail   # use the version you chose
pip install -r requirements.txt
```

Don't install `requirements-dev.txt` on the server; it's only for testing.
`mkvirtualenv` leaves the virtualenv active; in a new console run
`workon captains-mail`. The environment lives at
`/home/YOURUSERNAME/.virtualenvs/captains-mail`.

## 3. Create `.env`

```bash
cp .env.example .env
nano .env
```

Set:

```text
APP_ENV=production
SECRET_KEY=<a long random string>
TRUST_PROXY=1
APP_TIMEZONE=America/New_York
FLASK_DEBUG=0
```

Generate the key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

- Keep `.env` private (it's git-ignored). Don't commit it or paste the key
  anywhere.
- In production the app **refuses to start** if `SECRET_KEY` is missing or a
  placeholder, if `FLASK_DEBUG` is on, or if secure cookies are off.
- Changing `SECRET_KEY` later signs everyone out.
- `APP_TIMEZONE` is where "today" starts on the home screen. The server clock
  is UTC; the app stores UTC and converts.

## 4. Web tab: manual configuration

1. Open the **Web** tab, then **Add a new web app**.
2. Choose **Manual configuration** (not "Flask"), and pick the same Python
   version as your virtualenv.
3. Under **Code**:
   - **Source code:** `/home/YOURUSERNAME/captains-mail`
   - **Working directory:** `/home/YOURUSERNAME/captains-mail` (optional; the
     app doesn't depend on it)
4. Under **Virtualenv**, enter `/home/YOURUSERNAME/.virtualenvs/captains-mail`.
5. Under **Security**, turn on **Force HTTPS**. Phones only allow the camera on
   HTTPS pages, and the app's cookies are Secure-only in production.

## 5. The WSGI file

On the Web tab, under **Code**, open the **WSGI configuration file** (it's named
like `/var/www/YOURUSERNAME_pythonanywhere_com_wsgi.py`). Replace everything in
it with:

```python
import sys

PROJECT = "/home/YOURUSERNAME/captains-mail"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from app import create_app

application = create_app()
```

`create_app()` reads `.env` from the project folder itself, so no `load_dotenv`
call or `os.environ` lines are needed here. If you'd rather set variables in the
WSGI file, put `os.environ["NAME"] = "value"` lines **before** the `import`;
they take priority over `.env`.

## 6. Optional: map `/static/`

The app serves its own static files, so this works without any mapping. For
speed you can let PythonAnywhere serve them directly: on the Web tab, under
**Static files**, add:

| URL | Directory |
|---|---|
| `/static/` | `/home/YOURUSERNAME/captains-mail/app/static` |

Don't map anything to `instance/`: it holds the database, the photos and the
backups, and must never be public. Photos are served only through the
login-protected `/photos/...` routes. The security headers are added by the
app, so files served through this mapping will **not** carry them
**(unverified)**; skip the mapping if you want every response to have the
Content-Security-Policy headers.

## 7. Create the database and the first admin

In the Bash console, with the virtualenv active:

```bash
cd ~/captains-mail
flask --app run init-db
flask --app run create-user YOURNAME --admin
```

`init-db` lists the tables it created (the app also creates missing tables each
time it starts). `create-user` asks for a password twice (12 characters or
more). Add everyone else from the Manage Staff page.

## 8. Reload and check

Click **Reload** on the Web tab, then open
`https://YOURUSERNAME.pythonanywhere.com`. You should land on the sign-in page.

If the page shows an error, read the logs (section 11). The startup line to
look for in the **error log** after a reload is
`Production startup checks passed: ...` **(unverified: PythonAnywhere may show
INFO lines in the error log or the server log; check both)**.

Check that sign-in works, and that the camera prompt appears on the Check In
tab (this needs HTTPS).

## 9. Updating

```bash
cd ~/captains-mail
git pull
workon captains-mail
pip install -r requirements.txt   # only needed if requirements.txt changed
```

Then click **Reload** on the Web tab. Run a backup first if the update touches
`app/models.py`: `create_all` only creates missing tables and never adds a
column to an existing one (the README explains `reset-db`, which erases data;
don't run that on production without a backup).

## 10. Backups

```bash
cd ~/captains-mail
workon captains-mail
flask --app run backup
```

This writes `instance/backups/captains_mail_YYYYMMDD_HHMMSS.zip` (UTC) holding
a consistent copy of the database and the photo folder, and keeps the newest 5.
Download a copy to your own computer from the **Files** tab; backups left only
on the server don't protect you from losing the account or its disk.

To run it automatically, use the **Tasks** tab with a daily task such as
`cd /home/YOURUSERNAME/captains-mail && /home/YOURUSERNAME/.virtualenvs/captains-mail/bin/flask --app run backup`
**(unverified: the exact task setup and whether your plan allows scheduled
tasks)**.

To restore, unzip, stop the web app (Disable on the Web tab), put
`captains_mail.db` back at `instance/captains_mail.db` and the `uploads` folder
at `instance/uploads/`, then re-enable and reload.

## 11. Reading logs

On the **Web** tab, under **Log files**, open:

- the **Error log** for Python errors and the app's own log messages (start-up
  checks, failed photo saves), and
- the **Server log** for startup problems and the web server's messages.

The most recent entries are at the bottom. A startup refusal (missing
`SECRET_KEY`, debug on, and so on) appears in the error log as a
`RuntimeError` with a plain-language message.

## Things to know

- **Rate limits are per process.** The sign-in and search limits are counted in
  memory, so they reset on reload and are not shared between web workers. With
  `TRUST_PROXY=1` each user has their own count; without it, everyone would
  share one. **(unverified: how many worker processes your plan uses.)**
- **Don't set `TRUST_PROXY=1` unless a proxy really is in front** (PythonAnywhere
  is **(unverified)**; confirm that it sets `X-Forwarded-For`), because the
  headers could otherwise be faked to dodge the rate limit.
- **Disk quota.** Photos and backups count toward your account's disk space
  **(unverified: your quota)**.
- **Free accounts** may restrict things such as custom domains and scheduled
  tasks, and may need periodic "keep alive" clicks to stay active
  **(unverified: check your plan)**.
- Nothing here has been run on PythonAnywhere itself. Everything that could be
  checked locally (import from another working directory, `.env` loading, proxy
  headers, startup checks, `init-db`, `backup`) is covered by
  `tests/test_deployment.py`.
