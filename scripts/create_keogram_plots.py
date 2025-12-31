#!/usr/bin/env python3
import io, os, re, sys, gzip, shutil, subprocess, builtins
from pathlib import Path
from datetime import datetime, timedelta

import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from bs4 import BeautifulSoup
from PIL import Image
from netCDF4 import Dataset, num2date

# ---------------- Config ----------------
AMISR_BASE = "https://optics.gi.alaska.edu/amisr_archive/Processed_data/aurorax/stream2"
STATION    = "pfrr_amisr01"
CAMERA     = "asi3"   # ..._asi3_rgb-keogram.png

GOES_BASE  = "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/goes18/l1b/mag-l1b-flat"
DSCVR_BASE = "https://www.ngdc.noaa.gov/dscovr/data"

OUT_FULL   = Path.home() / "keogram_project" / "overlaid_full"
OUT_PART   = Path.home() / "keogram_project" / "overlaid_partial"
OUT_FULL.mkdir(parents=True, exist_ok=True)
OUT_PART.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "keogram-overlay/streaming (cross-platform)"}
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0.0.0 Safari/537.36")
REQ_RETRIES = 3
CURL_CONNECT_TIMEOUT = "30"
CURL_MAX_TIME = "120"

# --------------- HTTP helpers ---------------
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


def http_get_text(url, timeout=30):
    errors = []
    # Prefer curl (works even if Python DNS has issues)
    if shutil.which("curl"):
        try:
            return _curl_fetch(url, as_text=True)
        except Exception as e:
            errors.append(str(e))

    # Retry requests a few times
    for attempt in range(1, REQ_RETRIES + 1):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r.text
            errors.append(f"requests HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"requests error {e}")
    detail = "; ".join(errors) if errors else "no detail"
    raise RuntimeError(f"Failed to GET HTML: {url} ({detail})")

def http_get_bytes(url, timeout=120):
    errors = []
    if shutil.which("curl"):
        try:
            return _curl_fetch(url, as_text=False)
        except Exception as e:
            errors.append(str(e))
    for attempt in range(1, REQ_RETRIES + 1):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r.content
        except Exception as e:
            errors.append(f"requests error {e}")
    detail = "; ".join(errors) if errors else "no detail"
    raise RuntimeError(f"Failed to GET bytes: {url} ({detail})")

# --------------- AMISR scraping ---------------
def amisr_day_url(y, m, d):
    return f"{AMISR_BASE}/{y}/{m}/{d}/{STATION}/"

def list_hours_for_day(y, m, d):
    html = http_get_text(amisr_day_url(y, m, d))
    soup = BeautifulSoup(html, "html.parser")
    hours = []
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip("/")
        m = re.fullmatch(r"ut(\d{1,2})/?", href, flags=re.IGNORECASE)
        if m:
            hh = int(m.group(1))
            if 0 <= hh <= 24:
                hours.append(hh)
    if not hours:
        raise RuntimeError("No utHH/ folders found.")
    hours = sorted(set(hours))
    return hours[0], hours[-1] + 1  # [start, end)

def pick_hour_filename(y, m, d, hh):
    ymd = f"{y}{m}{d}"
    site = STATION.split("_", 1)[0]  # 'pfrr'
    hdir = f"{amisr_day_url(y, m, d)}ut{int(hh):02d}/"
    html = http_get_text(hdir)
    exact = f"{ymd}_{int(hh):02d}_{site}_{CAMERA}_rgb-keogram.png"
    if exact in html:
        return exact
    mobj = re.search(
        rf"{ymd}_(?:{int(hh):02d}|{int(hh)})_{re.escape(site)}_{re.escape(CAMERA)}_rgb-keogram\.png",
        html, flags=re.IGNORECASE
    )
    if mobj:
        return mobj.group(0)
    return None

def fetch_hour_keogram(y, m, d, hh):
    fname = pick_hour_filename(y, m, d, hh)
    if not fname:
        return None
    url = f"{amisr_day_url(y, m, d)}ut{int(hh):02d}/{fname}"
    data = http_get_bytes(url)
    return Image.open(io.BytesIO(data)).convert("RGB")

def stitch_hours(y, m, d, h0, h1):
    imgs = []
    for hh in range(h0, h1):
        im = fetch_hour_keogram(y, m, d, hh)
        if im is None:
            continue
        imgs.append(im)
    if not imgs:
        raise RuntimeError("No hourly keogram images available in the window.")
    hmin = min(im.height for im in imgs)
    fixed = []
    for im in imgs:
        if im.height != hmin:
            nw = int(im.width * (hmin / im.height))
            im = im.resize((nw, hmin), Image.BICUBIC)
        fixed.append(im)
    W = sum(im.width for im in fixed)
    canvas = Image.new("RGB", (W, hmin), (0, 0, 0))
    x = 0
    for im in fixed:
        canvas.paste(im, (x, 0))
        x += im.width
    return np.array(canvas)

# ---- Keogram (in-memory) ----
def find_full_keogram_name(y, m, d):
    """Return the full-keogram filename in the day directory."""
    day_html = http_get_text(amisr_day_url(y, m, d))
    ymd = f"{y}{m}{d}"
    # Try the known canonical name first
    exact = f"{ymd}__pfrr_asi3_full-keo-rgb.png"
    if exact in day_html:
        return exact
    # Robust regex fallback (allow hyphen/underscore variants)
    mobj = re.search(
        rf"{ymd}__pfrr_asi3_.*?full[-_]?keo[-_]?rgb\.png",
        day_html, flags=re.IGNORECASE
    )
    return mobj.group(0) if mobj else None

def fetch_full_keogram(y, m, d):
    fname = find_full_keogram_name(y, m, d)
    if not fname:
        raise RuntimeError("Full-day keogram PNG not found in day directory.")
    url = f"{amisr_day_url(y, m, d)}{fname}"
    data = http_get_bytes(url)
    return Image.open(io.BytesIO(data)).convert("RGB")

# --------------- GOES (in-memory) ---------------
def goes_day_nc_url(y, m, d):
    idx = f"{GOES_BASE}/{y}/{m}/"
    html = http_get_text(idx)
    mobj = re.search(rf"ops_mag-l1b-flat_g18_d{y}{m}{d}_v[\d\-]+\.nc", html)
    if not mobj:
        raise RuntimeError("GOES daily file not found in month index.")
    return idx + mobj.group(0)

def load_goes_hp_inmemory(y, m, d):
    url = goes_day_nc_url(y, m, d)
    nc_bytes = http_get_bytes(url)
    ds = Dataset("inmem", mode="r", memory=nc_bytes)
    if 'OB_time' not in ds.variables or 'OB_mag_EPN' not in ds.variables:
        ds.close()
        raise RuntimeError("GOES variables missing (OB_time/OB_mag_EPN).")
    t = num2date(ds.variables['OB_time'][:], ds.variables['OB_time'].units)
    hrs = np.array([(np.datetime64(tt) - np.datetime64(f"{y}-{m}-{d}T00:00"))
                    .astype('timedelta64[s]').astype(float) / 3600 for tt in t])
    hp = ds.variables['OB_mag_EPN'][:, 1]  # Hp
    ds.close()
    return hrs, np.asarray(hp)

# --------------- DSCOVR (in-memory) ---------------
def dscovr_day_nc_url(y, m, d):
    idx = f"{DSCVR_BASE}/{y}/{m}/"
    html = http_get_text(idx)
    pat = rf"oe_mg1_dscovr_s{y}{m}{d}000000_e{y}{m}{d}235959_p\d+_pub\.nc(?:\.gz)?"
    mobj = re.search(pat, html)
    if not mobj:
        raise RuntimeError("DSCOVR daily file not found in month index.")
    return idx + mobj.group(0)

def load_dscovr_bz_inmemory(y, m, d):
    url = dscovr_day_nc_url(y, m, d)
    raw = http_get_bytes(url)
    if url.endswith(".gz"):
        raw = gzip.decompress(raw)
    ds = Dataset("inmem", mode="r", memory=raw)
    if 'time' not in ds.variables or 'bz_gse' not in ds.variables:
        ds.close()
        raise RuntimeError("DSCOVR variables missing (time/bz_gse).")
    tt = num2date(ds.variables['time'][:], ds.variables['time'].units,
                  only_use_cftime_datetimes=False)
    bz = ds.variables['bz_gse'][:]
    ds.close()
    df = (pd.DataFrame({"time": pd.to_datetime(tt), "bz": bz})
          .dropna()
          .set_index("time")
          .resample("1min").mean())
    df["hour"] = (df.index - pd.Timestamp(f"{y}-{m}-{d}")).total_seconds()/3600
    return df

# --------------- Overlay ----------------
def make_overlay_for_day(date_str, mode):
    y, m, d = date_str[:4], date_str[4:6], date_str[6:]

    # Decide hour window
    if mode == "full":
        h0, h1 = list_hours_for_day(y, m, d)   # scrape window
        # Fetch full-day keogram and crop pixels to [h0, h1)
        full_img = fetch_full_keogram(y, m, d)  # PIL Image
        img = np.array(full_img)
        W = img.shape[1]
        x0 = int(W * (h0 / 24.0))
        x1 = int(W * (h1 / 24.0))
        x0 = max(0, min(W, x0)); x1 = max(0, min(W, x1))
        if x1 <= x0: x1 = min(W, x0 + 1)
        keo = img[:, x0:x1]
    else:
        # Partial → prompt for hours and stitch only those
        h0 = int(input("Start hour (00-23): ").strip())
        h1 = int(input("End hour (exclusive, 01-24): ").strip())
        keo = stitch_hours(y, m, d, h0, h1)

    # Load series and crop to window
    gh, hp = load_goes_hp_inmemory(y, m, d)
    gmask = (gh >= h0) & (gh < h1)
    gh, hp = gh[gmask], hp[gmask]

    df = load_dscovr_bz_inmemory(y, m, d)
    df = df[(df["hour"] >= h0) & (df["hour"] < h1)]

    # Plot
    fig, ax1 = plt.subplots(figsize=(12, 6))
    ax1.imshow(keo, aspect="auto", extent=[h0, h1, 0, 1])
    ax1.set_xlim(h0, h1)
    ax1.set_ylim(0, 1)
    ax1.tick_params(left=False, labelleft=False)
    ax1.spines["left"].set_visible(False)
    ax1.set_xticks(np.arange(h0, h1 + 1))
    ax1.set_xlabel("Time (Hours UTC)")

    ax2 = ax1.twinx()
    if gh.size:
        ax2.plot(gh, hp, color="#f28e2b")  # GOES in orange
        go_min, go_max = np.nanmin(hp), np.nanmax(hp)
        ax2.set_ylim(min(0, go_min), max(130, go_max))
    ax2.set_ylabel("Hp (nT)", color="#f28e2b")
    ax2.tick_params(axis="y", colors="#f28e2b")
    ax2.spines["right"].set_color("#f28e2b")
    ax2.set_xlim(h0, h1)

    ax3 = ax1.twinx()
    ax3.spines["right"].set_position(("outward", 60))
    if not df.empty:
        ax3.plot(df["hour"], df["bz"], linewidth=1.5, color="#1f77b4")
        bz_min, bz_max = df["bz"].min(), df["bz"].max()
        ax3.set_ylim(min(-15, bz_min), max(15, bz_max))
    ax3.set_ylabel("Bz GSE (nT)", color="#1f77b4")
    ax3.tick_params(axis="y", colors="#1f77b4")
    ax3.spines["right"].set_color("#1f77b4")
    ax3.set_xlim(h0, h1)
    ax3.axhline(0, linestyle="--", linewidth=1, alpha=0.7)

    plt.title(f"GOES-18 Hp and DSCOVR Bz over Keogram: {date_str}")
    plt.tight_layout()

    out_root = OUT_PART if mode == "partial" else OUT_FULL
    out = out_root / y / m
    out.mkdir(parents=True, exist_ok=True)
    out_png = out / f"{date_str}_overlay_streaming.png"
    plt.savefig(out_png, dpi=300)
    plt.close()
    print(f"[SAVED] {out_png}")

# --------------- CLI ----------------
if __name__ == "__main__":
    mode_in = input("Mode ('full'/'f' or 'partial'/'p'): ").strip().lower()
    if mode_in not in ("full", "partial", "f", "p"):
        print("Enter 'full' or 'partial'."); sys.exit(1)
    mode = "full" if mode_in.startswith("f") else "partial"

    def parse_any(s):
        return datetime.strptime(s, "%Y%m%d") if re.fullmatch(r"\d{8}", s) \
               else datetime.strptime(s, "%Y-%m-%d")

    if mode == "partial":
        one = input("Date (YYYY-MM-DD or YYYYMMDD): ").strip()
        try:
            d = parse_any(one)
        except Exception as e:
            print(f"[ERR] Bad date format: {e}"); sys.exit(1)
        y, m, dday = d.strftime("%Y"), d.strftime("%m"), d.strftime("%d")
        try:
            h_start, h_end = list_hours_for_day(y, m, dday)
        except Exception as e:
            print(f"[ERR] Could not scrape available hours: {e}"); sys.exit(1)
        print(f"Available hour window: {h_start:02d}-{(h_end-1):02d}")
        try:
            h0_in = input(f"Start hour [{h_start:02d}-{h_end-1:02d}]: ").strip() or f"{h_start:02d}"
            h1_in = input(f"End hour exclusive [{h_start+1:02d}-{h_end:02d}]: ").strip() or f"{h_end:02d}"
            h0 = int(h0_in); h1 = int(h1_in)
        except ValueError:
            print("[ERR] Hours must be integers."); sys.exit(1)
        if h0 < h_start or h1 > h_end or h1 <= h0:
            print("[ERR] Hours must be within available window and end > start."); sys.exit(1)
        # Patch hour prompts into the overlay function by temporarily wrapping input
        orig_input = builtins.input
        def _patched_input(prompt=""):
            if "Start hour" in prompt:
                return f"{h0:02d}"
            if "End hour" in prompt:
                return f"{h1:02d}"
            return orig_input(prompt)
        builtins.input = _patched_input
        try:
            try:
                make_overlay_for_day(d.strftime("%Y%m%d"), mode)
            except Exception as e:
                print(f"[SKIP {d.strftime('%Y%m%d')}] {e}")
        finally:
            builtins.input = orig_input
    else:
        start_in = input("Start date (YYYY-MM-DD or YYYYMMDD): ").strip()
        end_in   = input("End date   (YYYY-MM-DD or YYYYMMDD): ").strip()

        try:
            d0 = parse_any(start_in)
            d1 = parse_any(end_in)
        except Exception as e:
            print(f"[ERR] Bad date format: {e}"); sys.exit(1)
        if d1 < d0:
            print("[ERR] End date must be >= start date."); sys.exit(1)

        cur = d0
        while cur <= d1:
            try:
                make_overlay_for_day(cur.strftime("%Y%m%d"), mode)
            except Exception as e:
                print(f"[SKIP {cur.strftime('%Y%m%d')}] {e}")
            cur += timedelta(days=1)
