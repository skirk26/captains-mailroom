"""Phone-screen audit for Captain's Mail (development only).

Starts the app against a temporary database and photo folder (never
instance/), seeds a few packages and a staff account, then opens every
signed-in page in Chromium at several phone and tablet sizes, in light and
dark mode. For each page it reports:

  - horizontal scrolling
  - tappable elements smaller than 44 x 44 px
  - content that can't be scrolled out from behind the tab bar or a bottom bar
  - text clipped by, or spilling out of, its container
  - accessibility violations found by axe-core

and saves a full-page screenshot of each page at each size to
audit-screenshots/ (git-ignored), with the report in audit-screenshots/report.md.
The camera can't be tested here; on the scan pages it checks that the page
loads and the "no camera" message shows.

Setup, once:
    pip install -r requirements-dev.txt
    python -m playwright install chromium

Run from the project folder:
    python scripts/audit_screens.py            (all sizes, both color schemes)
    python scripts/audit_screens.py --quick    (one phone size, light mode)
"""
import argparse
import io
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Plain HTTP on localhost, with a throwaway key: set before the app reads .env
os.environ["APP_ENV"] = "development"
os.environ["FLASK_DEBUG"] = "0"

from flask import redirect, url_for  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import Package, Recipient, Staff, utc_now  # noqa: E402

OUT = ROOT / "audit-screenshots"
SIZES = [(360, 640), (375, 667), (393, 852), (430, 932), (768, 1024)]
SCHEMES = ["light", "dark"]
MIN_TARGET = 44
STAFF = ("audit", "audit-only-password")

TRACKING_PHOTO = "1Z999AA10123456784"
TRACKING_PLAIN = "TBA123456789012"
TRACKING_PICKED_UP = "9400100000000000000001"


# ---- App with seeded data ----

def build_app(tmp):
    uploads = Path(tmp) / "uploads"
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(Path(tmp) / "audit.db"),
        "SECRET_KEY": "audit-only-secret-key-" + "x" * 32,
        "UPLOAD_FOLDER": str(uploads),
    })
    with app.app_context():
        staff = Staff(username=STAFF[0])
        staff.set_password(STAFF[1])
        db.session.add(staff)
        db.session.flush()

        photo_name = "0123456789abcdef0123456789abcdef.jpg"
        Image.new("RGB", (1200, 1600), (52, 96, 140)).save(uploads / photo_name, "JPEG", quality=82)

        now = utc_now()
        db.session.add_all([
            Package(tracking_number=TRACKING_PHOTO, carrier="UPS", checked_in_by=staff.id,
                    checked_in_at=now - timedelta(minutes=12), location_photo_path=photo_name,
                    notes="Large box, left of the door. Fragile: glass inside.",
                    recipient=Recipient(first_name="Alexandria", last_name="Rivera-Montgomery",
                                        mailbox_number="1815")),
            Package(tracking_number=TRACKING_PLAIN, carrier="Amazon", checked_in_by=staff.id,
                    checked_in_at=now - timedelta(hours=2),
                    recipient=Recipient(mailbox_number="2001")),
            Package(tracking_number=TRACKING_PICKED_UP, carrier="USPS", checked_in_by=staff.id,
                    checked_in_at=now - timedelta(days=1), status="checked_out",
                    checked_out_at=now - timedelta(hours=3), checked_out_by=staff.id,
                    checked_out_to="86753091",
                    recipient=Recipient(first_name="Sam", last_name="Lee", mailbox_number="3050")),
        ])
        db.session.commit()
        ids = {p.tracking_number: p.id for p in db.session.scalars(db.select(Package))}

    # Same as run.py: sign-in redirects to "/", which sends staff to the dashboard
    app.add_url_rule("/", "index", lambda: redirect(url_for("dashboard.dashboard")))
    return app, ids


class LogCollector(logging.Handler):
    """Keeps server warnings, errors, and 4xx/5xx request lines for the report."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")


class RequestLog(logging.Handler):
    def __init__(self, collector):
        super().__init__(logging.INFO)
        self.collector = collector

    def emit(self, record):
        message = record.getMessage()
        # werkzeug: '127.0.0.1 - - [date] "GET /x HTTP/1.1" 404 -'
        parts = message.split('"')
        status = parts[2].split()[0] if len(parts) > 2 and parts[2].split() else ""
        if status[:1] in ("4", "5"):
            self.collector.lines.append(f"HTTP {status}: {parts[1]}")


# ---- Checks run inside the page ----

CHECKS_JS = r"""
(minTarget) => {
  const issues = [];
  const doc = document.documentElement;
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none" || el.closest("[hidden], dialog:not([open])")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const describe = (el) => {
    const text = (el.getAttribute("aria-label") || el.innerText || el.value || el.id || el.className || el.tagName)
      .toString().trim().replace(/\s+/g, " ").slice(0, 40);
    return `<${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}> "${text}"`;
  };
  const isFixed = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      if (getComputedStyle(n).position === "fixed") return true;
    }
    return false;
  };

  // 1. Horizontal scrolling
  if (doc.scrollWidth > doc.clientWidth + 1) {
    issues.push(["horizontal-scroll", `page is ${doc.scrollWidth}px wide in a ${doc.clientWidth}px viewport`]);
  }

  // 2. Small tap targets (inline links inside running text are reported separately)
  const targets = document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=option], [tabindex]:not([tabindex="-1"])');
  for (const el of targets) {
    if (!visible(el) || el.closest(".visually-hidden") || el.classList.contains("visually-hidden")) continue;
    const r = el.getBoundingClientRect();
    if (r.width + 0.5 < minTarget || r.height + 0.5 < minTarget) {
      const inline = getComputedStyle(el).display === "inline" && el.closest("p, li");
      issues.push([inline ? "small-target-inline" : "small-target",
                   `${describe(el)} is ${Math.round(r.width)}x${Math.round(r.height)}`]);
    }
  }

  // 3. Content stuck behind a fixed bottom bar, even at full scroll
  window.scrollTo({ top: doc.scrollHeight, behavior: "instant" }); // Bootstrap turns on smooth scrolling
  const bars = [...document.querySelectorAll(".tab-bar, .action-bar, .scan-bottom")].filter(visible);
  const barTop = Math.min(...bars.map((b) => b.getBoundingClientRect().top), window.innerHeight);
  if (bars.length) {
    const main = document.querySelector("main") || document.body;
    for (const el of main.querySelectorAll("p, h1, h2, h3, a, button, input, select, textarea, li, dd, dt, img, label")) {
      if (!visible(el) || isFixed(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.bottom > barTop + 1 && r.top < window.innerHeight) {
        issues.push(["hidden-behind-bar", `${describe(el)} ends at ${Math.round(r.bottom)}px, bar starts at ${Math.round(barTop)}px`]);
      }
    }
  }
  window.scrollTo({ top: 0, behavior: "instant" });

  // 4. Text clipped by, or spilling out of, its box
  for (const el of document.querySelectorAll("body *")) {
    if (["SCRIPT", "STYLE", "svg", "path", "INPUT", "TEXTAREA", "SELECT", "IMG", "VIDEO", "CANVAS"].includes(el.tagName)) continue;
    if (el.closest("#reader, svg, .visually-hidden") || !visible(el)) continue;
    const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!hasText) continue;
    if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) {
      const s = getComputedStyle(el);
      const clipped = s.overflowX !== "visible" || s.textOverflow === "ellipsis";
      issues.push([clipped ? "text-clipped" : "text-overflow",
                   `${describe(el)} needs ${el.scrollWidth}px, has ${el.clientWidth}px`]);
    }
  }
  return issues;
}
"""


def run_axe(page, axe):
    if axe is None:
        return []
    results = axe.run(page)
    return [
        ("axe:" + v["id"], f'{v["impact"]}: {v["help"]} ({len(v["nodes"])} element(s), e.g. {v["nodes"][0]["target"]})')
        for v in results.response["violations"]
    ]


# ---- Main ----

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--quick", action="store_true", help="one phone size, light mode only")
    args = parser.parse_args()
    sizes = [(393, 852)] if args.quick else SIZES
    schemes = ["light"] if args.quick else SCHEMES

    try:
        from axe_playwright_python.sync_playwright import Axe
        axe = Axe()
    except ImportError:
        print("axe-playwright-python isn't installed: skipping accessibility checks.")
        axe = None

    tmp = tempfile.mkdtemp(prefix="captains-mail-audit-")
    app, ids = build_app(tmp)

    collector = LogCollector()
    app.logger.addHandler(collector)
    logging.getLogger("werkzeug").addHandler(RequestLog(collector))
    logging.getLogger("werkzeug").setLevel(logging.INFO)

    server = make_server("127.0.0.1", 0, app, threaded=True)
    base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    pages = [
        ("home", "/dashboard"),
        ("checkin-scan", "/checkin"),
        ("checkin-confirm", "/checkin/confirm?tracking=1Z999AA10100000099"),
        ("photo", f"/checkin/photo/{ids[TRACKING_PLAIN]}"),
        ("checkout-scan", "/checkout"),
        ("checkout-scan-expect", f"/checkout?expect={ids[TRACKING_PHOTO]}"),
        ("checkout-confirm", f"/checkout/confirm?tracking={TRACKING_PHOTO}"),
        ("checkout-confirm-picked-up", f"/checkout/confirm?tracking={TRACKING_PICKED_UP}"),
        ("search-results", "/search?q=1Z999"),
        ("search-none", "/search?q=zzzz"),
        ("package-detail", f"/packages/{ids[TRACKING_PHOTO]}?q=rivera"),
        ("account", "/account"),
    ]

    OUT.mkdir(exist_ok=True)  # each run overwrites its own screenshots and report
    findings = defaultdict(lambda: defaultdict(set))  # page -> (kind, detail) -> sizes
    camera_notes = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Sign in once and share the session, so the 5-per-minute login limit isn't hit
        context = browser.new_context()
        login = context.new_page()
        login.goto(base + "/login")
        login.fill("#username", STAFF[0])
        login.fill("#password", STAFF[1])
        login.click("button[type=submit]")
        login.wait_for_url("**/dashboard")
        state = context.storage_state()
        context.close()

        for scheme in schemes:
            for width, height in sizes:
                label = f"{scheme} {width}x{height}"
                context = browser.new_context(
                    viewport={"width": width, "height": height}, color_scheme=scheme,
                    is_mobile=width < 768, has_touch=True, device_scale_factor=2, storage_state=state,
                )
                page = context.new_page()
                for name, path in pages:
                    page.goto(base + path, wait_until="networkidle")
                    if name.endswith("-scan") or name == "checkout-scan-expect":
                        # No camera here: the fallback message should appear
                        try:
                            page.wait_for_selector("#camera-error:not([hidden])", timeout=5000)
                            camera_notes[name] = page.inner_text("#scan-message-title")
                        except Exception:
                            findings[name][("camera-fallback", "the no-camera message didn't appear")].add(label)
                    else:
                        page.wait_for_timeout(150)
                    for kind, detail in page.evaluate(CHECKS_JS, MIN_TARGET) + run_axe(page, axe):
                        findings[name][(kind, detail)].add(label)
                    shot = OUT / scheme / f"{width}x{height}"
                    shot.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(shot / f"{name}.png"), full_page=True)
                context.close()
                print(f"checked {label}")
        browser.close()
    server.shutdown()

    # ---- Report ----
    all_labels = {f"{s} {w}x{h}" for s in schemes for w, h in sizes}
    out = io.StringIO()
    out.write("# Screen audit\n\n")
    out.write(f"{len(pages)} pages x {len(sizes)} sizes x {len(schemes)} color schemes. "
              f"Screenshots: audit-screenshots/<scheme>/<size>/<page>.png\n\n")
    for name, _ in pages:
        issues = findings.get(name, {})
        out.write(f"## {name}\n\n")
        if name in camera_notes:
            out.write(f"- camera fallback shown: \"{camera_notes[name]}\"\n")
        if not issues:
            out.write("- no problems found\n\n")
            continue
        for (kind, detail), labels in sorted(issues.items()):
            where = "all sizes" if labels == all_labels else ", ".join(sorted(labels))
            out.write(f"- **{kind}** {detail} ({where})\n")
        out.write("\n")
    out.write("## Server log (warnings, errors, 4xx/5xx)\n\n")
    out.write("".join(f"- {line}\n" for line in sorted(set(collector.lines))) or "- nothing\n")

    (OUT / "report.md").write_text(out.getvalue(), encoding="utf-8")
    print(out.getvalue())
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
