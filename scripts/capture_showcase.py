#!/usr/bin/env python3
"""
capture_showcase.py — Captures Botson dashboard screenshots and creates an animated GIF.

Usage:
    python scripts/capture_showcase.py [--no-blur] [--collage]

Requirements:
    pip install playwright pillow
    playwright install chromium
"""

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = PROJECT_ROOT / "media" / "screenshots"
GIF_OUTPUT = PROJECT_ROOT / "media" / "botson-showcase.gif"
COLLAGE_OUTPUT = PROJECT_ROOT / "media" / "botson-collage.png"

TELEGRAM_SIZE_LIMIT_BYTES = 10 * 1024 * 1024  # 10 MB

# ---------------------------------------------------------------------------
# Pages to capture
# ---------------------------------------------------------------------------
PAGES = [
    {"path": "/",          "name": "home",      "label": "Home — Overview"},
    {"path": "/health",    "name": "health",    "label": "Bot Status"},
    {"path": "/prompts",   "name": "prompts",   "label": "Engagement System"},
    {"path": "/planner",   "name": "planner",   "label": "Weekly Calendar"},
    {"path": "/activity",  "name": "activity",  "label": "Activity Log"},
    {"path": "/levels",    "name": "levels",    "label": "Member Levels"},
]

# ---------------------------------------------------------------------------
# Privacy blur regions (x0, y0, x1, y1) — applied per page name
# These cover name/description columns in tables to obscure member data.
# ---------------------------------------------------------------------------
BLUR_REGIONS = {
    # RTL layout: names are on the RIGHT side of tables
    "home": [
        (300, 140, 1050, 260),   # stat cards row — member names in "highest level" and "longest streak"
        (530, 330, 1050, 570),   # leaderboard table — name column
    ],
    "activity": [(100, 250, 900, 700)],    # description column (wide, center)
    "levels": [(530, 280, 1050, 720)],     # member name column — extended to bottom
}

BLUR_RADIUS = 8


# ---------------------------------------------------------------------------
# Login helper
# ---------------------------------------------------------------------------
def do_login(page, base_url: str, password: str) -> bool:
    """Navigate to /login, submit the password form, return True on success."""
    try:
        login_url = f"{base_url}/login"
        print(f"  Navigating to {login_url} ...")
        page.goto(login_url, wait_until="networkidle", timeout=15_000)

        # Fill password field (name="password" or type="password")
        page.fill('input[name="password"], input[type="password"]', password)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_load_state("networkidle", timeout=10_000)

        # Check we're no longer on the login page
        if "/login" in page.url:
            print("  ERROR: Still on login page after submit — wrong password?")
            return False

        print(f"  Logged in. Current URL: {page.url}")
        return True
    except Exception as exc:
        print(f"  ERROR during login: {exc}")
        return False


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------
def capture_pages(base_url: str, password: str) -> list[tuple[str, Path]]:
    """Launch Playwright, log in, capture each page. Returns list of (name, path)."""
    from playwright.sync_api import sync_playwright

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    captured = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            color_scheme="dark",
        )
        page = context.new_page()

        # Inject dark-mode CSS so the page renders correctly even if prefers-color-scheme
        # isn't detected by media query alone.
        page.add_init_script(
            "Object.defineProperty(window, 'matchMedia', {value: (q) => ({matches: q.includes('dark'), media: q, onchange: null, addListener: ()=>{}, removeListener: ()=>{}, addEventListener: ()=>{}, removeEventListener: ()=>{}, dispatchEvent: ()=>false})});"
        )

        if not do_login(page, base_url, password):
            print("Login failed. Aborting capture.")
            browser.close()
            return captured

        for page_def in PAGES:
            url = f"{base_url}{page_def['path']}"
            name = page_def["name"]
            label = page_def["label"]
            out_path = SCREENSHOTS_DIR / f"{name}.png"

            print(f"  Capturing: {label} ({url}) ...")
            try:
                page.goto(url, wait_until="networkidle", timeout=15_000)
                # Extra wait for any JS-rendered content
                page.wait_for_timeout(800)
                page.screenshot(path=str(out_path), full_page=False)
                print(f"    Saved: {out_path}")
                captured.append((name, out_path))
            except Exception as exc:
                print(f"    ERROR capturing {label}: {exc} — skipping.")

        browser.close()

    return captured


# ---------------------------------------------------------------------------
# Privacy blurring
# ---------------------------------------------------------------------------
def apply_blur(img, regions: list[tuple[int, int, int, int]]):
    """Apply gaussian blur to specified rectangular regions of a PIL image."""
    from PIL import ImageFilter

    for x0, y0, x1, y1 in regions:
        # Clamp to image bounds
        iw, ih = img.size
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(iw, x1), min(ih, y1)
        if x0 >= x1 or y0 >= y1:
            continue
        region = img.crop((x0, y0, x1, y1))
        blurred = region.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
        img.paste(blurred, (x0, y0))
    return img


# ---------------------------------------------------------------------------
# GIF assembly
# ---------------------------------------------------------------------------
def build_gif(frames: list, output_path: Path, frame_duration_ms: int = 2500):
    """Build an animated GIF from a list of PIL images.

    Each page is held for ~2000 ms, then a crossfade transition of ~8 frames
    at ~60 ms each leads into the next page (~500 ms total per transition).
    """
    from PIL import Image

    if not frames:
        print("No frames to assemble — skipping GIF.")
        return

    STILL_DURATION_MS = 2000
    TRANSITION_FRAMES = 8
    TRANSITION_FRAME_MS = 60  # 8 × 60 ms ≈ 500 ms per transition

    # Build the interleaved sequence of (RGB image, duration_ms) pairs.
    sequence: list[tuple] = []  # (PIL RGB image, int duration_ms)

    rgb_frames = [f.convert("RGB") for f in frames]

    for i, img in enumerate(rgb_frames):
        # Add the still frame for this page.
        sequence.append((img, STILL_DURATION_MS))

        # Add transition frames between this page and the next (not after the last).
        if i < len(rgb_frames) - 1:
            next_img = rgb_frames[i + 1]
            for t in range(1, TRANSITION_FRAMES + 1):
                alpha = t / (TRANSITION_FRAMES + 1)  # 0 < alpha < 1
                blended = Image.blend(img, next_img, alpha=alpha)
                sequence.append((blended, TRANSITION_FRAME_MS))

    print(f"  Total GIF frames: {len(sequence)} "
          f"({len(rgb_frames)} still + "
          f"{len(sequence) - len(rgb_frames)} transition)")

    # Quantize each frame to palette mode for GIF compatibility.
    palette_frames = []
    durations = []
    for rgb_img, duration in sequence:
        quantized = rgb_img.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        palette_frames.append(quantized)
        durations.append(duration)

    first = palette_frames[0]
    rest = palette_frames[1:]

    first.save(
        str(output_path),
        format="GIF",
        save_all=True,
        append_images=rest,
        duration=durations,
        loop=0,
        optimize=True,
    )

    size_bytes = output_path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    print(f"\n  GIF saved: {output_path}")
    print(f"  File size: {size_mb:.2f} MB ({size_bytes:,} bytes)")

    if size_bytes > TELEGRAM_SIZE_LIMIT_BYTES:
        print(
            f"  WARNING: GIF exceeds Telegram's 10 MB limit "
            f"({size_mb:.2f} MB). Consider reducing viewport or frame count."
        )
    else:
        print("  Size is within Telegram's 10 MB limit.")


# ---------------------------------------------------------------------------
# Collage builder (2x3 grid)
# ---------------------------------------------------------------------------
def build_collage(frames, output_path: Path):
    """Build a 2×3 grid collage from PIL images."""
    from PIL import Image, ImageDraw, ImageFont

    if not frames:
        print("No frames for collage — skipping.")
        return

    cols, rows = 2, 3
    thumb_w, thumb_h = 640, 360  # half of 1280x720
    padding = 4
    label_h = 20

    canvas_w = cols * thumb_w + (cols + 1) * padding
    canvas_h = rows * (thumb_h + label_h) + (rows + 1) * padding

    canvas = Image.new("RGB", (canvas_w, canvas_h), color=(10, 10, 10))
    draw = ImageDraw.Draw(canvas)

    # Try to load a font; fall back to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    for idx, (name, img) in enumerate(frames):
        col = idx % cols
        row = idx // cols
        x = padding + col * (thumb_w + padding)
        y = padding + row * (thumb_h + label_h + padding)

        thumb = img.convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        canvas.paste(thumb, (x, y))

        # Label
        label = next((p["label"] for p in PAGES if p["name"] == name), name)
        draw.text((x + 4, y + thumb_h + 2), label, fill=(180, 180, 180), font=font)

    canvas.save(str(output_path), format="PNG", optimize=True)
    size_bytes = output_path.stat().st_size
    print(f"\n  Collage saved: {output_path}  ({size_bytes / 1024:.0f} KB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Capture Botson dashboard screenshots and build an animated GIF showcase."
    )
    parser.add_argument(
        "--no-blur",
        action="store_true",
        help="Skip privacy blurring of member names/descriptions.",
    )
    parser.add_argument(
        "--collage",
        action="store_true",
        help="Also generate a 2×3 grid collage PNG.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8080",
        help="Base URL of the dashboard (default: http://localhost:8080).",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Dashboard password (overrides DASHBOARD_PASSWORD env var).",
    )
    args = parser.parse_args()

    password = (
        args.password
        or os.environ.get("DASHBOARD_PASSWORD")
        or "botson-admin"
    )
    base_url = args.base_url.rstrip("/")

    print("=== Botson Dashboard Showcase Capture ===")
    print(f"Base URL : {base_url}")
    print(f"Blur     : {'disabled' if args.no_blur else 'enabled'}")
    print(f"Collage  : {'yes' if args.collage else 'no'}")
    print()

    # --- Step 1: Capture screenshots ---
    print("[1/3] Capturing screenshots ...")
    captured = capture_pages(base_url, password)

    if not captured:
        print("No screenshots captured. Exiting.")
        sys.exit(1)

    print(f"\n  {len(captured)}/{len(PAGES)} pages captured.")

    # --- Step 2: Load + optionally blur ---
    print("\n[2/3] Processing images ...")
    try:
        from PIL import Image
    except ImportError:
        print("ERROR: Pillow is not installed. Run: pip install pillow")
        sys.exit(1)

    frames_for_gif = []   # list of PIL images
    frames_for_collage = []  # list of (name, PIL image)

    for name, path in captured:
        try:
            img = Image.open(str(path))
            img.load()  # force decode

            if not args.no_blur and name in BLUR_REGIONS:
                print(f"  Blurring privacy regions on: {name}")
                img = apply_blur(img, BLUR_REGIONS[name])
                # Overwrite the screenshot with the blurred version
                img.save(str(path))

            frames_for_gif.append(img.copy())
            frames_for_collage.append((name, img.copy()))
        except Exception as exc:
            print(f"  ERROR loading {path}: {exc} — skipping.")

    # --- Step 3: Build outputs ---
    print("\n[3/3] Assembling outputs ...")

    build_gif(frames_for_gif, GIF_OUTPUT)

    if args.collage:
        build_collage(frames_for_collage, COLLAGE_OUTPUT)

    print("\nDone.")


if __name__ == "__main__":
    main()
