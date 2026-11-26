"""Makes the two-ink printed pictures of the public community page (/welcome). No AI, no network.

Each scene is drawn soft in grey, then screened into round printing dots. Output in
dashboard/static/community/: <scene>-a.png (first ink) and <scene>-b.png (second ink), dots as
alpha only, so the page colours them with CSS masks (light and dark from one file), plus share.jpg,
the picture shown when the page's link is shared. Run once after changing a scene:
    .venv/bin/python scripts/make_community_art.py
"""
import math, os
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "dashboard", "static", "community")
S = 1000  # scene size


def canvas():
    return Image.new("L", (S, S), 255)


def die(size, body, pip, pips):
    col = Image.new("L", (size, size), 255)
    d = ImageDraw.Draw(col)
    d.rounded_rectangle([0, 0, size - 1, size - 1], size // 6, fill=body)
    r = size // 11
    for (px, py) in pips:
        cx, cy = px * size, py * size
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=pip)
    al = Image.new("L", (size, size), 0)
    ImageDraw.Draw(al).rounded_rectangle([0, 0, size - 1, size - 1], size // 6, fill=255)
    return col, al


def place(base, col, alpha, xy, angle):
    col = col.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    alpha = alpha.rotate(angle, resample=Image.BICUBIC, expand=True)
    out = base.copy()
    out.paste(col, xy, alpha)
    return out


def shadow(base, alpha, xy, angle, strength=110, blur=28, off=(26, 30)):
    a = alpha.rotate(angle, resample=Image.BICUBIC, expand=True)
    m = Image.new("L", (S, S), 0)
    m.paste(a, (xy[0] + off[0], xy[1] + off[1]))
    m = m.filter(ImageFilter.GaussianBlur(blur))
    out = base.copy()
    out.paste(Image.new("L", (S, S), 255 - strength), (0, 0), m)
    return out


# ---------- scenes: return (a, b) grey images, 0 = full ink ----------

def scene_dice():
    a, b = canvas(), canvas()
    ImageDraw.Draw(b).ellipse([120, 140, 900, 920], fill=150)
    P5 = [(.27, .27), (.73, .73), (.5, .5), (.73, .27), (.27, .73)]
    P3 = [(.27, .27), (.5, .5), (.73, .73)]
    c1, al1 = die(330, 70, 250, P3)
    c2, al2 = die(270, 225, 30, P5)
    a = shadow(a, al1, (170, 250), -16)
    a = shadow(a, al2, (520, 470), 14)
    a = place(a, c1, al1, (170, 250), -16)
    a = place(a, c2, al2, (520, 470), 14)
    return a, b


def scene_cup():
    a, b = canvas(), canvas()
    d = ImageDraw.Draw(a)
    d.ellipse([170, 690, 830, 850], fill=150)
    d.ellipse([230, 700, 770, 800], fill=110)
    d.rounded_rectangle([300, 420, 640, 760], 60, fill=55)
    d.ellipse([300, 380, 640, 470], fill=90)
    d.ellipse([325, 395, 615, 455], fill=25)
    d.ellipse([600, 480, 760, 660], outline=55, width=42)
    for i, x in enumerate((390, 470, 550)):
        pts = [(x + 26 * math.sin(t / 9 + i), 340 - t * 2.1) for t in range(0, 110)]
        d.line(pts, fill=150, width=22, joint="curve")
    a = a.filter(ImageFilter.GaussianBlur(3))
    db = ImageDraw.Draw(b)
    db.rectangle([0, 560, S, S], fill=140)
    db.ellipse([560, 90, 900, 430], fill=120)
    return a, b


def scene_question():
    a, b = canvas(), canvas()
    f = ImageFont.truetype(os.path.join(ROOT, "assets", "fonts", "rubik", "Rubik-Black.ttf"), 900)
    ImageDraw.Draw(b).text((560, 560), "?", font=f, fill=130, anchor="mm")
    ImageDraw.Draw(a).text((500, 520), "?", font=f, fill=20, anchor="mm")
    return a, b


def scene_picnic():
    a, b = canvas(), canvas()
    quad = [(90, 900), (930, 860), (760, 420), (240, 450)]
    n = 7

    def lerp(p, q, t):
        return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)

    def pt(u, v):
        return lerp(lerp(quad[0], quad[1], u), lerp(quad[3], quad[2], u), v)

    d = ImageDraw.Draw(a)
    for i in range(n):
        for j in range(n):
            u0, u1, v0, v1 = i / n, (i + 1) / n, j / n, (j + 1) / n
            d.polygon([pt(u0, v0), pt(u1, v0), pt(u1, v1), pt(u0, v1)],
                      fill=60 if (i + j) % 2 == 0 else 205)
    d.ellipse([430, 470, 640, 600], fill=30)
    d.ellipse([455, 480, 615, 560], fill=120)
    db = ImageDraw.Draw(b)
    db.rectangle([0, 520, S, S], fill=170)
    db.ellipse([600, 60, 940, 400], fill=60)
    return a.filter(ImageFilter.GaussianBlur(2)), b


def scene_table():
    a, b = canvas(), canvas()
    d = ImageDraw.Draw(a)
    cx, cy, R = 500, 500, 250
    for k in range(7):
        ang = -math.pi / 2 + k * 2 * math.pi / 7
        x, y = cx + 370 * math.cos(ang), cy + 370 * math.sin(ang)
        if k == 5:
            d.ellipse([x - 78, y - 78, x + 78, y + 78], outline=30, width=16)
        else:
            d.ellipse([x - 78, y - 78, x + 78, y + 78], fill=45 if k % 2 else 95)
    a = a.filter(ImageFilter.GaussianBlur(2))
    sh = Image.new("L", (S, S), 0)
    ImageDraw.Draw(sh).ellipse([cx - R + 30, cy - R + 40, cx + R + 30, cy + R + 40], fill=255)
    sh = sh.filter(ImageFilter.GaussianBlur(30))
    a.paste(Image.new("L", (S, S), 150), (0, 0), sh)
    ImageDraw.Draw(a).ellipse([cx - R, cy - R, cx + R, cy + R], fill=235, outline=40, width=10)
    c1, al1 = die(90, 60, 240, [(.27, .27), (.5, .5), (.73, .73)])
    a = place(a, c1, al1, (420, 420), -14)
    d = ImageDraw.Draw(a)
    for (x, y) in ((585, 440), (430, 600), (560, 610)):
        d.ellipse([x - 34, y - 34, x + 34, y + 34], outline=50, width=12)
    ImageDraw.Draw(b).ellipse([cx - R - 160, cy - R - 160, cx + R + 160, cy + R + 160], fill=175)
    return a, b


def scene_hills():
    a, b = canvas(), canvas()
    d = ImageDraw.Draw(a)
    d.polygon([(0, 1000), (0, 640), (260, 430), (470, 610), (700, 360), (1000, 640), (1000, 1000)], fill=70)
    d.polygon([(0, 1000), (0, 820), (380, 700), (720, 800), (1000, 720), (1000, 1000)], fill=25)
    a = a.filter(ImageFilter.GaussianBlur(4))
    db = ImageDraw.Draw(b)
    db.rectangle([0, 0, S, 600], fill=200)
    db.ellipse([140, 120, 420, 400], fill=40)
    return a, b


# ---------- halftone ----------

def halftone(grey, cell=13, angle=15, gain=0.72):
    """Round dots on a rotated screen; returns RGBA black dots on transparent."""
    up = 2
    W = S * up
    out = Image.new("L", (W, W), 0)
    d = ImageDraw.Draw(out)
    th = math.radians(angle)
    ca, sa = math.cos(th), math.sin(th)
    c = cell * up
    span = int(W * 1.5 / c) + 2
    for i in range(-span, span):
        for j in range(-span, span):
            x = (i * ca - j * sa) * c + W / 2
            y = (i * sa + j * ca) * c + W / 2
            if not (-c <= x < W + c and -c <= y < W + c):
                continue
            sx, sy = min(S - 1, max(0, int(x / up))), min(S - 1, max(0, int(y / up)))
            dark = 1 - grey.getpixel((sx, sy)) / 255
            if dark < 0.04:
                continue
            r = c * gain * math.sqrt(dark)
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    out = out.resize((S, S), Image.LANCZOS)
    rgba = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    rgba.putalpha(out)
    return rgba



def save_mask(rgba, size, path):
    """Alpha-only PNG (grey + alpha), small and recolourable."""
    a = rgba.getchannel("A").resize((size, size), Image.LANCZOS)
    a = a.point(lambda v: 0 if v < 24 else (255 if v > 232 else v))
    out = Image.new("LA", (size, size), 0)
    out.putalpha(a)
    out.save(path, optimize=True)


def share_picture(path):
    """1200x630, the round table printed in green over orange on paper; no words (the page title says them)."""
    a, b = scene_table()
    W, H = 1200, 630
    paper = Image.new("RGB", (W, H), (243, 239, 230))
    for grey, cell, ang, rgb, off in ((b, 17, 75, (242, 106, 60), (14, -10)), (a, 13, 15, (47, 143, 78), (0, 0))):
        dots = halftone(grey, cell=cell, angle=ang).getchannel("A").resize((600, 600), Image.LANCZOS)
        ink = Image.new("RGB", (W, H), rgb)
        mask = Image.new("L", (W, H), 0)
        mask.paste(dots, (300 + off[0], 15 + off[1]))
        inked = Image.composite(ink, Image.new("RGB", (W, H), (255, 255, 255)), mask)
        paper = ImageChops.multiply(paper, inked)
    paper.save(path, quality=82, optimize=True, progressive=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    sizes = {"table": 640, "question": 320, "dice": 320, "cup": 320, "hills": 320}
    scenes = {"table": scene_table, "question": scene_question, "dice": scene_dice, "cup": scene_cup, "hills": scene_hills}
    for name, fn in scenes.items():
        a, b = fn()
        save_mask(halftone(a, cell=13, angle=15), sizes[name], os.path.join(OUT, f"{name}-a.png"))
        save_mask(halftone(b, cell=17, angle=75, gain=0.7), sizes[name] * 3 // 4, os.path.join(OUT, f"{name}-b.png"))
        print("made", name)
    share_picture(os.path.join(OUT, "share.jpg"))
    print("made share")
