# Inline SVG icons for templates: {{ icon("chevron-left") }}.
# The SVGs are Bootstrap Icons (MIT), stored in static/vendor/bootstrap-icons-1.11.3/.
import re
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup

ICON_DIR = Path(__file__).parent / "static" / "vendor" / "bootstrap-icons-1.11.3"
ICON_NAME = re.compile(r"^[a-z0-9-]+$")


@lru_cache(maxsize=None)
def _svg_body(name):
    if not ICON_NAME.match(name):
        raise ValueError(f"Bad icon name: {name!r}")
    svg = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    # Keep only what's inside <svg>…</svg>; icon() writes its own opening tag
    return svg[svg.index(">") + 1 : svg.rindex("</svg>")].strip()


def icon(name, class_name=""):
    classes = f"icon {class_name}".strip()
    # Decorative: the word next to it carries the meaning for screen readers
    return Markup(
        f'<svg class="{classes}" viewBox="0 0 16 16" aria-hidden="true" focusable="false">'
        f"{_svg_body(name)}</svg>"
    )
