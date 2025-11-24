#!/usr/bin/env python3
# dscovr_direct.py
# Download DSCOVR daily files (mg1/m1s/m1m/etc.) straight from the public directory.
import argparse, datetime as dt, os, re, shutil, subprocess
from urllib.parse import urljoin

import requests  # pip install requests

BASE = "https://www.ngdc.noaa.gov/dscovr/data/"

def daterange(d0, d1):
    while d0 <= d1:
        yield d0
        d0 += dt.timedelta(days=1)

def index_url(day):
    return f"{BASE}{day.year}/{day:%m}/"

def find_daily_files(index_html: str, dtype: str, day: dt.date):
    ymd = day.strftime("%Y%m%d")
    # Match e.g. oe_mg1_dscovr_sYYYYMMDD000000_eYYYYMMDD235959_pYYYYMMDDHHMMSS_pub.nc.gz
    pat = re.compile(
        rf'href="(oe_{dtype}_dscovr_s{ymd}000000_e{ymd}235959_p\d+_pub\.nc\.gz)"'
    )
    return pat.findall(index_html)

def fetch(url: str, outdir: str):
    os.makedirs(outdir, exist_ok=True)
    dst = os.path.join(outdir, os.path.basename(url))

    if shutil.which("wget"):
        # -c continue, -N timestamping, -nv quiet-ish, -P output dir
        subprocess.run(["wget", "-nv", "-c", "-N", "-P", outdir, url], check=True)
        return dst
    elif shutil.which("curl"):
        # resume with -C -, follow redirects, write to file
        subprocess.run(["curl", "-L", "-C", "-", "-o", dst, url], check=True)
        return dst
    else:
        # pure Python fallback
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dst, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)
        return dst

def main():
    ap = argparse.ArgumentParser(description="Download DSCOVR daily files directly.")
    ap.add_argument("start", nargs="?", help="YYYY-MM-DD (UTC)")
    ap.add_argument("end", nargs="?", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--dtype", default="mg1",
                    choices=["mg1","m1s","m1m","mg0","fc0","fc1","f1m","f3s"],
                    help="Product type (mg1=full-rate magnetometer).")
    ap.add_argument(
        "--outdir",
        default="/Users/anniepflaum/Documents/keogram_project/DSCOVR_data",
        help="Where to store downloads"
    )
    args = ap.parse_args()

    if not args.start:
        args.start = input("Start date (YYYY-MM-DD): ").strip()
    if not args.end:
        args.end = input("End date inclusive (YYYY-MM-DD): ").strip()

    start = dt.date.fromisoformat(args.start)
    end   = dt.date.fromisoformat(args.end)

    downloaded = []
    for day in daterange(start, end):
        idx = index_url(day)
        print(f"Index: {idx}")
        r = requests.get(idx, timeout=60)
        if r.status_code != 200:
            print(f"  ! index not available ({r.status_code})")
            continue

        files = find_daily_files(r.text, args.dtype, day)
        if not files:
            print(f"  ! no {args.dtype} file published for {day}")
            continue

        for fname in files:
            url = urljoin(idx, fname)
            print(f"  -> {fname}")
            try:
                dst = fetch(url, os.path.join(args.outdir, f"{day:%Y-%m}"))
                downloaded.append(dst)
            except Exception as e:
                print(f"  ! download failed: {e}")

    if downloaded:
        print("\nDone. Files:")
        for p in downloaded:
            print("  ", p)
    else:
        print("\nNo files downloaded.")

if __name__ == "__main__":
    main()
