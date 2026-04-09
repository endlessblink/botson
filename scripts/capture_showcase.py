#!/usr/bin/env python3
"""
capture_showcase.py — Captures Botson dashboard as a video walkthrough and converts to GIF.

Usage:
    python scripts/capture_showcase.py               # video mode (default)
    python scripts/capture_showcase.py --screenshots # screenshot mode (fallback)

Requirements:
    pip install playwright pillow
    playwright install chromium
    ffmpeg must be available in PATH (for video mode)
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = PROJECT_ROOT / "media"
SCREENSHOTS_DIR = MEDIA_DIR / "screenshots"
GIF_OUTPUT = MEDIA_DIR / "botson-showcase.gif"
WEBM_OUTPUT = MEDIA_DIR / "botson-showcase.webm"
COLLAGE_OUTPUT = MEDIA_DIR / "botson-collage.png"

TELEGRAM_SIZE_LIMIT_BYTES = 10 * 1024 * 1024  # 10 MB

# ---------------------------------------------------------------------------
# Pages to capture
# ---------------------------------------------------------------------------
PAGES = [
    {"path": "/",          "name": "home",      "label": "Home — Overview",      "nav_text": "סקירה כללית"},
    {"path": "/health",    "name": "health",    "label": "Bot Status",           "nav_text": "מצב הבוט"},
    {"path": "/prompts",   "name": "prompts",   "label": "Engagement System",    "nav_text": "הודעות ושאלות"},
    {"path": "/planner",   "name": "planner",   "label": "Weekly Calendar",      "nav_text": "תכנון שבועי"},
    {"path": "/activity",  "name": "activity",  "label": "Activity Log",         "nav_text": "לוג פעילות"},
    {"path": "/levels",    "name": "levels",    "label": "Member Levels",        "nav_text": "רמות"},
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
# Video capture (default mode)
# ---------------------------------------------------------------------------
def capture_video(base_url: str, password: str) -> tuple[Path | None, list]:
    """
    Launch Playwright with video recording, walk through each dashboard page
    via sidebar navigation clicks, then return the path to the saved .webm file.
    """
    from playwright.sync_api import sync_playwright

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                color_scheme="dark",
                record_video_dir=str(tmp_path),
                record_video_size={"width": 1280, "height": 720},
            )
            page = context.new_page()

            # Inject dark-mode CSS so the page renders correctly even if
            # prefers-color-scheme isn't detected by media query alone.
            page.add_init_script(
                "Object.defineProperty(window, 'matchMedia', {value: (q) => ({matches: q.includes('dark'), media: q, onchange: null, addListener: ()=>{}, removeListener: ()=>{}, addEventListener: ()=>{}, removeEventListener: ()=>{}, dispatchEvent: ()=>false})});"
            )

            import time as _time
            # t0 = video recording start (context.new_page triggers recording)
            t0 = _time.monotonic()

            if not do_login(page, base_url, password):
                print("Login failed. Aborting video capture.")
                context.close()
                browser.close()
                return None, []

            # After login we're already on the home page — wait for animations.
            page.wait_for_load_state("networkidle", timeout=15_000)
            page.wait_for_timeout(2000)

            # Track precise start/end times for each page (relative to t0).
            # "start" = page fully loaded, "end" = just before clicking away.
            # Login page and transitions between pages are NOT tracked → no blur.
            timestamps = []
            home_start = _time.monotonic() - t0
            print(f"  Already on: {PAGES[0]['label']} (after login)")

            # Walk through remaining pages by clicking the sidebar nav link.
            for page_def in PAGES[1:]:
                nav_text = page_def["nav_text"]
                label = page_def["label"]
                print(f"  Navigating to: {label} (clicking '{nav_text}') ...")

                # Mark end of previous page BEFORE clicking
                prev_end = _time.monotonic() - t0
                if timestamps:
                    timestamps[-1]["end"] = prev_end
                else:
                    # First entry: home page
                    timestamps.append({"name": PAGES[0]["name"],
                                       "start": home_start, "end": prev_end})

                try:
                    sidebar = page.locator("aside.lg\\:flex")
                    sidebar.locator(f"text={nav_text}").click(timeout=8_000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    page.wait_for_timeout(2000)
                    timestamps.append({"name": page_def["name"],
                                       "start": _time.monotonic() - t0,
                                       "end": 0})  # filled on next iteration or at end
                    print(f"    OK — {page.url}")
                except Exception as exc:
                    print(f"    WARNING: Could not click nav link for '{label}': {exc}")
                    try:
                        fallback_url = f"{base_url}{page_def['path']}"
                        print(f"    Falling back to goto: {fallback_url}")
                        page.goto(fallback_url, wait_until="networkidle", timeout=15_000)
                        page.wait_for_timeout(2000)
                        timestamps.append({"name": page_def["name"],
                                           "start": _time.monotonic() - t0,
                                           "end": 0})
                    except Exception as exc2:
                        print(f"    ERROR on fallback: {exc2} — skipping page.")

            # Mark the end of the last page.
            if timestamps:
                timestamps[-1]["end"] = _time.monotonic() - t0

            # Extra second after the last page before closing.
            page.wait_for_timeout(1000)

            # Closing the context triggers Playwright to flush and save the video file.
            context.close()
            browser.close()

        # Find the recorded .webm file (Playwright names it with a UUID).
        webm_files = list(tmp_path.glob("*.webm"))
        if not webm_files:
            print("ERROR: No .webm file found after recording.")
            return None, []

        # Move it to the final output path.
        src = webm_files[0]
        WEBM_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.move(str(src), str(WEBM_OUTPUT))
        size_mb = WEBM_OUTPUT.stat().st_size / (1024 * 1024)
        print(f"\n  Video saved: {WEBM_OUTPUT}  ({size_mb:.2f} MB)")
        return WEBM_OUTPUT, timestamps


# ---------------------------------------------------------------------------
# FFmpeg: webm → gif
# ---------------------------------------------------------------------------
def convert_webm_to_gif(webm_path: Path, gif_path: Path,
                        timestamps=None, do_blur: bool = True) -> bool:
    """
    Convert a .webm file to an animated GIF.
    1. Extract frames via ffmpeg
    2. Apply Pillow-based privacy blur per page (reusing BLUR_REGIONS)
    3. Assemble palette-optimised GIF via ffmpeg
    Returns True on success.
    """
    from PIL import Image

    print(f"\n  Converting {webm_path.name} → {gif_path.name} ...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        frames_dir = Path(tmp_dir) / "frames"
        blurred_dir = Path(tmp_dir) / "blurred"
        frames_dir.mkdir()
        blurred_dir.mkdir()

        # Step 1: Extract frames at 12 fps, scaled to 960px wide
        print("  Extracting frames ...")
        extract_cmd = [
            "ffmpeg", "-y", "-i", str(webm_path),
            "-vf", "fps=12,scale=960:-1:flags=lanczos",
            str(frames_dir / "frame_%05d.png"),
        ]
        result = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"  ERROR extracting frames: {result.stderr[-500:]}")
            return False

        frame_files = sorted(frames_dir.glob("frame_*.png"))
        print(f"  Extracted {len(frame_files)} frames")

        if not frame_files:
            return False

        # Step 2: Apply blur to frames belonging to pages with BLUR_REGIONS
        fps = 12
        scale = 960 / 1280  # coords were defined for 1280px viewport

        # Determine which page a frame belongs to using precise start/end windows.
        # Frames during login or page transitions (between end→start) get NO blur.
        def get_page_for_frame(frame_idx):
            if not timestamps:
                return None
            t = frame_idx / fps
            for ts in timestamps:
                if ts["start"] <= t <= ts["end"]:
                    return ts["name"]
            return None  # login screen or transition → no blur

        # Debug: print timestamp windows
        if timestamps:
            print(f"  Timestamp windows (video has {len(frame_files)} frames = {len(frame_files)/fps:.1f}s):")
            for ts in timestamps:
                needs_blur = ts["name"] in BLUR_REGIONS
                print(f"    {ts['name']:12s}  {ts['start']:5.1f}s – {ts['end']:5.1f}s  {'BLUR' if needs_blur else ''}")

        blur_count = 0
        for idx, frame_path in enumerate(frame_files):
            page_name = get_page_for_frame(idx)
            out_path = blurred_dir / frame_path.name

            if do_blur and page_name and page_name in BLUR_REGIONS:
                img = Image.open(str(frame_path))
                # Scale blur regions to match the 960px output
                scaled_regions = [
                    (int(x0 * scale), int(y0 * scale),
                     int(x1 * scale), int(y1 * scale))
                    for (x0, y0, x1, y1) in BLUR_REGIONS[page_name]
                ]
                img = apply_blur(img, scaled_regions)
                img.save(str(out_path))
                blur_count += 1
            else:
                # Just copy the frame as-is
                import shutil
                shutil.copy2(str(frame_path), str(out_path))

        if blur_count:
            print(f"  Applied blur to {blur_count} frames")

        # Step 3: Reassemble into palette-optimised GIF
        print("  Assembling GIF ...")
        vf = (
            "split[s0][s1];"
            "[s0]palettegen=max_colors=128:stats_mode=diff[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=3"
        )
        assemble_cmd = [
            "ffmpeg", "-y",
            "-framerate", "12",
            "-i", str(blurred_dir / "frame_%05d.png"),
            "-filter_complex", vf,
            "-loop", "0",
            str(gif_path),
        ]
        result = subprocess.run(assemble_cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  ERROR assembling GIF: {result.stderr[-500:]}")
            return False

    size_bytes = gif_path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    print(f"\n  GIF saved: {gif_path}")
    print(f"  File size: {size_mb:.2f} MB ({size_bytes:,} bytes)")

    if size_bytes > TELEGRAM_SIZE_LIMIT_BYTES:
        print(
            f"  WARNING: GIF exceeds Telegram's 10 MB limit "
            f"({size_mb:.2f} MB). Consider reducing scale or fps."
        )
    else:
        print("  Size is within Telegram's 10 MB limit.")

    return True


# ---------------------------------------------------------------------------
# Screenshot capture (fallback / --screenshots mode)
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
# GIF assembly (screenshot mode)
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
        description="Capture Botson dashboard as a video walkthrough and convert to GIF.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Modes:\n"
            "  (default)      Video mode — records a live browser walkthrough via Playwright,\n"
            "                 then converts to GIF with ffmpeg.\n"
            "  --screenshots  Screenshot mode — takes static screenshots per page and\n"
            "                 assembles them into an animated GIF using Pillow.\n"
        ),
    )
    parser.add_argument(
        "--screenshots",
        action="store_true",
        help="Use screenshot mode instead of video recording (fallback).",
    )
    parser.add_argument(
        "--no-blur",
        action="store_true",
        help="Skip privacy blurring of member names/descriptions (screenshot mode only).",
    )
    parser.add_argument(
        "--collage",
        action="store_true",
        help="Also generate a 2×3 grid collage PNG (screenshot mode only).",
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

    mode = "screenshots" if args.screenshots else "video"

    print("=== Botson Dashboard Showcase Capture ===")
    print(f"Mode     : {mode}")
    print(f"Base URL : {base_url}")
    if args.screenshots:
        print(f"Blur     : {'disabled' if args.no_blur else 'enabled'}")
        print(f"Collage  : {'yes' if args.collage else 'no'}")
    print()

    # -------------------------------------------------------------------------
    # VIDEO MODE (default)
    # -------------------------------------------------------------------------
    if not args.screenshots:
        print("[1/2] Recording video walkthrough ...")
        webm_path, timestamps = capture_video(base_url, password)
        if webm_path is None:
            print("Video capture failed. Exiting.")
            sys.exit(1)

        print("\n[2/2] Converting to GIF ...")
        success = convert_webm_to_gif(webm_path, GIF_OUTPUT, timestamps, not args.no_blur)
        if not success:
            print("FFmpeg conversion failed. The raw .webm is still available at:")
            print(f"  {webm_path}")
            sys.exit(1)

        print("\nDone.")
        return

    # -------------------------------------------------------------------------
    # SCREENSHOT MODE (--screenshots flag)
    # -------------------------------------------------------------------------
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
