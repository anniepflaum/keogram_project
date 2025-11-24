import os
import re
import requests
import shutil
from bs4 import BeautifulSoup
import matplotlib.pyplot as plt
import numpy as np
from netCDF4 import Dataset, num2date
from PIL import Image
import gzip
import tempfile
import pandas as pd
from pathlib import Path

# Configuration: directories
KEOGRAM_DIR = Path('/Users/anniepflaum/Documents/keogram_project/full_keograms')
GOES_DIR    = Path('/Users/anniepflaum/Documents/keogram_project/GOES_18_data')
DSCOVR_DIR  = Path('/Users/anniepflaum/Documents/keogram_project/DSCOVR_data')
OUTPUT_DIR  = Path('/Users/anniepflaum/Documents/keogram_project/overlaid_full_plots')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# AMISR base URL template for scraping hours
AMISR_URL = 'https://optics.gi.alaska.edu/amisr_archive/Processed_data/aurorax/stream2/{year}/{month}/{day}/pfrr_amisr01/'


def scrape_time_bounds(year: str, month: str, day: str):
    url = AMISR_URL.format(year=year, month=month, day=day)
    resp = requests.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    hours = [int(m.group(1)) for link in soup.find_all('a')
             if (m := re.match(r'^ut(\d{2})/$', link.get('href', '')))]
    return min(hours), max(hours) + 1


def process_date(date_str: str):
    year, month, day = date_str[:4], date_str[4:6], date_str[6:]
    try:
        first_h, last_h = scrape_time_bounds(year, month, day)
    except Exception as e:
        print(f"Skipping {date_str}: can't scrape AMISR ({e})")
        return

    # Find files by date
    keo_file = next(KEOGRAM_DIR.glob(f"*{date_str}*keo*.png"), None)
    goes_file = next(GOES_DIR.glob(f"*d{date_str}_v*.nc"), None)
    dscovr_file  = next(DSCOVR_DIR.glob(f"*dscovr_s{year}{month}{day}*pub.nc.gz"), None)

    if not all([keo_file, goes_file, dscovr_file]):
        print(f"Missing data for {date_str}: ")
        print(f"  keogram: {keo_file}, GOES: {goes_file}, DSCOVR: {dscovr_file}")
        return

    # Load keogram image
    keo_img = Image.open(keo_file)
    keo_arr = np.array(keo_img)

    # GOES data
    ds_goes = Dataset(goes_file, 'r')
    goes_time = num2date(ds_goes.variables['OB_time'][:], ds_goes.variables['OB_time'].units)
    goes_hours = np.array([
        (np.datetime64(t) - np.datetime64(f'{year}-{month}-{day}T00:00'))
        .astype('timedelta64[s]').astype(float) / 3600 for t in goes_time
    ])
    he = ds_goes.variables['OB_mag_EPN'][:, 1]
    mask = (goes_hours >= first_h) & (goes_hours <= last_h)
    goes_hours, he = goes_hours[mask], he[mask]

    # DSCOVR data: handle potential EOFError on truncated gzip
    try:
        with gzip.open(dscovr_file, 'rb') as gz, tempfile.NamedTemporaryFile(delete=False, suffix='.nc') as tmp:
            shutil.copyfileobj(gz, tmp)
            tmp_path = tmp.name
    except (OSError, EOFError) as e:
        print(f"Error reading DSCOVR file for {date_str}: {e}")
        return

    ds_dscovr = Dataset(tmp_path, 'r')
    dscovr_time = num2date(
        ds_dscovr.variables['time'][:], ds_dscovr.variables['time'].units,
        only_use_cftime_datetimes=False
    )
    bz = ds_dscovr.variables['bz_gse'][:]
    ds_dscovr.close()
    os.remove(tmp_path)

    # Prepare DataFrame for DSCOVR Bz
    dscovr = (
        pd.DataFrame({'time': dscovr_time, 'bz': bz})
        .dropna()
        .assign(time=lambda df: pd.to_datetime(df['time']))
        .set_index('time')
        .resample('1min')
        .mean()
    )
    dscovr['hour'] = (dscovr.index - pd.Timestamp(f'{year}-{month}-{day}')).total_seconds() / 3600
    dscovr = dscovr[(dscovr['hour'] >= first_h) & (dscovr['hour'] <= last_h)]

    # Plot
    fig, ax1 = plt.subplots(figsize=(12, 6))
    ax1.imshow(keo_arr, aspect='auto', extent=[0, 24, 0, 1])
    ax1.set_xlim(first_h, last_h)
    ax1.set_ylim(0, 1)
    ax1.tick_params(left=False, labelleft=False)
    ax1.spines['left'].set_visible(False)
    ax1.set_xticks(np.arange(first_h, last_h + 1))
    ax1.set_xlabel('Time (Hours UTC)')

    # GOES Hp (orange)
    ax2 = ax1.twinx()
    ax2.plot(goes_hours, he, label='GOES Hp', color='orange')
    ax2.set_ylabel('Hp (nT)', color='orange')
    ax2.tick_params(axis='y', labelcolor='orange')
    ax2.set_xlim(first_h, last_h)
    go_min, go_max = np.nanmin(he), np.nanmax(he)
    ax2.set_ylim(min(0, go_min), max(130, go_max))

    # DSCOVR Bz (dark blue)
    ax3 = ax1.twinx()
    ax3.spines['right'].set_position(('outward', 60))
    ax3.plot(dscovr['hour'], dscovr['bz'], linewidth=1.5, label='DSCOVR Bz', color='darkblue')
    ax3.set_ylabel('Bz GSE (nT)', color='darkblue')
    ax3.tick_params(axis='y', labelcolor='darkblue')
    ax3.set_xlim(first_h, last_h)
    bz_min, bz_max = dscovr['bz'].min(), dscovr['bz'].max()
    ax3.set_ylim(min(-15, bz_min), max(15, bz_max))
    ax3.axhline(0, linestyle='--', linewidth=1, alpha=0.7, color='darkblue')

    plt.title(f'GOES-18 Hp and DSCOVR Bz over Keogram: {date_str}')
    plt.tight_layout()

    out_file = OUTPUT_DIR / f"{date_str}_overlaid_plot.png"
    plt.savefig(out_file, dpi=300)
    plt.close()
    print(f"Saved: {out_file}")


if __name__ == '__main__':
    # Process all dates based on keogram filenames
    pattern = re.compile(r'(\d{8})')
    dates = {pattern.search(p.name).group(1) for p in KEOGRAM_DIR.glob('*png') if pattern.search(p.name)}
    for date in sorted(dates):
        process_date(date)
