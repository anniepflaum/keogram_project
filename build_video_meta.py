#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone, date
from collections import deque
import json
import re
import tempfile
import argparse

import cv2
import pytesseract
from PIL import Image
import requests
import urllib3
import sys

# --- CONFIG ---
BASE_URL = "https://optics.gi.alaska.edu/realtime/data/MPEG/PKR_DASC_512/"

# how many frames to keep from the start / end
HEAD_MAX = 120     # search this many early frames for start time
TAIL_MAX = 80      # last N frames for end time

# debugging controls
DEBUG = True
DEBUG_HEAD_PRINT = 10   # how many head frames to dump OCR for if start not found
DEBUG_TAIL_PRINT = 0    # set >0 if you ever want similar debug for end

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATETIME_REGEX = re.compile(
    r"(\d{4})[/-](\d{2})[/-](\d{2}).*?(\d{2}):(\d{2}):(\d{2})",
    re.S,
)

TIME_REGEX = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})"
)


def fetch_html(url: str) -> str:
    print(f"Fetching index: {url}")
    r = requests.get(url, timeout=30, verify=False)
    r.raise_for_status()
    return r.text


def list_remote_videos_for_month(year: int, month: int):
    """
    Scrape BASE_URL and return list of (filename, full_url) for videos
    in the given year/month.
    """
    html = fetch_html(BASE_URL)
    hrefs = re.findall(r'href="([^"]+)"', html)

    ym = f"{year}{month:02d}"
    files: list[tuple[str, str]] = []

    for h in hrefs:
        h = h.strip()
        if not h.lower().endswith(".mp4"):
            continue
        if ym in h:
            files.append((h, BASE_URL + h))

    files.sort()
    print(f"Found {len(files)} remote videos for {ym}")
    return files


def date_from_filename(name: str) -> date | None:
    m = re.search(r"(\d{4})(\d{2})(\d{2})", name)
    if not m:
        return None
    y, mth, d = map(int, m.groups())
    return date(y, mth, d)


def parse_timestamp_from_text(text: str, fallback_date: date | None) -> datetime | None:
    text = text.strip()

    # full YYYY/MM/DD + hh:mm:ss
    m = DATETIME_REGEX.search(text)
    if m:
        year, month, day, hh, mm, ss = map(int, m.groups())
        return datetime(year, month, day, hh, mm, ss, tzinfo=timezone.utc)

    # hh:mm:ss only, with fallback date
    if fallback_date is not None:
        mt = TIME_REGEX.search(text)
        if mt:
            hh, mm, ss = map(int, mt.groups())
            return datetime(
                fallback_date.year,
                fallback_date.month,
                fallback_date.day,
                hh, mm, ss,
                tzinfo=timezone.utc,
            )

    return None


def extract_timestamp_from_frame(frame, fallback_date: date | None) -> datetime | None:
    """
    OCR the bottom-left time bar (hh:mm:ss) using fallback_date from filename.
    This is much more reliable than full-frame OCR.
    """
    h, w = frame.shape[:2]

    # Tight ROI around the black time bar (based on your attached frame)
    left_frac   = 0.00
    right_frac  = 0.60
    top_frac    = 0.93
    bottom_frac = 1.00

    x0 = int(left_frac * w)
    x1 = int(right_frac * w)
    y0 = int(top_frac * h)
    y1 = int(bottom_frac * h)

    roi = frame[y0:y1, x0:x1]

    # --- preprocess for OCR: grayscale -> upscale -> blur -> Otsu -> invert -> pad
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Tesseract is happier with dark text on light background
    if th.mean() < 127:
        th = cv2.bitwise_not(th)

    th = cv2.copyMakeBorder(th, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)

    config = "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789:UTC "
    text = pytesseract.image_to_string(Image.fromarray(th), config=config)

    dt = parse_timestamp_from_text(text, fallback_date=fallback_date)
    if dt is not None:
        return dt

    # Optional: fall back to full-frame OCR if you want (slower, usually worse)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    text_full = pytesseract.image_to_string(Image.fromarray(rgb))
    return parse_timestamp_from_text(text_full, fallback_date)


def debug_ocr_for_head_frames(head_frames, fallback_date: date | None):
    """
    Print OCR text for the first few head frames, like your previous
    'OCR text (head[i] full): ...' debugging.
    """
    left_frac = 0.00
    right_frac = 0.60
    top_frac = 0.93
    bottom_frac = 1.00


    for i, frame in enumerate(head_frames[:DEBUG_HEAD_PRINT]):
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_full = Image.fromarray(rgb)
        text_full = pytesseract.image_to_string(pil_full)
        print(f"OCR text (head[{i}] full): {text_full!r}")

        x0 = int(left_frac * w)
        x1 = int(right_frac * w)
        y0 = int(top_frac * h)
        y1 = int(bottom_frac * h)

        crop = frame[y0:y1, x0:x1]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        pil_crop = Image.fromarray(thresh)
        text_crop = pytesseract.image_to_string(pil_crop)
        print(f"OCR text (head[{i}] crop): {text_crop!r}")


def download_video_to_temp(url: str) -> Path:
    print(f"  Downloading {url}")
    r = requests.get(url, stream=True, timeout=120, verify=False)
    r.raise_for_status()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    with tmp as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)

    tmp_path = Path(tmp.name)
    print(f"  Saved to temp file {tmp_path}")
    return tmp_path


def get_video_timerange(name: str, url: str) -> tuple[datetime | None, datetime | None]:
    """
    Download video, scan head/tail frames for timestamps, then delete the temp file.
    """
    print(f"\n=== Processing {name} ===")

    tmp_path = download_video_to_temp(url)

    try:
        cap = cv2.VideoCapture(str(tmp_path))
        if not cap.isOpened():
            print("  Could not open video.")
            return None, None

        head_frames = []
        tail_frames = deque(maxlen=TAIL_MAX)

        n = 0
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            n += 1

            if len(head_frames) < HEAD_MAX:
                head_frames.append(frame.copy())
            tail_frames.append(frame.copy())

        cap.release()
        print(f"  Total frames read: {n}")

        if not head_frames:
            return None, None

        # --- START TIMESTAMP ---
        fallback_date = date_from_filename(name)
        start_dt = None
        for idx, f in enumerate(head_frames):
            start_dt = extract_timestamp_from_frame(f, fallback_date=fallback_date)
            if start_dt is not None:
                if DEBUG:
                    print(f"  Start timestamp found on head frame {idx}")
                break

        if start_dt is None and DEBUG:
            print("  Could not find start timestamp; dumping OCR for first frames...")
            debug_ocr_for_head_frames(head_frames, fallback_date=fallback_date)

        # --- END TIMESTAMP ---
        end_dt = None
        if tail_frames:
            end_fallback_date = start_dt.date() if start_dt else fallback_date
            for i, f in enumerate(reversed(tail_frames)):
                end_dt = extract_timestamp_from_frame(f, fallback_date=end_fallback_date)
                if end_dt is not None:
                    if DEBUG and DEBUG_TAIL_PRINT > 0:
                        print(f"  End timestamp found in tail frame index from end: {i}")
                    break

        print(f"  Start: {start_dt}")
        print(f"  End  : {end_dt}")
        if start_dt and end_dt:
            dur = (end_dt - start_dt).total_seconds()
            print(f"  Duration (from OCR): {dur:.1f} s")

        return start_dt, end_dt

    finally:
        try:
            tmp_path.unlink()
            print(f"  Deleted temp file {tmp_path}")
        except Exception:
            pass


def dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_year_month(raw: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})(\d{2})", raw)
    if not m:
        raise ValueError("Please enter 6 digits in the form YYYYMM.")

    year = int(m.group(1))
    month = int(m.group(2))
    if 1 <= month <= 12:
        return year, month

    raise ValueError("Month must be between 01 and 12.")


def prompt_year_month() -> tuple[int, int]:
    """
    Prompt for YYYYMM and return (year, month).
    """
    prompt = "Enter target month as YYYYMM (e.g., 202511): "
    while True:
        raw = input(prompt).strip()
        try:
            return parse_year_month(raw)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Build video metadata for a month.")
    parser.add_argument("--month", help="Target month in YYYYMM format")
    args = parser.parse_args()

    if args.month:
        try:
            year, month = parse_year_month(args.month)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        year, month = prompt_year_month()

    stack_dir = Path("/Users/anniepflaum/keogram_project/interactive_stacks") / f"{year}{month:02d}"
    stack_dir.mkdir(parents=True, exist_ok=True)  # ensure YYYYMM folder exists

    out_path = stack_dir / f"video_meta_{year}{month:02d}.json"

    remote_files = list_remote_videos_for_month(year, month)
    if not remote_files:
        print("No remote videos found for this month.")
        return

    by_ymd: dict[str, dict[str, str | None]] = {}

    for name, url in remote_files:
        day = date_from_filename(name)
        if day is None:
            print(f"Skipping {name} (no YYYYMMDD in filename).")
            continue

        ymd = f"{day.year}{day.month:02d}{day.day:02d}"
        start_dt, end_dt = get_video_timerange(name, url)

        if start_dt is None or end_dt is None:
            print(f"  WARNING: missing start/end for {name}")
            continue

        rec = by_ymd.get(ymd)
        if rec is None:
            by_ymd[ymd] = {
                "ymd": ymd,
                "video_file": name,
                "start_utc": dt_to_iso(start_dt),
                "end_utc": dt_to_iso(end_dt),
            }
        else:
            existing_start = datetime.fromisoformat(rec["start_utc"].replace("Z", "+00:00"))
            existing_end = datetime.fromisoformat(rec["end_utc"].replace("Z", "+00:00"))

            if start_dt < existing_start:
                rec["start_utc"] = dt_to_iso(start_dt)
                rec["video_file"] = name
            if end_dt > existing_end:
                rec["end_utc"] = dt_to_iso(end_dt)
                rec["video_file"] = name

    meta = {
        "year": year,
        "month": month,
        "videos": sorted(by_ymd.values(), key=lambda d: d["ymd"]),
    }

    with open(out_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\nWrote video metadata to {out_path}")


if __name__ == "__main__":
    main()
