"""Draws the share picture of the public community page (/welcome): the picture a chat app shows when
the page's link is shared. It matches the page's transit look: a dark station sign with the two line
badges and the red and teal stripes, beside a line with stops ending in a dashed "your place" stop.
No words (the page title travels with the link). No AI, no network.
Output: dashboard/static/community/share.jpg (1200x630). Run once after a change:
    .venv/bin/python scripts/make_community_art.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dashboard", "static", "community", "share.jpg")
HEEBO = os.path.join(ROOT, "assets", "fonts", "heebo", "Heebo-VariableFont_wght.ttf")
S = 2  # draw at twice the size, then scale down for smooth edges
BG, SIGN = (246, 246, 243), (30, 34, 38)
RED, TEAL, WHITE = (184, 71, 60), (47, 111, 108), (255, 255, 255)


def badge(d, cx, cy, r, colour, digit, font):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)
    d.text((cx, cy), digit, font=font, fill=WHITE, anchor="mm")


if __name__ == "__main__":
    W, H = 1200 * S, 630 * S
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(HEEBO, 40 * S)
    try:
        font.set_variation_by_axes([900])
    except Exception:
        pass

    # the station sign (right side: the page reads right to left)
    x0, y0, x1, y1 = 520 * S, 90 * S, 1120 * S, 540 * S
    d.rounded_rectangle([x0, y0, x1, y1], radius=36 * S, fill=SIGN)
    badge(d, x1 - 80 * S, y0 + 80 * S, 38 * S, RED, "1", font)
    badge(d, x1 - 170 * S, y0 + 80 * S, 38 * S, TEAL, "2", font)
    for i, w in enumerate((420, 300)):  # two quiet bars where the name sits on the page
        d.rounded_rectangle([x1 - 70 * S - w * S, y0 + (170 + i * 90) * S, x1 - 70 * S, y0 + (230 + i * 90) * S],
                            radius=14 * S, fill=(58, 64, 70))
    d.rectangle([x0, y1 - 70 * S, x1, y1 - 40 * S], fill=RED)
    d.rectangle([x0, y1 - 40 * S, x1, y1 - 24 * S], fill=TEAL)

    # the line with its stops (left side)
    lx, top, bottom = 300 * S, 110 * S, 520 * S
    d.rounded_rectangle([lx - 9 * S, top, lx + 9 * S, bottom], radius=9 * S, fill=RED)
    stops = [top + 20 * S, top + 150 * S, top + 280 * S]
    for y in stops:
        d.ellipse([lx - 30 * S, y - 30 * S, lx + 30 * S, y + 30 * S], fill=BG, outline=RED, width=13 * S)
        d.rounded_rectangle([lx - 230 * S, y - 14 * S, lx - 70 * S, y + 14 * S], radius=10 * S, fill=(214, 216, 210))
    yend = bottom - 10 * S
    d.ellipse([lx - 34 * S, yend - 34 * S, lx + 34 * S, yend + 34 * S], fill=BG)
    for k in range(12):  # the dashed last stop: "your place"
        a0 = k * 30
        d.arc([lx - 34 * S, yend - 34 * S, lx + 34 * S, yend + 34 * S], a0, a0 + 18, fill=RED, width=13 * S)

    im.resize((1200, 630), Image.LANCZOS).save(OUT, quality=86, optimize=True, progressive=True)
    print("made", OUT)
