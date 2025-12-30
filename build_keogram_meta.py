#!/usr/bin/env python3
import argparse
import re
import json
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
import sys

# WARNING: we disable TLS certificate verification for this script,
# because optics.gi.alaska.edu uses a cert chain that your local
# Python/OpenSSL combo can't validate. This is similar to
# `wget --no-check-certificate`.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://optics.gi.alaska.edu/amisr_archive/Processed_data/aurorax/stream2"
VIDEO_BASE = "https://optics.gi.alaska.edu/realtime/data/MPEG/PKR_DASC_512"
STATION_TAG = "PKR_DASC"   # base of video filenames


def fetch_html(url: str) -> str:
    # Critical: disable verification (trusted host, science data)
    r = requests.get(url, timeout=20, verify=False)
    r.raise_for_status()
    return r.text


def video_exists_for_day(ymd: str) -> bool:
    """
    Check whether PKR_DASC_YYYYMMDD_rgb_512.mp4 exists on the MPEG server.
    Returns True if HTTP status is 2xx/3xx, False otherwise.
    """
    url = f"{VIDEO_BASE}/{STATION_TAG}_{ymd}_rgb_512.mp4"
    try:
        r = requests.head(url, timeout=20, verify=False, allow_redirects=True)
    except requests.RequestException as e:
        print(f"  [warn] HEAD {url} failed: {e}")
        return False

    ok = 200 <= r.status_code < 400
    return ok


def list_day_dirs(year: int, month: int):
    """
    Return sorted list of day directory names like 'YYYYMMDD'
    by scraping links like '03/' from .../YYYY/MM/.

    Month index shows:
      href="?C=N;O=D", ...
      href="/amisr_archive/Processed_data/aurorax/stream2/2025/"
      href="03/"
      href="04/"
      ...

    We:
      - pick hrefs that are exactly 'DD/'
      - convert them to 'YYYYMMDD' strings.
    """
    url = f"{BASE}/{year}/{month:02d}/"
    html = fetch_html(url)

    hrefs = re.findall(r'href="([^"]+)"', html)

    day_set = set()
    for h in hrefs:
        h = h.strip()

        # Match exactly "DD/" where DD is 2 digits (01–31)
        if re.fullmatch(r"\d{2}/", h):
            dd = int(h[:2])
            if 1 <= dd <= 31:
                ymd = f"{year}{month:02d}{dd:02d}"   # e.g. 2025,11,3 -> "20251103"
                day_set.add(ymd)

    day_dirs = sorted(day_set)
    return url, day_dirs

def extract_day_time_extent(day_url: str):
    """
    Given a URL like .../2025/11/03/, step into the instrument
    subdirectory (pfrr_amisr01) and then look for utXX subdirs to
    determine the hour coverage.
    """
    # 1) Find instrument subdir (pfrr_amisr01) under the day
    html = fetch_html(day_url)
    hrefs = re.findall(r'href="([^"]+)"', html)

    inst_href = None
    for h in hrefs:
        h = h.strip()
        if "pfrr_amisr01" in h:
            # Drop any ?C=... junk
            inst_href = h.split("?")[0]
            break

    if inst_href is None:
        print(f"  No pfrr_amisr01 dir found under {day_url}")
        return None, None

    inst_url = urljoin(day_url, inst_href)

    # 2) Inside pfrr_amisr01/, look for utXX/ dirs
    html_inst = fetch_html(inst_url)
    hrefs_inst = re.findall(r'href="([^"]+)"', html_inst)

    hours = []
    for h in hrefs_inst:
        h = h.strip()
        core = h.split("?")[0]  # strip any query params
        # Match ut0, ut02, ut23, etc, at end of the path
        mh = re.search(r"ut(\d{1,2})/?$", core, flags=re.IGNORECASE)
        if mh:
            hh = int(mh.group(1))
            if 0 <= hh <= 24:
                hours.append(hh)

    if not hours:
        print(f"  No utXX dirs found under {inst_url}")
        return None, None

    h0 = float(min(hours))
    h1 = float(max(hours) + 1)  # [h0, h1) like your keogram code
    return h0, h1

def build_meta(year: int, month: int, out_path: str):
    month_url, day_dirs = list_day_dirs(year, month)

    day_entries = []
    all_h0 = []
    all_h1 = []

    for ymd in day_dirs:
        # ymd is 'YYYYMMDD' from list_day_dirs, e.g. '20251103'
        day_num = int(ymd[-2:])          # 3
        day_href = f"{day_num:02d}/"     # "03/"

        # Actual directory on the server is .../YYYY/MM/DD/
        day_url = urljoin(month_url, day_href)

        h0, h1 = extract_day_time_extent(day_url)

        # If this day has no data, skip it (like your stack_keograms_for_month
        # only adds a day when "hours" is non-empty)
        if h0 is None or h1 is None:
            continue

        # Check if there is a corresponding MPEG video
        has_video = video_exists_for_day(ymd)

        entry = {
            "ymd": ymd,      # '20251103' – used later to build video filename
            "day": day_num,  # 3
            "h0": h0,
            "h1": h1,
            "has_video": has_video,
        }
        day_entries.append(entry)
        all_h0.append(h0)
        all_h1.append(h1)

    # Compute global hour range across all days that had data
    if all_h0 and all_h1:
        global_h0 = min(all_h0)
        global_h1 = max(all_h1)
    else:
        # If somehow nothing had hours, fall back to full 0–24
        global_h0 = 0.0
        global_h1 = 24.0

    meta = {
        "year": year,
        "month": month,
        "days": day_entries,           # one entry per day in stack
        "global_min_hour": global_h0,
        "global_max_hour": global_h1,
    }

    with open(out_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\nWrote metadata to {out_path}")
    print(f"Global hour range: {global_h0:.2f}–{global_h1:.2f} UT")
    print(f"Days in stack: {[d['ymd'] for d in day_entries]}")

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build keogram metadata for a month.")
    parser.add_argument("--month", help="Target month in YYYYMM format")
    args = parser.parse_args()

    if args.month:
        try:
            YEAR, MONTH = parse_year_month(args.month)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        YEAR, MONTH = prompt_year_month()
    stack_dir = Path("/Users/anniepflaum/Documents/keogram_project/interactive_stacks") / f"{YEAR}{MONTH:02d}"
    stack_dir.mkdir(parents=True, exist_ok=True)  # ensure YYYYMM folder exists

    out_path = stack_dir / f"keogram_meta_{YEAR}{MONTH:02d}.json"
    build_meta(YEAR, MONTH, out_path)
