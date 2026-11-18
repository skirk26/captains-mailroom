# Captain's Mail design system

Captain's Mail is a phone-first tool for mailroom staff. The design follows
the spirit of Apple's Human Interface Guidelines:

- **Clarity.** One clear task per screen, plain words, and large text.
- **Deference.** Quiet surfaces, so package details and the camera stand out.
- **Consistency.** Every screen has the same navigation bar, buttons, and lists.

It uses Christopher Newport University's blue, grey, and white. It doesn't use
Apple's branding, icons, or fonts.

## Files

| File | What it holds |
|---|---|
| `app/static/css/tokens.css` | Every color, spacing step, radius, type size, and timing, as CSS custom properties. Change the theme here. |
| `app/static/css/components.css` | Shared components, built on Bootstrap 5.3 and the tokens. |
| `app/static/css/style.css` | Page-specific layout: the scan screens, confirm pages, recipient suggestions, home screen, and search. |
| `app/templates/base.html` | The base layout: viewport, safe areas, nav bar, flash messages, and content. |
| `app/templates/_macros.html` | Jinja macros for alerts, flash messages, status chips, and the back button. |
| `app/templates/_package.html` | Macros shared by the pages that show one package: local times, the "Where it's stored" card, and the full-screen photo viewer (with `js/photo_viewer.js`). |
| `app/icons.py` | The `icon("name")` template helper, which inlines an SVG icon. |

Load the stylesheets in this order: Bootstrap, then `tokens.css`, then
`components.css`, then `style.css`.

## Colors

CNU Blue and CNU Silver come from the university's brand guide:
[cnu.edu/brand/colors.html](https://cnu.edu/brand/colors.html). It lists CNU
Blue as `#1b386d` (Pantone 288) and CNU Silver as `#84888b` (Pantone 877).

All contrast ratios are WCAG 2 ratios against the background shown.

### Light mode

| Token | Value | Used for | Contrast |
|---|---|---|---|
| `--color-brand` | `#1B386D` CNU Blue | Primary buttons, navigation, and dashboard row icons | White text on it: 11.5:1 |
| `--color-brand-text` | `#1B386D` CNU Blue | Headings, links, back buttons, and the nav bar's Sign out | 11.5:1 on white, 10.3:1 on page background |
| `--color-brand-pressed` | `#142A52` | Pressed and hover state of brand fills | |
| `--color-brand-tint` | `#E3E9F3` | Secondary button fill and highlighted suggestion | CNU Blue text on it: 9.4:1 |
| `--color-silver` | `#84888B` CNU Silver | Borders, dividers, icons, and disabled states only. **Never text**: it's 3.6:1 on white. | |
| `--color-separator` | CNU Silver at 45% | Hairlines between list rows | |
| `--color-bg` | `#F2F3F5` | Page background (grouped) | |
| `--color-surface` | `#FFFFFF` | Cards, lists, nav bar, and inputs | |
| `--color-text` | `#16191D` | Primary text | 15.9:1 on page background |
| `--color-text-secondary` | `#5F656B` | Captions, hints, and detail values | 5.9:1 on white, 5.3:1 on page background |
| `--color-fill-disabled` | `#84888B` CNU Silver | Disabled button fill | |
| `--color-success` / `-bg` | `#1E6B34` on `#E4F3E8` | "Checked in" and "Barcode scanned" chips, and success alerts | 5.7:1 |
| `--color-neutral` / `-bg` | `#4A5056` on `#E8E9EB` | "Picked up" and "Entered manually" chips | 6.7:1 |
| `--color-warning` / `-bg` | `#7A4A00` on `#FDF0D5` | Warning alerts (amber) | 6.6:1 |
| `--color-danger` / `-bg` | `#A4211A` on `#FBE7E5` | Error alerts (red) | 6.3:1 |
| `--color-danger-fill` | `#B3261E` | Destructive buttons | White text on it: 6.5:1 |
| `--color-info` / `-bg` | `#1B386D` on `#E6ECF6` | Neutral notices, such as "You have been signed out." | 9.7:1 |
| `--color-viewfinder` | `#000000` | Camera viewfinder background, in both modes | |
| `--color-viewfinder-mark` | `#FFFFFF` | Scan-area corner brackets | |
| `--color-scan-bar` | Black at 55% | Top bar over the camera | White text on it: 4.7:1 even over a white label, 21:1 over a dark image |
| `--color-scan-success` | `#34C759` | Scan frame after a good read, always with a check icon | 9.5:1 against the black viewfinder |
| `--color-scrim` | Black at 45% | Dimmed page behind a bottom sheet | |

Red isn't a CNU brand color. The brand guide asks for it to be kept out of
official materials. Here it's only a functional signal for errors and
destructive actions, never decoration.

### Dark mode

Dark-mode values switch on automatically with `prefers-color-scheme: dark`.
Brand and status colors get lighter so text keeps at least 4.5:1.

| Token | Value | Contrast |
|---|---|---|
| `--color-bg` / `--color-surface` | `#000000` / `#1C1C1E` | |
| `--color-text` | `#F2F3F5` | 15.3:1 on surface |
| `--color-text-secondary` | `#A3A8AD` | 7.1:1 on surface |
| `--color-brand` (fills) | `#2B5299`, CNU Blue lightened | White text on it: 7.6:1 |
| `--color-brand-text` | `#9DB8EC` | 8.5:1 on surface |
| `--color-success` | `#7DD69A` on `#16301F` | 8.1:1 |
| `--color-neutral` | `#C3C7CB` on `#2A2C2F` | 8.2:1 |
| `--color-warning` | `#F5C46B` on `#33270F` | 9.0:1 |
| `--color-danger` | `#FF9C94` on `#3A1715` | 8.0:1 |

### Status colors

Status is never shown by color alone. Each chip and alert pairs its color
with an icon and a word:

| Status | Color | Icon | Word |
|---|---|---|---|
| Checked in | Green | `check-circle-fill` | "Checked in" |
| Picked up | Grey | `bag-check-fill` | "Picked up" |
| Warning | Amber | `exclamation-triangle-fill` | The alert's message |
| Error | Red | `exclamation-octagon-fill` | The alert's message |

## Typography

The app uses the system font stack, so nothing is downloaded:
`-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif`.

| Style | Class | Size | Weight |
|---|---|---|---|
| Large title | `.large-title` | 34px | Bold |
| Title 1 | `.title-1` | 28px | Bold |
| Title 2 | `.title-2` | 22px | Bold |
| Title 3 | `.title-3` | 20px | Semibold |
| Headline | `.headline` | 17px | Semibold |
| Body | (default) | 17px | Regular |
| Subheadline | `.subheadline` | 15px | Regular |
| Footnote | `.footnote` | 13px | Regular |
| Caption | `.caption` | 12px | Regular |

Inputs use 17px text. iOS zooms into any field whose text is under 16px.

## Spacing, shape, and motion

- **Spacing** follows a 4px grid: `--space-1` (4px) to `--space-10` (40px).
  Page margins and row padding are `--space-4` (16px).
- **Radii:** 12px for buttons and inputs, 14px for cards and lists, and full
  rounding for chips.
- **Touch targets:** every tappable element is at least 44 × 44px
  (`--touch-target`).
- **Motion:** short transitions (`--duration-fast`, 120ms). With
  `prefers-reduced-motion: reduce`, all transitions and animations are
  switched off.

## Components

### Navigation bar

`base.html` shows a nav bar when a page fills in `nav_title`. The back
button goes on the left, the title is centered, and an optional action goes
on the right:

```jinja
{% block nav_leading %}{{ nav_back(url_for("dashboard.dashboard")) }}{% endblock %}
{% block nav_title %}Check In{% endblock %}
{% block nav_trailing %}<button class="nav-bar-button">Done</button>{% endblock %}
```

The bar is sticky and translucent, and it pads itself below the notch with
`env(safe-area-inset-top)`.

### Tab bar

The main sections, fixed to the bottom of the screen where a thumb can reach
them: Home, Check In, Check Out, Find, and Account. A page shows it by setting
`tab` at the top of its template; the current tab gets `aria-current="page"`,
the brand color, and a tinted pill behind its icon. Each tab is 56px tall, and
the bar pads itself above the home indicator.

```jinja
{% extends "base.html" %}
{% set tab = "find" %}
```

Scan, confirm, and photo screens leave `tab` unset. They're focused flows with
their own bottom actions, and two bottom bars would collide.

### Buttons

| Class | Use |
|---|---|
| `btn btn-primary` | The main action on a screen (CNU Blue) |
| `btn btn-secondary` | Other actions (tinted) |
| `btn btn-destructive` | Actions that remove or undo something (red) |
| `btn-lg w-100` | A full-width, 50px-tall button at the bottom of a form |

Disabled buttons use a CNU Silver fill. While a form submits, add
`aria-busy="true"` along with `disabled` to show a spinner before the label.
The check-in and pickup forms already do this.

### Inset grouped list

These are the cards for forms and detail rows. The label sits on the left and
the value or field on the right, with hairlines between rows:

```html
<div class="inset-section">
  <p class="inset-header">Recipient</p>
  <div class="inset-list">
    <div class="inset-row">
      <label class="inset-label" for="box">Box number</label>
      <input class="inset-input" id="box" name="box_number">
    </div>
    <div class="inset-row">
      <span class="inset-label">Carrier</span>
      <span class="inset-value">UPS</span>
    </div>
  </div>
  <p class="inset-footer">Help text goes here.</p>
</div>
```

A row can also be a link, with an icon, title, and chevron, as on the
dashboard.

### Segmented control

A row of two to four filter choices, built as links so each choice has its own
URL. The current one has `aria-current="page"`. Each segment is at least 44px
tall:

```html
<nav class="segmented-control" aria-label="Filter by status">
  <a href="?status=all" class="segmented-control-item" aria-current="page">All</a>
  <a href="?status=waiting" class="segmented-control-item">Waiting for pickup</a>
</nav>
```

### Field rows

Forms use the inset list with the label above each field. A server message
appears directly under its field (with an icon, a red bar, `aria-invalid`, and
`aria-describedby`), and the form also gets a summary alert at the top.

```html
<div class="inset-list">
  <div class="inset-row inset-field">
    <label class="inset-field-label" for="box-number">Box number</label>
    <input class="inset-field-input" id="box-number" name="box_number">
  </div>
</div>
```

A native `<select class="inset-select">` at the end of an ordinary row opens
the phone's own picker.

### Action bar

The screen's main button, fixed to the bottom above the home indicator. Put
`<div class="action-bar"><div class="action-bar-inner">…</div></div>` inside
the page content; the content gets enough bottom padding that nothing hides
behind it. Used for Save Check-In, Mark as Picked Up, and the photo buttons.

### Toasts

Brief confirmations at the top of the screen, such as "Picked up: …23456784."
Flash a message with the category `"toast"`; `toasts()` in `_macros.html`
shows it in an `aria-live` region, and `js/toasts.js` hides it after 3 seconds
or when its close button is tapped. Everything else (warnings, errors) stays
an inline alert.

### Bottom sheet

A modal `<dialog class="sheet">` that rises from the bottom, with a handle,
a title, and a 44px close button. Escape closes it (built into `<dialog>`),
and so does tapping the dimmed area outside the panel. The scan screens use it
for typing a tracking number; the camera pauses while it's open.

### Status chips and alerts

Both come from `_macros.html`, so the icon is never left out:

```jinja
{% call status_chip("success", "check-circle-fill") %}Checked in{% endcall %}
{% call alert("danger") %}No package found with this tracking number.{% endcall %}
{{ flash_messages() }}
```

### Icons

Icons come from [Bootstrap Icons](https://icons.getbootstrap.com/) 1.11.3
(MIT license). The SVGs are stored in
`app/static/vendor/bootstrap-icons-1.11.3/` with their license file. The
`icon("name")` helper inlines one and sizes it to the text. To add an icon,
download its SVG from the same version into that folder.

Apple's SF Symbols aren't used.

## Pages

| Page | Layout |
|---|---|
| Sign in, error pages | Extend `base.html` and use the components above (no tab bar) |
| Home (`/dashboard`) | Extends `base.html` with the tab bar: search field, Check In / Check Out tiles, Today counts, and recent activity |
| Account | Extends `base.html` with the tab bar: the staff member's name and Sign out |
| Search (Find tab) | Extends `base.html` with the tab bar: a search field, a segmented status filter, and result rows that link to the package (64px thumbnail, details, status chip) |
| Package detail | Extends `base.html` with the tab bar (Find stays current): the storage card and photo viewer from `_package.html`, a status chip, and inset detail rows |
| Storage photo (after check-in) | Extends `base.html`: a package card, the photo frame, and Take Photo / Retake / Use Photo |
| Scan pages (check in, check out) | Extend `_scan_page.html`: full-screen camera under a translucent top bar (back, title, flashlight when supported), the scan frame, alerts and banners floating over the camera, a bottom panel, and the manual-entry bottom sheet. No tab bar. |
| Confirm pages (check in, pickup) | Extend `base.html` (no tab bar): a header card or the storage card, field rows or read-only inset rows, and an action bar |

## Scan feedback

On a good read the scan frame turns green with a check icon for about 300 ms,
and phones that support it give a 30 ms vibration, before the confirm page
opens. With `prefers-reduced-motion` the frame still turns green; it just
doesn't animate. Camera problems (blocked permission, no camera, camera in
use, an insecure connection) show as an inline warning alert with a
plain-language fix and a Try again button.

## Contrast check

Every text pair in the palette was checked against WCAG 2 (4.5:1 for text),
in light and dark mode. All pass except one:

| Pair | Light | Dark | Note |
|---|---|---|---|
| Disabled button text (`--color-on-disabled` on `--color-fill-disabled`) | 3.6:1 | 5.0:1 | WCAG exempts disabled controls; the button's label still reads, and it's never the only way to know an action is unavailable. |

Graphics need 3:1: the green scan frame is 9.5:1 on the black camera view, and
the sheet handle (CNU Silver, decorative) is 3.6:1 in light mode and 4.8:1 in
dark mode.
