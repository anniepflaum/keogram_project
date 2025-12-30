#!/usr/bin/env python3
"""
Stack keogram images vertically by month, streaming them directly from the
AMISR archive (no local source folder required). Each image is resized to a
given aspect ratio (default 10:1), then concatenated into a tall image for
the chosen month.
"""

import argparse
import calendar
import io
import re
import sys
from pathlib import Path
import shutil, subprocess

import requests
from PIL import Image, ImageDraw

BASE = "https://optics.gi.alaska.edu/amisr_archive/Processed_data/aurorax/stream2"
DEFAULT_STATION = "pfrr_amisr01"
DEFAULT_CAMERA = "asi3"
UA = {"User-Agent": "keogram-stacker/1.0 (cross-platform)"}
REQ_RETRIES = 3
CURL_CONNECT_TIMEOUT = "30"
CURL_MAX_TIME = "120"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def month_days(year: int, month: int):
    return calendar.monthrange(year, month)[1]


def _curl_fetch(url: str, as_text: bool):
    if not shutil.which("curl"):
        raise RuntimeError("curl not found")
    args = [
        "curl", "-sL",
        "--connect-timeout", CURL_CONNECT_TIMEOUT,
        "--max-time", CURL_MAX_TIME,
        "-A", BROWSER_UA,
        url,
    ]
    res = subprocess.run(args, check=False, capture_output=True, text=as_text)
    if res.returncode != 0:
        raise RuntimeError(f"curl exit {res.returncode} stderr: {res.stderr.strip() if res.stderr else ''}")
    return res.stdout if as_text else res.stdout.encode() if isinstance(res.stdout, str) else res.stdout


def http_get_text(url: str, timeout: int = 30):
    errors = []
    if shutil.which("curl"):
        try:
            return _curl_fetch(url, as_text=True)
        except Exception as e:
            errors.append(str(e))
    for _ in range(REQ_RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r.text
            errors.append(f"requests HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"requests error {e}")
    raise RuntimeError(f"Failed to GET HTML: {url} ({'; '.join(errors)})")


def http_get_bytes(url: str, timeout: int = 120):
    errors = []
    if shutil.which("curl"):
        try:
            return _curl_fetch(url, as_text=False)
        except Exception as e:
            errors.append(str(e))
    for _ in range(REQ_RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r.content
        except Exception as e:
            errors.append(f"requests error {e}")
    raise RuntimeError(f"Failed to GET bytes: {url} ({'; '.join(errors)})")


def keogram_url(year: int, month: int, day: int, station: str, camera: str) -> tuple[str, str]:
    y = f"{year:04d}"
    m = f"{month:02d}"
    d = f"{day:02d}"
    ymd = f"{y}{m}{d}"
    site = station.split("_", 1)[0]
    fname = f"{ymd}__{site}_{camera}_full-keo-rgb.png"
    url = f"{BASE}/{y}/{m}/{d}/{station}/{fname}"
    return url, fname


def list_day_files(year: int, month: int, day: int, station: str) -> list[str]:
    """Return href targets in the day directory."""
    y = f"{year:04d}"
    m = f"{month:02d}"
    d = f"{day:02d}"
    day_url = f"{BASE}/{y}/{m}/{d}/{station}/"
    try:
        html = http_get_text(day_url)
    except Exception:
        return []
    hrefs = re.findall(r'href="([^"#?]+)"', html, flags=re.IGNORECASE)
    return hrefs


def fetch_image(url: str) -> Image.Image | None:
    try:
        data = http_get_bytes(url, timeout=120)
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        raise RuntimeError(f"fetch failed ({e})")


def pick_day_image(year: int, month: int, day: int, station: str, camera: str):
    """Try expected name first; if missing, scrape listing to find a close match."""
    expected_url, expected_fname = keogram_url(year, month, day, station, camera)
    try:
        img = fetch_image(expected_url)
        if img:
            return img, expected_fname
    except Exception:
        pass

    # Fallback: scrape listing to find any full-keo-rgb PNG for this date/camera
    ymd = f"{year:04d}{month:02d}{day:02d}"
    hrefs = list_day_files(year, month, day, station)
    if not hrefs:
        return None, expected_fname
    pat = re.compile(
        rf"{ymd}__.*{re.escape(camera)}.*full[-_]?keo[-_]?rgb\.png",
        re.IGNORECASE,
    )
    for name in hrefs:
        if pat.search(name):
            url = f"{BASE}/{year:04d}/{month:02d}/{day:02d}/{station}/{name}"
            img2 = fetch_image(url)
            if img2:
                return img2, name
    return None, expected_fname


def stack_keograms_for_month(ym: str,
                             station: str,
                             camera: str,
                             output_dir: Path,
                             aspect: float,
                             draw_hours: tuple[int, ...],
                             skip_existing: bool):
    if not re.fullmatch(r"\d{6}", ym):
        raise ValueError("Month must be YYYYMM")
    year = int(ym[:4])
    month = int(ym[4:6])

    out_path = output_dir / str(year) / f"stacked_keograms_{ym}.png"
    interactive_dir = Path.home() / "Documents" / "keogram_project" / "interactive_stacks" / f"{year}{month:02d}"
    interactive_path = interactive_dir / out_path.name

    if skip_existing and out_path.exists():
        print(f"[skip] {out_path} (exists)")
        return

    # First pass: discover available hour windows per day and overall
    day_windows = []
    global_h0, global_h1 = 24, 0
    min_day = max_day = None
    for day in range(1, month_days(year, month) + 1):
        hrefs = list_day_files(year, month, day, station)
        hours = []
        for h in hrefs:
            mh = re.search(r"ut(\d{1,2})/?$", h, flags=re.IGNORECASE)
            if mh:
                hh = int(mh.group(1))
                if 0 <= hh <= 24:
                    hours.append(hh)
        if hours:
            h0, h1 = min(hours), max(hours) + 1
            day_windows.append((day, h0, h1))
            if h0 < global_h0:
                global_h0 = h0
                min_day = day
            if h1 > global_h1:
                global_h1 = h1
                max_day = day

    if global_h0 >= global_h1:
        global_h0, global_h1 = 0, 24
        min_day = max_day = None

    images = []
    for day, h0, h1 in day_windows:
        try:
            img, fname = pick_day_image(year, month, day, station, camera)
            if img is None:
                print(f"[miss] {fname}")
                continue
        except Exception as e:
            print(f"[miss] {year:04d}{month:02d}{day:02d} ({e})")
            continue
        # Crop to shared hour window across the month
        W = img.width
        x0 = int(W * (global_h0 / 24.0))
        x1 = int(W * (global_h1 / 24.0))
        x0 = max(0, min(W, x0))
        x1 = max(0, min(W, x1))
        if x1 <= x0:
            x1 = min(W, x0 + 1)
        img = img.crop((x0, 0, x1, img.height))

        h = img.height
        new_w = int(aspect * h)
        img = img.resize((new_w, h), Image.LANCZOS)
        images.append(img)

    if not images:
        print(f"[warn] No images found for {ym}")
        return

    widths, heights = zip(*(im.size for im in images))
    max_width = max(widths)
    total_height = sum(heights)

    stacked = Image.new("RGB", (max_width, total_height))
    y_offset = 0
    for im in images:
        if im.width < max_width:
            padded = Image.new("RGB", (max_width, im.height), (0, 0, 0))
            padded.paste(im, (0, 0))
            im = padded
        stacked.paste(im, (0, y_offset))
        y_offset += im.height

    draw = ImageDraw.Draw(stacked)
    span = max(global_h1 - global_h0, 1)
    for hour in (6, 12):
        if global_h0 <= hour <= global_h1:
            rel = (hour - global_h0) / span
            x = int(max_width * rel)
            draw.line((x, 0, x, total_height), fill="white", width=10)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    interactive_dir.mkdir(parents=True, exist_ok=True)
    stacked.save(out_path)
    stacked.save(interactive_path)
    print(f"[saved] {out_path}")
    print(f"[saved] {interactive_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stream and stack keogram PNGs by month from AMISR.")
    parser.add_argument("--month", help="Month to stack, format YYYYMM")
    parser.add_argument("--station", default=DEFAULT_STATION, help="Station folder (default: pfrr_amisr01)")
    parser.add_argument("--camera", default=DEFAULT_CAMERA, help="Camera code in filename (default: asi3)")
    parser.add_argument("--out", default=str(Path.home() / "Documents" / "keogram_project" / "stacked_by_month"),
                        help="Output folder for stacked images")
    parser.add_argument("--aspect", type=float, default=10.0, help="Width:height ratio to enforce on each keogram")
    parser.add_argument("--hours", default="6,12", help="Comma-separated UTC hours to draw reference lines")
    parser.add_argument("--skip-existing", action="store_true", help="Skip if output already exists")
    args = parser.parse_args()

    month = args.month or input("Which month to stack? (YYYYMM): ").strip()
    if not month:
        print("Month is required."); sys.exit(1)
    if not re.fullmatch(r"\d{6}", month):
        print("Month must be YYYYMM."); sys.exit(1)

    hours_tuple = tuple(int(h) for h in args.hours.split(",") if h.strip().isdigit()) or (6, 12)
    out_dir = Path(args.out)

    stack_keograms_for_month(
        ym=month,
        station=args.station,
        camera=args.camera,
        output_dir=out_dir,
        aspect=args.aspect,
        draw_hours=hours_tuple,
        skip_existing=args.skip_existing,
    )
