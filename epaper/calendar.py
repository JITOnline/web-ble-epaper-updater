"""
Generate an 800x480 calendar day-view image from an iCal feed.

Designed for BWR (black/white/red) e-paper displays.
- Red: meeting blocks and current-time marker
- Black: text, grid lines, headers
- White: free time
"""
import logging
from datetime import datetime, date, timedelta, time
from PIL import Image, ImageDraw, ImageFont

import requests
import icalendar
import recurring_ical_events
from dateutil import tz as dateutil_tz

logger = logging.getLogger(__name__)

# ── Layout constants ──────────────────────────────────────────────
IMG_W, IMG_H = 800, 480

# The day grid runs from HOUR_START to HOUR_END (e.g. 7 AM – 22 PM)
HOUR_START = 7
HOUR_END = 22

HEADER_H = 64          # top bar for date + all-day events
FOOTER_H = 0
SIDEBAR_W = 56         # left column for hour labels

GRID_LEFT = SIDEBAR_W
GRID_TOP = HEADER_H
GRID_W = IMG_W - SIDEBAR_W
GRID_H = IMG_H - HEADER_H - FOOTER_H
TOTAL_HOURS = HOUR_END - HOUR_START  # visible hours

# ── Colors (RGB) ──────────────────────────────────────────────────
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
LIGHT_GRAY = (200, 200, 200)


def _y_for_time(dt_time):
    """Map a time-of-day to a y pixel inside the grid area."""
    minutes_from_start = (dt_time.hour - HOUR_START) * 60 + dt_time.minute
    total_minutes = TOTAL_HOURS * 60
    frac = max(0.0, min(1.0, minutes_from_start / total_minutes))
    return int(GRID_TOP + frac * GRID_H)


def _try_load_font(size):
    """Try to load a TTF font, falling back to the default bitmap font."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def fetch_events_today(ical_url, local_tz=None):
    """Fetch and parse an iCal feed, returning today's events as dicts.

    Each event dict has keys: summary, start, end, all_day.
    start/end are datetime objects in the local timezone.
    """
    if local_tz is None:
        local_tz = dateutil_tz.tzlocal()

    resp = requests.get(ical_url, timeout=15)
    resp.raise_for_status()

    cal = icalendar.Calendar.from_ical(resp.text)

    today = date.today()
    tomorrow = today + timedelta(days=1)

    # recurring_ical_events handles recurring events, EXDATE, etc.
    raw_events = recurring_ical_events.of(cal).between(
        datetime.combine(today, time.min).replace(tzinfo=local_tz),
        datetime.combine(tomorrow, time.min).replace(tzinfo=local_tz),
    )

    events = []
    for ev in raw_events:
        summary = str(ev.get("SUMMARY", "Busy"))
        dtstart = ev.get("DTSTART").dt
        dtend_prop = ev.get("DTEND")

        # Determine if all-day
        if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
            events.append({
                "summary": summary,
                "start": datetime.combine(
                    dtstart, time.min
                ).replace(tzinfo=local_tz),
                "end": datetime.combine(
                    dtstart + timedelta(days=1), time.min
                ).replace(tzinfo=local_tz),
                "all_day": True,
            })
            continue

        # Ensure timezone-aware
        if dtstart.tzinfo is None:
            dtstart = dtstart.replace(tzinfo=local_tz)
        else:
            dtstart = dtstart.astimezone(local_tz)

        if dtend_prop is not None:
            dtend = dtend_prop.dt
            if isinstance(dtend, date) and not isinstance(dtend, datetime):
                dtend = datetime.combine(dtend, time.min).replace(
                    tzinfo=local_tz
                )
            elif dtend.tzinfo is None:
                dtend = dtend.replace(tzinfo=local_tz)
            else:
                dtend = dtend.astimezone(local_tz)
        else:
            # Default 1-hour event
            dtend = dtstart + timedelta(hours=1)

        events.append({
            "summary": summary,
            "start": dtstart,
            "end": dtend,
            "all_day": False,
        })

    # Sort by start time
    events.sort(key=lambda e: e["start"])
    return events


# ── Drawing helpers ───────────────────────────────────────────────

def _draw_header(draw, now, all_day_events, fonts):
    """Draw the black header bar with date and all-day events."""
    draw.rectangle([0, 0, IMG_W, HEADER_H], fill=BLACK)
    date_str = now.strftime("%A, %B %-d, %Y")
    draw.text((16, 12), date_str, fill=WHITE, font=fonts["header"])

    if all_day_events:
        allday_text = (
            "All day: "
            + " · ".join(e["summary"] for e in all_day_events)
        )
        draw.text((16, 40), allday_text, fill=RED, font=fonts["allday"])


def _draw_hour_grid(draw, fonts):
    """Draw hour labels, horizontal grid lines, and half-hour dashes."""
    for h in range(HOUR_START, HOUR_END + 1):
        y = _y_for_time(time(h, 0))
        label = f"{h:02d}"
        draw.text((8, y - 7), label, fill=BLACK, font=fonts["hour"])
        line_color = LIGHT_GRAY if h != HOUR_START else BLACK
        draw.line(
            [(GRID_LEFT, y), (IMG_W, y)], fill=line_color, width=1
        )

    # Half-hour dashed lines
    for h in range(HOUR_START, HOUR_END):
        y = _y_for_time(time(h, 30))
        for x in range(GRID_LEFT, IMG_W, 8):
            draw.line(
                [(x, y), (min(x + 3, IMG_W), y)],
                fill=LIGHT_GRAY, width=1,
            )

    # Left border of grid
    draw.line(
        [(GRID_LEFT, GRID_TOP), (GRID_LEFT, GRID_TOP + GRID_H)],
        fill=BLACK, width=1,
    )


def _compute_column_layout(timed_events):
    """Assign each timed event a column index for side-by-side rendering.

    Returns a list of (col_index, total_cols, event) tuples.
    """
    def _events_overlap(a, b):
        return a["start"] < b["end"] and b["start"] < a["end"]

    # First pass: assign column indices
    columns = []
    active_cols = []

    for ev in timed_events:
        ev_start = ev["start"]
        ev_end = ev["end"]
        active_cols = [
            (et, ci) for et, ci in active_cols if et > ev_start
        ]
        used = {ci for _, ci in active_cols}
        col = 0
        while col in used:
            col += 1
        active_cols.append((ev_end, col))
        columns.append((col, ev))

    # Second pass: group overlapping events, compute total_cols
    groups = []
    for i, (_, ev) in enumerate(columns):
        placed = False
        for group in groups:
            if any(_events_overlap(ev, columns[j][1]) for j in group):
                group.add(i)
                placed = True
                break
        if not placed:
            groups.append({i})

    col_info = [None] * len(columns)
    for group in groups:
        total = max(columns[j][0] for j in group) + 1
        for j in group:
            col_info[j] = (columns[j][0], total)

    return [
        (col_info[i][0], col_info[i][1], ev)
        for i, (_, ev) in enumerate(columns)
    ]


def _draw_event_block(draw, ev, col_idx, total_cols, fonts):
    """Draw a single event block on the calendar grid."""
    event_pad = 3
    ev_start = max(ev["start"].time(), time(HOUR_START, 0))
    ev_end = min(ev["end"].time(), time(HOUR_END, 0))
    if ev_end <= ev_start:
        return

    y1 = _y_for_time(ev_start) + 1
    y2 = _y_for_time(ev_end) - 1
    if y2 - y1 < 4:
        y2 = y1 + 4

    col_w = GRID_W // total_cols
    x1 = GRID_LEFT + col_idx * col_w + event_pad
    x2 = GRID_LEFT + (col_idx + 1) * col_w - event_pad

    draw.rectangle([x1, y1, x2, y2], fill=RED)
    draw.rectangle([x1, y1, x2, y2], outline=BLACK, width=1)

    text_x = x1 + 4
    text_y = y1 + 2
    block_h = y2 - y1

    time_str = (
        f"{ev['start'].strftime('%-H:%M')}"
        f"–{ev['end'].strftime('%-H:%M')}"
    )

    if block_h > 30:
        draw.text(
            (text_x, text_y), ev["summary"][:30],
            fill=WHITE, font=fonts["event"],
        )
        draw.text(
            (text_x, text_y + 18), time_str,
            fill=WHITE, font=fonts["event_small"],
        )
    elif block_h > 16:
        draw.text(
            (text_x, text_y), ev["summary"][:20],
            fill=WHITE, font=fonts["event_small"],
        )


def _draw_current_time_marker(draw, now):
    """Draw the red arrow + line at the current time."""
    if HOUR_START <= now.hour < HOUR_END:
        y_now = _y_for_time(now.time())
        draw.polygon(
            [(GRID_LEFT, y_now),
             (GRID_LEFT + 8, y_now - 4),
             (GRID_LEFT + 8, y_now + 4)],
            fill=RED,
        )
        draw.line(
            [(GRID_LEFT + 8, y_now), (IMG_W, y_now)],
            fill=RED, width=2,
        )


# ── Main entry point ─────────────────────────────────────────────

def generate_calendar_image(ical_url, local_tz=None):
    """Generate an 800x480 PIL Image showing today's calendar.

    Returns a PIL.Image.Image in RGB mode.
    """
    if local_tz is None:
        local_tz = dateutil_tz.tzlocal()

    now = datetime.now(tz=local_tz)
    events = fetch_events_today(ical_url, local_tz=local_tz)
    all_day_events = [e for e in events if e["all_day"]]
    timed_events = [e for e in events if not e["all_day"]]

    logger.info(
        f"Calendar: {len(timed_events)} timed, "
        f"{len(all_day_events)} all-day for {now.date()}"
    )

    fonts = {
        "header": _try_load_font(22),
        "hour": _try_load_font(13),
        "event": _try_load_font(14),
        "event_small": _try_load_font(11),
        "allday": _try_load_font(13),
    }

    img = Image.new("RGB", (IMG_W, IMG_H), WHITE)
    draw = ImageDraw.Draw(img)

    _draw_header(draw, now, all_day_events, fonts)
    _draw_hour_grid(draw, fonts)

    layout = _compute_column_layout(timed_events)
    for col_idx, total_cols, ev in layout:
        _draw_event_block(draw, ev, col_idx, total_cols, fonts)

    _draw_current_time_marker(draw, now)

    if not timed_events and not all_day_events:
        cx = GRID_LEFT + GRID_W // 2
        cy = GRID_TOP + GRID_H // 2
        draw.text(
            (cx - 60, cy - 10), "No meetings today",
            fill=BLACK, font=fonts["header"],
        )

    return img
