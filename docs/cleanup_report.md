# Pre-production cleanup report

Branch: `cleanup/pre-production` (from `main` at 7b94585). Phase 1 is read-only: nothing has been changed or deleted except this file.

**Baseline:** `python -m pytest` → **543 passed**, 0 failed, 0 skipped (88 s).
**Tools run (venv only, not in requirements.txt):** ruff (`--select F,ERA`), vulture (min-confidence 60), deptry, coverage (branch).
**Size before:** 94 tracked files; about 11,100 lines across app, tests, scripts and static (excluding vendored libraries).

| Group | Items |
|---|---|
| SAFE TO REMOVE | 3 |
| PROBABLY SAFE (needs your OK) | 8 |
| KEEP (looked unused, isn't) | 14 |
| DECISIONS FOR YOU | 9 |

Overall: the code is already clean. Ruff found one unused import; vulture's 36 hits are all Flask/pytest dynamic use (see KEEP); there are no stray TODO/FIXME/debug prints in Python, no commented-out code, no unused templates, and no unreferenced JS or icons.

---

## 1. SAFE TO REMOVE (3)

| # | File : line / name | Why it's redundant | How verified |
|---|---|---|---|
| S1 | `app/routes/dashboard.py:2`, `datetime` in `from datetime import datetime, timedelta, timezone` | Imported, never used. `timedelta`, `timezone` are used. | Ruff F401. Grep of `datetime` in the file shows only the import line and `timedelta`/`timezone` usages. |
| S2 | `tests/test_models.py` (1 line: `# Model tests go here.`) | Empty scaffold placeholder; contains no tests. It defines no tests. | File is 1 line; Nothing imports it. (Test count stays 543.) |
| S3 | `project-structure.md` sections describing `checkout.py` and `search.py` as "a placeholder … doesn't contain any code yet" | Factually wrong now: both are fully implemented. This is a doc fix, not code. | Compared against `app/routes/checkout.py` (149 lines) and `app/routes/search.py` (170 lines). See also D8 for the rest of that file. |

## 2. PROBABLY SAFE (8, needs your OK)

| # | File : line / name | Why it looks redundant | How verified / caveat |
|---|---|---|---|
| P1 | `app/static/js/scanner.js:271`, `console.log("Scanned:", decodedText);` | Leftover debug output; it also writes the scanned tracking number to the browser console. | Only `console.log` in the app JS. Removing it changes no behavior. |
| P2 | `app/routes/checkout.py` (`flash(f"Picked up: …{tracking[-8:]}.", …)`) and `app/routes/checkin.py:ending()` | Same "…last 8 characters" formatting written twice. | `ending()` exists in checkin.py and is used 3×; checkout.py inlines the same expression. Merge by importing `ending` (checkout.py already imports from checkin.py). Output identical, covered by existing checkout tests. |
| P3 | `tracking_patterns = [pattern.pattern for _, pattern in CARRIER_PATTERNS]` in `checkin.py:checkin()` and `checkout.py:checkout()` | Same list built in two views. | Could become one `TRACKING_PATTERNS` constant next to `CARRIER_PATTERNS`. Covered by scan-screen tests. |
| P4 | `app/static/css/components.css` `.form-select`, `.form-select-lg` rules (≈ lines 305–340) | No template uses `form-select`; the carrier `<select>` uses `inset-select`. | Grepped templates, JS, Python, tests for `form-select`: only CSS hits. **Caveat:** `docs/design-system.md` may mention it; and it's a Bootstrap class, so a future form could pick it up. |
| P5 | CSS classes defined but never used: `.alert-secondary`, `.caption`, `.inset-row-icon`, `.status-danger`, `.status-warning`, `.title-2`, `.title-3` (components.css), `.confirm-tracking` (style.css) | No reference anywhere. | Script scanned every class selector in the three CSS files against all templates, JS, Python, tests and scripts. **Caveats:** `.status-{{ kind }}` is built dynamically in `_macros.html`; only `neutral/success/info` appear to be passed, but `status-danger`/`status-warning` are design-system chips, so they may be intentional spares. `.title-*` and `.caption` are type-scale classes from `docs/design-system.md`. I'd keep the design-system spares and remove only `.confirm-tracking`, `.inset-row-icon`, `.alert-secondary` if you agree. |
| P6 | `app/static/css/tokens.css` variables `--space-5` and `--text-callout` | Defined, never referenced via `var()`. | Searched all CSS, templates and JS. They are part of the design-token scale, so removal is cosmetic only. |
| P7 | Duplicated test scaffolding: `app` / `client` fixtures and `add_staff` / `fetch` / `sign_in` helpers re-defined in `test_accounts.py`, `test_audit.py` (identical shape), and similar `app`/`client` fixtures in most other test files; there is no `tests/conftest.py` | Copy-pasted fixtures. | Could be merged into a `conftest.py`. **Risk:** each file's `create_app` config differs slightly, so this is a refactor of tests, not a deletion, and I would not do it without your explicit approval. Test count must stay 543. |
| P8 | `.gitkeep` + `app/static/uploads/` folder and its `.gitignore` rules (`app/static/uploads/*`, `!…/.gitkeep`) | Photos are now stored in `instance/uploads/` (`UPLOAD_FOLDER`), outside `static/`. The old folder is a first-scaffold leftover. | Grep for `static/uploads` in code, tests, scripts, README, docs: no hits. **Caveat:** harmless to keep; removing it means touching `.gitignore` too. |


## 3. KEEP (14): looked unused but isn't

| # | Item | Why kept | How checked |
|---|---|---|---|
| K1 | Vulture's 36 "unused function" hits: `load_user`, `add_hsts_header`, `enforce_session_timeouts`, `enforce_account_state`, `csrf_failed`, `add_security_headers`, `too_large`, `server_error`, all route functions in `admin.py`, `auth.py`, `checkin.py`, `dashboard.py`, `search.py` | Registered through decorators (`@route`, `@before_request`, `@after_request`, `@errorhandler`, `@user_loader`). | Each is decorated; routes also exercised by the 543 tests. |
| K2 | Vulture: `login_view`, `login_message_category`, `permanent`, model columns `email`, `recipient_id`, relationship `checked_out_staff` | Framework attributes (Flask-Login, Flask session, SQLAlchemy). | `checked_out_staff` is used in `checkout_confirm.html:76` and `package_detail.html:59`. `email` is a column, see D1. |
| K3 | Vulture test hits `test_client_class`, `enabled` (test_accounts/test_audit) | Monkeypatch/attribute assignments in tests. | Read the lines. |
| K4 | CLI alias `create-staff` (`app/__init__.py`) | Public CLI command; you said keep all CLI commands. | Registered with `app.cli.add_command(create_user, "create-staff")`, mentioned in project-structure.md. |
| K5 | `PASSWORD_CHANGE_ENDPOINTS` includes `"static"` and `"auth.logout"` | Security logic: lets a forced-password-change user still load CSS/JS and sign out. | Read `enforce_account_state`. Off limits. |
| K6 | `REMEMBER_COOKIE_*` settings, the "Remember-me isn't used yet" comment | Cookie security configuration; off limits. | See D5. |
| K7 | `tzdata` in requirements.txt (deptry DEP002 "unused") | Needed at runtime by `zoneinfo` on Windows (no system tz database); the app raises at startup without it for `APP_TIMEZONE`. | `configure_environment` calls `ZoneInfo(...)`; requirements comment says so. |
| K8 | deptry DEP003 (transitive: click, markupsafe, sqlalchemy, werkzeug) and DEP004 (playwright, axe-playwright-python in `scripts/audit_screens.py`) | Imports of packages installed by Flask/Flask-SQLAlchemy, and dev tooling you asked me to keep. | Informational only; see D6. |
| K9 | All 20 Bootstrap Icons SVGs | Names are passed to `icon()` dynamically through macros (`status_chip(kind, "bag-check-fill")`, `base.html` nav tuples), so a plain `icon("…")` grep misses `bag-check-fill`, `house-door-fill`, `pencil-fill`, `person-circle`. | Grepped every name across templates; all 20 are used. |
| K10 | `app/common_passwords.txt` | Read by `passwords.common_passwords()`. | Referenced in `passwords.py`. Security. |
| K11 | `app/static/vendor/*` and `vendor/README.md` | Self-hosted for the CSP; README holds the integrity hashes. | Referenced by `base.html`, `_scan_page.html`, `icons.py`. |
| K12 | All 9 JS files in `app/static/js/` | Each is loaded by a template; no unused functions inside any of them (every declared function is referenced at least twice). | Grepped each file name in templates; scripted per-file function-usage check. |
| K13 | All 18 templates | Each is rendered by a route or extended/included. | Grepped each name in Python and templates. |
| K14 | `Staff.last_login_at`, `password_changed_at`, `failed_login_count`, `locked_until`; `AuditEvent.ip_address`, `Package.notes`, etc. | Used in templates, routes or tests. | Grepped. (Columns are untouchable anyway, see D1.) |

## 4. DECISIONS FOR YOU (9)

| # | Item | Notes |
|---|---|---|
| D1 | **Database column, `Recipient.email`** (`app/models.py:103`) | The only model field with no reader or writer anywhere in the app, templates, tests or scripts. You said never touch models, so I'm only flagging it. Decide before the production DB is created whether you want it. |
| D2 | **Relationship, `Package.recipient_id` comment** "Optional until Save decides how form fields map onto Recipient rows" | Stale comment; the column is in use and nullable by design. Comment-only change; models are off limits, so I'd leave it unless you OK a comment edit. |
| D3 | **CLI commands you named that don't exist:** `backup` and `init-db` | Only `create-user` (alias `create-staff`), `reset-db`, `purge-audit` and `trim-student-ids` are registered. I found no `backup` or `init-db`, so nothing to keep or remove. Flagging in case they were meant to exist (the DB is created by `db.create_all()` on app start). |
| D4 | **`trim-student-ids` CLI and `STUDENT_ID_*` logic** | Needed once if production data was loaded before the 4-digit change; on a fresh production DB it does nothing. It's a CLI command you asked to keep, so kept. |
| D5 | **Security items that look redundant (reported, left alone):** `SESSION_COOKIE_HTTPONLY`/`SAMESITE` plus separate `REMEMBER_COOKIE_*`; `SESSION_REFRESH_EACH_REQUEST=True` (Flask's default); `WTF_CSRF_TIME_LIMIT=None`; `Permissions-Policy`/`X-Frame-Options` alongside CSP `frame-ancestors`; `/logout` and `change_password` sharing `LOGIN_RATE_LIMIT` | `SESSION_REFRESH_EACH_REQUEST=True` is Flask's default, so that line is redundant but documents intent. All are security/session settings, so none touched. |
| D6 | **Dependencies:** `requirements.txt` lists 8 packages, all used. deptry suggests `werkzeug`, `sqlalchemy`, `click`, `markupsafe` are imported directly but only installed transitively | Not unused, so nothing to remove. You could pin them explicitly for production reproducibility. Also: no versions are pinned for any dependency. Worth considering for production. |
| D7 | **`run.py` uses `app.run(host="0.0.0.0")`** | Fine for development; production should use a WSGI server. Not a cleanup item; flagging only. |
| D8 | **`project-structure.md` is badly out of date** (lists only 4 templates, 1 CSS file, 4 JS files, 3 tests; says uploads are 16 MB and in `static/uploads`; omits admin, audit, photos, security, passwords, icons, error pages) and `README.md`/`docs/design-system.md` were not audited line by line | Recommend either rewriting it or deleting it; a rewrite is documentation work beyond "cleanup", so your call. |
| D9 | **Tracked-files check:** no databases, uploads, `.env`, secrets, backups, screenshots, caches or OS junk are tracked. `.env`, `instance/`, `audit-screenshots/`, `venv/`, caches exist locally and are git-ignored. | Nothing to action. `app/static/uploads/.gitkeep` is the only placeholder tracked (see P8). `.env.example` contains only a placeholder key. |

---

## Coverage (tests never reaching code)

Branch coverage over `app/`: **97%** (1,148 statements, 35 missed); 543 tests still pass under coverage. Everything not reached is a defensive error path, not dead code, so nothing is recommended for removal from this:

- `photos.py` 50, 64-67, 79-82, 109-110, 145-147: save/delete/thumbnail failure handlers (disk errors); upload validation, so off limits.
- `checkin.py` 296, 307-309, 320-324, 327-328: photo-upload database and disk failure branches.
- `checkout.py` 135-138: lost-race branch when two staff check out the same package.
- `audit.py` 73-74: the "logging itself failed twice" fallback; audit code, off limits.
- `models.py` 68, 84, 162, `auth.py` 43: a lockout early-return (line 68), two `__repr__` methods (84, 162), and `auth.py` 43, where an already-signed-in user opening /login is redirected home.

Files at 100%: `cli.py`, `icons.py`, `security.py`, `routes/admin.py`, `routes/dashboard.py`, `routes/search.py`, `routes/errors.py`.


## Other checks with nothing found

- **Commented-out code:** ruff ERA flagged one line (`scripts/audit_screens.py:120`) but it's an explanatory comment showing a log format, not code. Keep.
- **Debug prints / TODO / FIXME / XXX / HACK:** none in app, tests or run.py. `print()` in `scripts/audit_screens.py` is that tool's output.
- **Unused routes / dead feature flags / unread env vars:** none. All four env vars (`APP_ENV`, `APP_TIMEZONE`, `SECRET_KEY`, `FLASK_DEBUG`) are read and documented; every blueprint is registered and every route is reachable from a template link, JS fetch (`/api/recipients/search`, `/checkin/photo/<id>`) or test.
- **Duplicated helpers:** `normalize_tracking` exists once. Time formatting exists in three places with different output formats (`dashboard.full_time`, `admin.audit_log` strftime, `local_time.js`) so they are not true duplicates. The only real duplication is P2 and P3.

## Follow-up (approved after Phase 2)

- D6 (unpinned dependencies): `requirements.txt` is now pinned to the exact versions the tests ran against.
- D8 (stale `project-structure.md`): rewritten against the current code.
