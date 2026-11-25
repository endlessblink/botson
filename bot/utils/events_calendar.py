"""Month calendar picture of the community's published events.

Two separate parts:
- `build_month` turns a list of events into a plain month model (no drawing, no database).
- `render_png` draws that model with Pillow (the server has no browser).

Wording lives in `copy.events_calendar`, looks in `events_calendar` (config/settings.yaml).
"""
from __future__ import annotations

import calendar
import io
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import get_settings
from .copy import load_copy

logger = logging.getLogger(__name__)

FONT_PATH = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "heebo" / "Heebo-VariableFont_wght.ttf"


def calendar_cfg() -> dict:
    return (get_settings() or {}).get("events_calendar") or {}


def local_now() -> datetime:
    return datetime.now(ZoneInfo(calendar_cfg()["timezone"]))


def parse_month(raw: str | None, now: datetime) -> tuple[int, int]:
    """'YYYY-MM' to (year, month); nothing means the current month. ValueError when malformed."""
    if raw is None or raw == "":
        return now.year, now.month
    m = re.fullmatch(r"(20[0-9]{2})-(0[1-9]|1[0-2])", raw)
    if not m:
        raise ValueError("month must look like YYYY-MM")
    return int(m[1]), int(m[2])


# ── Month model ──────────────────────────────────────────────

@dataclass(frozen=True)
class CalEvent:
    title: str
    day: date
    start: time | None = None
    location: str = ""
    organizer: str = ""
    past: bool = False
    cover: str = ""  # path inside the media folder

    @property
    def sort_key(self):
        return (self.day, self.start or time.min, self.title)


@dataclass
class CalDay:
    day: date
    in_month: bool
    is_today: bool
    is_past: bool
    weekend: bool
    events: list[CalEvent] = field(default_factory=list)


@dataclass
class MonthModel:
    year: int
    month: int
    weeks: list[list[CalDay]]
    count: int
    next_event: CalEvent | None
    upcoming: list[CalEvent] = field(default_factory=list)  # detail rows under the grid, next one first


def _to_event(row: dict, now: datetime) -> CalEvent | None:
    try:
        day = date.fromisoformat(str(row.get("event_date") or "").strip())
    except ValueError:
        return None
    m = re.match(r"(\d{1,2}):(\d{2})", str(row.get("event_time") or "").strip())
    start = time(int(m[1]), int(m[2])) if m and int(m[1]) < 24 and int(m[2]) < 60 else None
    # Without a time, an event counts as upcoming until its day is over.
    past = datetime.combine(day, start) < now if start else day < now.date()
    clean = lambda key: " ".join(str(row.get(key) or "").split())
    return CalEvent(clean("title"), day, start, clean("location"), clean("organizer"), past, str(row.get("cover_path") or ""))


def build_month(year: int, month: int, events: list[dict], now: datetime) -> MonthModel:
    """Plain month model. `now` is the community's local time."""
    now = now.replace(tzinfo=None)
    parsed = sorted((e for e in (_to_event(r, now) for r in events) if e), key=lambda e: e.sort_key)
    mine = [e for e in parsed if (e.day.year, e.day.month) == (year, month)]
    weeks = [
        [CalDay(day=d, in_month=d.month == month, is_today=d == now.date(), is_past=d < now.date(),
                weekend=i >= 5, events=[e for e in mine if e.day == d])
         for i, d in enumerate(week)]
        for week in calendar.Calendar(firstweekday=calendar.SUNDAY).monthdatescalendar(year, month)
    ]
    # Next up belongs to the month shown; once the current month has run out of
    # events, look ahead to the following ones.
    upcoming = [e for e in mine if not e.past]
    if not upcoming and mine and (year, month) == (now.year, now.month):
        upcoming = [e for e in parsed if not e.past][:1]
    return MonthModel(year, month, weeks, len(mine), upcoming[0] if upcoming else None, upcoming)


# ── Renderer ─────────────────────────────────────────────────

class _Painter:
    """Drawing helpers: scaled sizes, right-to-left text, wrapping, smooth round corners."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.scale = int(cfg["width"]) / 1080  # the design's sizes are given at 1080px
        self.colors = cfg["colors"]
        self._fonts: dict = {}
        self._glyphs: dict = {}
        self.img = None
        self.media_dir: Path | None = None
        self._covers: dict = {}

    def cover(self, path: str, w: int, h: int):
        """The event's picture cropped to w×h, or None (no picture, missing, broken, outside the media folder)."""
        from PIL import Image, ImageOps

        if not path or self.media_dir is None:
            return None
        key = (path, w, h)
        if key not in self._covers:
            self._covers[key] = None
            try:
                root = self.media_dir.resolve()
                file = (root / path).resolve()
                if file.is_relative_to(root) and file.is_file():
                    with Image.open(file) as im:
                        if im.width * im.height <= int(self.cfg["max_cover_pixels"]):
                            im.draft("RGB", (w * 2, h * 2))
                            im = ImageOps.exif_transpose(im).convert("RGBA")
                            flat = Image.new("RGBA", im.size, self.colors["card"])
                            flat.alpha_composite(im)
                            self._covers[key] = ImageOps.fit(flat.convert("RGB"), (w, h), Image.Resampling.LANCZOS)
            except Exception:  # a bad picture never costs the whole calendar
                logger.warning("events calendar: could not draw a cover", exc_info=True)
        return self._covers[key]

    def picture(self, x: float, y: float, im, radius: int, *, faded: bool = False) -> None:
        from PIL import Image, ImageDraw

        if faded:
            im = Image.blend(im, Image.new("RGB", im.size, self.colors["past_chip"]), self.cfg["past_cover_fade"])
        mask = Image.new("L", (im.width * 4, im.height * 4), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width * 4 - 1, im.height * 4 - 1), radius * 4, fill=255)
        self.img.paste(im, (round(x), round(y)), mask.resize(im.size, Image.Resampling.LANCZOS))

    def px(self, n: float) -> int:
        return round(n * self.scale)

    def sp(self, name: str) -> int:
        return self.px(self.cfg["spacing"][name])

    def font(self, style: str):
        from PIL import ImageFont

        if style not in self._fonts:
            size, weight = self.cfg["fonts"][style]
            f = ImageFont.truetype(str(FONT_PATH), self.px(size), layout_engine=ImageFont.Layout.BASIC)
            f.set_variation_by_axes([weight])
            self._fonts[style] = f
        return self._fonts[style]

    def _has_glyph(self, ch: str) -> bool:
        if ch not in self._glyphs:
            f = self.font("title")
            shape = lambda c: (f.getbbox(c), bytes(f.getmask(c)))
            self._glyphs[ch] = shape(ch) != shape("\U0010fffd")  # private-use char: the "missing" box
        return self._glyphs[ch]

    def clean(self, text: str) -> str:
        """Drop what the font cannot draw (emoji, invisible marks, niqqud)."""
        text = unicodedata.normalize("NFC", str(text or ""))
        kept = "".join(" " if ch.isspace() else ch for ch in text
                       if ch.isspace() or (unicodedata.category(ch) not in ("Mn", "Me", "Cf", "Cc", "Cs", "Co", "Cn")
                                           and self._has_glyph(ch)))
        return " ".join(kept.split())

    def width(self, text: str, style: str) -> float:
        return self.font(style).getlength(text)

    def line_h(self, style: str) -> int:
        size, _ = self.cfg["fonts"][style]
        return self.px(size * self.cfg["line_heights"][style])

    def fit(self, text: str, style: str, room: float) -> str:
        """One line, cut with an ellipsis when it is too long."""
        text = self.clean(text)
        if self.width(text, style) <= room:
            return text
        while text and self.width(text + "…", style) > room:
            text = text[:-1]
        return text.rstrip() + "…"

    def wrap(self, text: str, style: str, room: float, max_lines: int) -> list[str]:
        """Logical-order lines; a word wider than the room is split, the rest ends with an ellipsis."""
        lines, line, words = [], "", self.clean(text).split()
        while words:
            word = words.pop(0)
            trial = f"{line} {word}".strip()
            if self.width(trial, style) <= room:
                line = trial
            elif line:
                lines.append(line)
                line = ""
                words.insert(0, word)
            else:
                n = len(word)
                while n > 1 and self.width(word[:n], style) > room:
                    n -= 1
                lines.append(word[:n])
                if word[n:]:
                    words.insert(0, word[n:])
            if len(lines) > max_lines:
                break
        if line:
            lines.append(line)
        if len(lines) > max_lines:
            last = lines[max_lines - 1]
            while last and self.width(last + "…", style) > room:
                last = last[:-1]
            lines = lines[: max_lines - 1] + [last.rstrip() + "…"]
        return lines

    def text(self, x: float, top: float, text: str, style: str, color: str, anchor: str = "r",
             base_dir: str = "R") -> None:
        """Draw one line. `x` is its right edge ('r'), centre ('m') or left edge ('l')."""
        from bidi.algorithm import get_display  # the package-level one does not mirror brackets
        from PIL import ImageDraw

        f = self.font(style)
        ascent, descent = f.getmetrics()
        baseline = top + (self.line_h(style) - ascent - descent) / 2 + ascent
        ImageDraw.Draw(self.img).text((x, round(baseline)), get_display(text, base_dir=base_dir), font=f,
                                      fill=self.colors[color], anchor=anchor + "s")

    def box(self, x: float, y: float, w: float, h: float, radius: int, color: str, *,
            outline: int = 0, shadow: bool = False) -> None:
        from PIL import Image, ImageDraw, ImageFilter

        x, y, w, h = round(x), round(y), round(w), round(h)
        ss = 4  # supersampling: Pillow's own round corners are jagged
        mask = Image.new("L", (w * ss, h * ss), 0)
        d = ImageDraw.Draw(mask)
        d.rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius * ss, fill=255)
        if outline:
            o = outline * ss
            d.rounded_rectangle((o, o, w * ss - 1 - o, h * ss - 1 - o), max(0, radius * ss - o), fill=0)
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)
        if shadow:
            sh = self.cfg["shadow"]
            soft = Image.new("L", self.img.size, 0)
            soft.paste(mask.point(lambda v: int(v * sh["opacity"])), (x, y + self.px(sh["offset"])))
            soft = soft.filter(ImageFilter.GaussianBlur(self.px(sh["blur"]) / 2))
            self.img.paste(self.colors["shadow"], (0, 0), soft)
        self.img.paste(self.colors[color], (x, y), mask)


def _copy(key: str, **fmt):
    return load_copy("events_calendar", key, **fmt)


def _hhmm(t: time | None) -> str:
    return t.strftime("%H:%M") if t else ""


def render_png(model: MonthModel, media_dir: Path | None = None) -> bytes:
    from PIL import Image

    cfg = calendar_cfg()
    p = _Painter(cfg)
    p.media_dir = media_dir
    sp, colors = p.sp, p.colors

    width = int(cfg["width"])
    card_x, card_w = sp("page_x"), width - 2 * sp("page_x")
    gap, card_pad = sp("gap"), sp("card_pad")
    inner_right = card_x + card_w - card_pad
    inner_w = card_w - 2 * card_pad
    edges = [round(inner_w * i / 7 + gap * i / 7) for i in range(8)]  # exact columns, no rounding drift
    pad_x, pad_y, item_gap = sp("cell_pad_x"), sp("cell_pad_y"), sp("cell_gap")
    chip_x, chip_top, chip_bottom, chip_mid = (sp(k) for k in ("chip_pad_x", "chip_top", "chip_bottom", "chip_mid"))
    num = sp("number_box")
    col_w = [edges[i + 1] - edges[i] - gap for i in range(7)]
    chip_room = min(col_w) - 2 * pad_x - 2 * chip_x
    today_label = p.clean(_copy("today"))

    thumb_w, thumb_h = min(col_w) - 2 * pad_x, sp("chip_cover_h")

    def layout(day: CalDay):
        """What goes inside one day: chips as (event, title lines, height), the hidden count, total height."""
        shown = day.events[: int(cfg["max_chips_per_day"])]
        h = 2 * pad_y + num
        if day.is_today and day.in_month:
            h += item_gap + p.line_h("today")
        chips = []
        for e in shown:
            lines = p.wrap(e.title, "title", chip_room, int(cfg["max_title_lines"]))
            ch = chip_top + chip_bottom + len(lines) * p.line_h("title")
            if e.start:
                ch += p.line_h("time") + chip_mid
            if p.cover(e.cover, thumb_w, thumb_h):
                ch += thumb_h
            chips.append((e, lines, ch))
            h += item_gap + ch
        hidden = len(day.events) - len(shown)
        if hidden:
            h += item_gap + p.line_h("more")
        return chips, hidden, h

    plans = [[layout(d) for d in week] for week in model.weeks]
    row_h = [max([p.px(cfg["min_cell_height"])] + [c[2] for c in week]) for week in plans]

    # Vertical plan (top to bottom).
    y_month = sp("page_top")
    y_sub = y_month + p.line_h("month") + sp("subtitle_gap")
    y_card = y_sub + p.line_h("subtitle") + sp("header_gap")
    y_dow = y_card + sp("card_top")
    dow_h = sp("dow_top") + p.line_h("dow") + sp("dow_bottom")
    y_grid = y_dow + dow_h + gap
    grid_h = sum(row_h) + gap * (len(row_h) - 1)
    card_h = y_grid - y_card + grid_h + card_pad
    y_next = y_card + card_h + sp("next_gap")

    # The card under the grid: next up, an empty-state line, or nothing.
    ev = model.next_event
    next_pad_x, next_pad_y = sp("next_pad_x"), sp("next_pad_y")
    rows, empty_lines, next_h = [], [], 0
    cover_w, cover_h, inner_gap = sp("next_cover_w"), sp("next_cover_h"), sp("next_inner_gap")
    tag = p.clean(_copy("next_up"))
    tag_w = round(p.width(tag, "tag")) + 2 * sp("tag_pad_x")
    tag_h = p.line_h("tag") + 2 * sp("tag_pad_y")

    def detail_row(e: CalEvent, first: bool):
        """(event, title lines, place and organizer, picture, height) of one upcoming event."""
        pic = p.cover(e.cover, cover_w, cover_h)
        room = card_w - 2 * next_pad_x - (cover_w + inner_gap if pic else 0)
        when = {"day": _copy("day_names")[(e.day.weekday() + 1) % 7], "date": f"{e.day.day}.{e.day.month}"}
        line = (_copy("next_line", time=_hhmm(e.start), title=e.title, **when) if e.start
                else _copy("next_line_no_time", title=e.title, **when))
        lines = p.wrap(line, "next", room, int(cfg["next_max_lines"]))
        meta = p.clean(e.location)
        if cfg.get("show_organizer") and p.clean(e.organizer):
            sep = _copy("meta_separator")
            full = (meta + sep if meta else "") + p.clean(_copy("organizer", name=e.organizer))
            if p.width(full, "meta") <= room:
                meta = full
        meta = p.fit(meta, "meta", room)
        text_h = len(lines) * p.line_h("next") + (sp("meta_gap") + p.line_h("meta") if meta else 0)
        if first:
            text_h += tag_h + sp("tag_gap")
        return e, lines, meta, pic, text_h, max(text_h, cover_h if pic else 0) + 2 * next_pad_y

    if ev:
        rows = [detail_row(e, i == 0) for i, e in enumerate(model.upcoming[: int(cfg["max_detail_rows"])])]
        next_h = sum(r[-1] for r in rows) + sp("rule") * (len(rows) - 1)
    elif not model.count:
        empty_lines = p.wrap(_copy("empty"), "empty", card_w - 2 * next_pad_x, int(cfg["next_max_lines"]))
        next_h = 2 * next_pad_y + len(empty_lines) * p.line_h("empty")

    y_footer = y_next + (next_h + sp("footer_gap") if next_h else 0)
    height = y_footer + p.line_h("footer") + sp("page_bottom")
    p.img = Image.new("RGB", (width, height), colors["background"])

    # Header.
    right = card_x + card_w - sp("header_x")
    month_name = p.clean(_copy("month_names")[model.month - 1])
    p.text(right, y_month, month_name, "month", "ink")
    p.text(right - p.width(month_name + " ", "month"), y_month, str(model.year), "month", "accent")
    p.text(right, y_sub, p.clean(_copy("subtitle")), "subtitle", "muted")
    if model.count:
        label = p.clean(_copy("count_one" if model.count == 1 else "count_many"))
        left = card_x + sp("header_x")
        base_shift = sp("count_shift")
        p.text(left, y_sub - base_shift, label, "count_label", "muted", "l")
        p.text(left + p.width(label, "count_label") + sp("count_gap"), y_sub - base_shift, str(model.count), "count", "ink", "l")

    # Grid card.
    p.box(card_x, y_card, card_w, card_h, sp("card_radius"), "card", shadow=True)
    for i, letter in enumerate(_copy("day_letters")):
        x = inner_right - edges[i] - col_w[i]
        weekend = i >= 5
        p.text(x + col_w[i] / 2, y_dow + sp("dow_top"), p.clean(letter), "dow_weekend" if weekend else "dow",
               "muted" if weekend else "ink", "m")
        p.box(x, y_dow + dow_h - sp("rule"), col_w[i], sp("rule"), 0, "line")

    y = y_grid
    for week, plan, h in zip(model.weeks, plans, row_h):
        for i, (day, (chips, hidden, _)) in enumerate(zip(week, plan)):
            w = col_w[i]
            x = inner_right - edges[i] - w
            today = day.is_today and day.in_month
            if day.in_month:
                p.box(x, y, w, h, sp("cell_radius"), "card" if today else "cell_weekend" if day.weekend else "cell")
            if today:
                p.box(x, y, w, h, sp("cell_radius"), "accent", outline=sp("today_outline"))
                p.box(x + w - pad_x - num, y + pad_y, num, num, num // 2, "accent")
            style, color = ("number_today", "card") if today else (
                ("number_quiet", "out_of_month") if not day.in_month else
                ("number_quiet", "muted") if day.weekend else ("number", "ink"))
            p.text(x + w - pad_x - num / 2, y + pad_y + (num - p.line_h(style)) / 2, str(day.day.day), style, color, "m")
            cy = y + pad_y + num
            if today:
                cy += item_gap
                p.text(x + w - pad_x - sp("today_pad"), cy, today_label, "today", "accent_ink")
                cy += p.line_h("today")
            for e, lines, ch in chips:
                cy += item_gap
                p.box(x + pad_x, cy, w - 2 * pad_x, ch, sp("chip_radius"), "past_chip" if e.past else "accent_soft")
                tx, ty = x + w - pad_x - chip_x, cy + chip_top
                thumb = p.cover(e.cover, thumb_w, thumb_h)
                if thumb:
                    p.picture(x + w - pad_x - thumb_w, cy, thumb, sp("chip_radius"), faded=e.past)
                    ty += thumb_h
                if e.start:
                    p.text(tx, ty, _hhmm(e.start), "time", "past_ink" if e.past else "accent_ink")
                    ty += p.line_h("time") + chip_mid
                for line in lines:
                    p.text(tx, ty, line, "title_past" if e.past else "title", "past_ink" if e.past else "ink")
                    ty += p.line_h("title")
                cy += ch
            if hidden:
                p.text(x + w - pad_x - chip_x, cy + item_gap, _copy("more", n=hidden), "more", "accent_ink",
                       base_dir="L")  # otherwise "+2" turns into "2+"
        y += h + gap

    # Next up / empty state.
    if next_h:
        p.box(card_x, y_next, card_w, next_h, sp("card_radius"), "card", shadow=True)
    ry = y_next
    for n, (_, lines, meta, pic, text_h, row_height) in enumerate(rows):
        if n:
            p.box(card_x + next_pad_x, ry, card_w - 2 * next_pad_x, sp("rule"), 0, "line")
            ry += sp("rule")
        tx, ty = card_x + card_w - next_pad_x, ry + (row_height - text_h) / 2
        if pic:
            p.picture(card_x + next_pad_x, ry + (row_height - cover_h) / 2, pic, sp("cell_radius"))
        if n == 0:
            p.box(tx - tag_w, ty, tag_w, tag_h, sp("tag_radius"), "accent")
            p.text(tx - tag_w / 2, ty + sp("tag_pad_y"), tag, "tag", "card", "m")
            ty += tag_h + sp("tag_gap")
        for line in lines:
            p.text(tx, ty, line, "next", "ink")
            ty += p.line_h("next")
        if meta:
            p.text(tx, ty + sp("meta_gap"), meta, "meta", "muted")
        ry += row_height
    for n, line in enumerate(empty_lines):
        p.text(width / 2, y_next + next_pad_y + n * p.line_h("empty"), line, "empty", "muted", "m")

    # Footer: a line and, to its left, what to write.
    lead, cta = p.clean(_copy("footer")), p.clean(_copy("footer_cta"))
    cta_w = round(p.width(cta, "footer_cta")) + 2 * sp("cta_pad_x")
    lead = p.fit(lead, "footer", card_w - cta_w - sp("cta_gap"))
    lead_w = p.width(lead, "footer")
    x = width / 2 + (lead_w + sp("cta_gap") + cta_w) / 2
    p.text(x, y_footer, lead, "footer", "muted")
    x -= lead_w + sp("cta_gap")
    p.box(x - cta_w, y_footer, cta_w, p.line_h("footer"), sp("cta_radius"), "cta_soft")
    p.text(x - cta_w / 2, y_footer, cta, "footer_cta", "ink", "m")

    out = io.BytesIO()
    p.img.save(out, "PNG", optimize=True)
    return out.getvalue()


# ── From the database to a picture ───────────────────────────

async def month_png(db, month: str | None = None, media_dir: Path | None = None) -> bytes:
    """The calendar of the really published events. ValueError on a malformed month."""
    import asyncio
    import os

    now = local_now()
    year, mon = parse_month(month, now)
    events = await db.get_published_events(int(os.getenv("GROUP_ID", "0") or 0))
    return await asyncio.to_thread(render_png, build_month(year, mon, events, now), media_dir)
