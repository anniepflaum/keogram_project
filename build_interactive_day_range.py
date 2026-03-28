#!/usr/bin/env python3
"""
Build an interactive keogram for a single UTC day and time range.

The output bundle includes:
  - a stitched keogram PNG built from hourly images in the requested interval
  - keogram metadata JSON
  - video metadata JSON for the same UTC day, when available
  - an HTML page that lets you click the stitched keogram to open the
    corresponding segment of the all-sky movie
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from datetime import date, datetime, time
from pathlib import Path
from string import Template

from PIL import Image, ImageDraw

from build_video_meta import BASE_URL as VIDEO_BASE_URL
from build_video_meta import dt_to_iso, get_video_timerange
from stack_keograms import BASE as AMISR_BASE
from stack_keograms import DEFAULT_CAMERA, DEFAULT_STATION, http_get_bytes, http_get_text


HTML_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>PKR DASC keogram $ymd $start_label-$end_label UT</title>
  <style>
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #111;
      color: #eee;
      margin: 0;
      padding: 1rem;
    }
    h1 {
      margin-top: 0;
    }
    #keogram-container {
      position: relative;
      display: inline-block;
      border: 1px solid #444;
    }
    #keogram {
      max-width: 100%;
      height: auto;
      display: block;
      cursor: crosshair;
    }
    #hover-tooltip {
      position: absolute;
      padding: 2px 6px;
      font-size: 12px;
      background: rgba(0, 0, 0, 0.85);
      color: #fff;
      border-radius: 3px;
      pointer-events: none;
      opacity: 0;
      transform: translate(8px, 8px);
      transition: opacity 0.05s linear;
      z-index: 20;
    }
    #info {
      margin-top: 0.75rem;
      font-size: 0.9rem;
      color: #ccc;
      white-space: pre-line;
    }
    #info strong {
      color: #fff;
    }
  </style>
</head>
<body>
  <h1>PKR keogram - $ymd - $start_label to $end_label UT</h1>
  <p>Click anywhere on the keogram to open the corresponding all-sky movie ~15 minutes around that time (UT).</p>

  <div id="keogram-container">
    <img id="keogram" src="$png_name" alt="PKR DASC keogram for $ymd">
    <div id="hover-tooltip"></div>
  </div>

  <div id="info">Click on the image to select a UT time.</div>
  <video id="player" style="display:none;"></video>

  <script>
    const keogramMeta = $keogram_meta_json;
    const videoMeta = $video_meta_json;
    const videoBase = $video_base_json;

    const img = document.getElementById("keogram");
    const tooltip = document.getElementById("hover-tooltip");
    const infoBox = document.getElementById("info");
    const player = document.getElementById("player");

    const dayEntry = keogramMeta.day;
    const ymd = String(dayEntry.ymd);
    const rangeStartHour = Number(keogramMeta.global_min_hour ?? 0);
    const rangeEndHour = Number(keogramMeta.global_max_hour ?? 24);

    function timeFromX(clientX) {
      const rect = img.getBoundingClientRect();
      const relX = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      const totalSeconds = Math.round((rangeStartHour + relX * (rangeEndHour - rangeStartHour)) * 3600);
      const hh = Math.floor(totalSeconds / 3600);
      const mm = Math.floor((totalSeconds % 3600) / 60);
      const ss = totalSeconds % 60;
      return {
        relX,
        hh,
        mm,
        ss,
        label:
          String(hh).padStart(2, "0") + ":" +
          String(mm).padStart(2, "0") + ":" +
          String(ss).padStart(2, "0"),
      };
    }

    function clickTimeMs(parts) {
      const year = parseInt(ymd.slice(0, 4), 10);
      const monthIdx = parseInt(ymd.slice(4, 6), 10) - 1;
      const day = parseInt(ymd.slice(6, 8), 10);
      return Date.UTC(year, monthIdx, day, parts.hh, parts.mm, parts.ss);
    }

    img.addEventListener("mousemove", (ev) => {
      const parts = timeFromX(ev.clientX);
      tooltip.textContent = ymd + " " + parts.label + " UT";
      tooltip.style.left = (ev.clientX - img.getBoundingClientRect().left) + "px";
      tooltip.style.top = (ev.clientY - img.getBoundingClientRect().top) + "px";
      tooltip.style.opacity = "1";
    });

    img.addEventListener("mouseleave", () => {
      tooltip.style.opacity = "0";
    });

    img.addEventListener("click", (ev) => {
      const parts = timeFromX(ev.clientX);
      const clickMs = clickTimeMs(parts);

      if (!videoMeta || !videoMeta.video_file || !videoMeta.start_utc || !videoMeta.end_utc) {
        infoBox.textContent = "Selected UT: " + ymd + " " + parts.label + "\\nNo video metadata is available for this day.";
        return;
      }

      const videoStartMs = Date.parse(videoMeta.start_utc);
      const videoEndMs = Date.parse(videoMeta.end_utc);
      if (!Number.isFinite(videoStartMs) || !Number.isFinite(videoEndMs) || videoEndMs <= videoStartMs) {
        infoBox.textContent = "Selected UT: " + ymd + " " + parts.label + "\\nVideo metadata is incomplete.";
        return;
      }

      const HALF_WINDOW_MIN = 15;
      let windowStartMs = clickMs - HALF_WINDOW_MIN * 60 * 1000;
      let windowEndMs = clickMs + HALF_WINDOW_MIN * 60 * 1000;

      if (windowStartMs < videoStartMs) windowStartMs = videoStartMs;
      if (windowEndMs > videoEndMs) windowEndMs = videoEndMs;
      if (windowEndMs < windowStartMs) windowEndMs = windowStartMs;

      const spanMs = videoEndMs - videoStartMs;
      const startNormPos = Math.min(1, Math.max(0, (windowStartMs - videoStartMs) / spanMs));
      const endNormPos = Math.min(1, Math.max(0, (windowEndMs - videoStartMs) / spanMs));
      const videoUrl = videoBase + videoMeta.video_file;

      infoBox.textContent =
        "Selected UT: " + ymd + " " + parts.label +
        "\\nVideo: " + videoMeta.video_file +
        "\\nWindow: " + new Date(windowStartMs).toISOString() +
        " -> " + new Date(windowEndMs).toISOString();

      player.pause();
      player.removeAttribute("src");
      player.load();
      player.src = videoUrl;
      player.load();

      player.onloadedmetadata = () => {
        const duration = player.duration;
        let startSec = 0;
        let endSec = duration;

        if (Number.isFinite(duration) && duration > 0) {
          startSec = startNormPos * duration;
          endSec = endNormPos * duration;
          if (endSec < startSec + 0.5) {
            endSec = Math.min(duration, startSec + 0.5);
          }
        }

        window.open(videoUrl + "#t=" + startSec.toFixed(3) + "," + endSec.toFixed(3), "_blank", "noopener");
      };

      player.onerror = () => {
        infoBox.textContent += "\\nCould not load video metadata in-browser.";
      };
    });
  </script>
</body>
</html>
"""
)


def parse_ymd(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("Please enter 8 digits in the form YYYYMMDD.") from exc


def parse_ut_time(raw: str) -> time:
    raw = raw.strip()
    if not re.fullmatch(r"\d{2}", raw):
        raise ValueError("Time must be HH in UT.")

    hour = int(raw)
    if not 0 <= hour <= 23:
        raise ValueError("Hour must be between 00 and 23.")

    return time(hour=hour)


def prompt_value(prompt: str, parser):
    while True:
        raw = input(prompt).strip()
        try:
            return parser(raw)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)


def time_to_hour_value(value: time) -> float:
    return value.hour + value.minute / 60.0 + value.second / 3600.0


def time_label(value: time) -> str:
    return value.strftime("%H%M%S")


def time_display(value: time) -> str:
    return value.strftime("%H:%M:%S")


def amisr_day_url(target_date: date, station: str) -> str:
    return (
        f"{AMISR_BASE}/"
        f"{target_date.year:04d}/{target_date.month:02d}/{target_date.day:02d}/{station}/"
    )


def pick_hour_filename(target_date: date, hour: int, station: str, camera: str) -> str | None:
    ymd = target_date.strftime("%Y%m%d")
    site = station.split("_", 1)[0]
    hour_url = f"{amisr_day_url(target_date, station)}ut{hour:02d}/"
    html = http_get_text(hour_url)

    exact = f"{ymd}_{hour:02d}_{site}_{camera}_rgb-keogram.png"
    if exact in html:
        return exact

    match = re.search(
        rf"{ymd}_(?:{hour:02d}|{hour})_{re.escape(site)}_{re.escape(camera)}_rgb-keogram\.png",
        html,
        flags=re.IGNORECASE,
    )
    return match.group(0) if match else None


def fetch_hour_keogram(target_date: date, hour: int, station: str, camera: str) -> Image.Image | None:
    try:
        filename = pick_hour_filename(target_date, hour, station, camera)
    except Exception:
        return None

    if not filename:
        return None

    hour_url = f"{amisr_day_url(target_date, station)}ut{hour:02d}/{filename}"
    try:
        data = http_get_bytes(hour_url)
    except Exception:
        return None

    return Image.open(io.BytesIO(data)).convert("RGB")


def stitch_hourly_keograms(
    target_date: date,
    start_time: time,
    end_time: time,
    station: str,
    camera: str,
    draw_hours: tuple[int, ...],
) -> Image.Image:
    start_hour = time_to_hour_value(start_time)
    end_hour = time_to_hour_value(end_time)
    hour_values = list(range(int(start_hour), int(end_hour)))

    tiles: list[Image.Image] = []
    missing_hours: list[int] = []
    for hour in hour_values:
        img = fetch_hour_keogram(target_date, hour, station, camera)
        if img is None:
            missing_hours.append(hour)
            continue
        tiles.append(img)

    if missing_hours:
        missing_str = ", ".join(f"{hour:02d}" for hour in missing_hours)
        raise RuntimeError(f"Missing hourly keogram image(s) for UT hour(s): {missing_str}.")
    if not tiles:
        raise RuntimeError("No hourly keogram images were found for the requested range.")

    side = min(min(img.width, img.height) for img in tiles)
    square_tiles = [img.resize((side, side), Image.LANCZOS) for img in tiles]

    stitched = Image.new("RGB", (side * len(square_tiles), side), (0, 0, 0))
    for idx, tile in enumerate(square_tiles):
        stitched.paste(tile, (idx * side, 0))

    draw = ImageDraw.Draw(stitched)
    span = max(end_hour - start_hour, 1e-6)
    for hour in draw_hours:
        if start_hour <= hour <= end_hour:
            rel = (hour - start_hour) / span
            x = int(stitched.width * rel)
            draw.line((x, 0, x, stitched.height), fill="white", width=4)

    return stitched


def build_video_meta_for_day(target_date: date) -> dict[str, str | None]:
    ymd = target_date.strftime("%Y%m%d")
    video_name = f"PKR_DASC_{ymd}_rgb_512.mp4"
    video_url = VIDEO_BASE_URL + video_name

    try:
        start_dt, end_dt = get_video_timerange(video_name, video_url)
    except Exception as exc:
        print(f"[warn] Could not build video metadata for {ymd}: {exc}", file=sys.stderr)
        return {"ymd": ymd, "video_file": None, "start_utc": None, "end_utc": None}

    return {
        "ymd": ymd,
        "video_file": video_name if start_dt and end_dt else None,
        "start_utc": dt_to_iso(start_dt),
        "end_utc": dt_to_iso(end_dt),
    }


def write_html(out_path: Path, png_name: str, keogram_meta: dict, video_meta: dict, target_date: date, start_time: time, end_time: time) -> None:
    html = HTML_TEMPLATE.substitute(
        ymd=target_date.strftime("%Y%m%d"),
        start_label=time_display(start_time),
        end_label=time_display(end_time),
        png_name=png_name,
        keogram_meta_json=json.dumps(keogram_meta, indent=2),
        video_meta_json=json.dumps(video_meta, indent=2),
        video_base_json=json.dumps(VIDEO_BASE_URL),
    )
    out_path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an interactive keogram for a UTC time range within one day.")
    parser.add_argument("--date", help="Target UTC day in YYYYMMDD format")
    parser.add_argument("--start", help="Range start hour in UT (HH)")
    parser.add_argument("--end", help="Range end hour in UT (HH)")
    parser.add_argument("--station", default=DEFAULT_STATION, help="Station folder (default: pfrr_amisr01)")
    parser.add_argument("--camera", default=DEFAULT_CAMERA, help="Camera code in filename (default: asi3)")
    parser.add_argument(
        "--out",
        help="Project root containing interactive_stacks (default: ~/keogram_project)",
    )
    parser.add_argument("--hours", default="6,12", help="Comma-separated UTC hours to draw reference lines")
    args = parser.parse_args()

    if args.date:
        try:
            target_date = parse_ymd(args.date)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        target_date = prompt_value("Enter target day as YYYYMMDD: ", parse_ymd)

    if args.start:
        try:
            start_time = parse_ut_time(args.start)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        start_time = prompt_value("Enter start UT hour as HH: ", parse_ut_time)

    if args.end:
        try:
            end_time = parse_ut_time(args.end)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        end_time = prompt_value("Enter end UT hour as HH: ", parse_ut_time)

    if time_to_hour_value(end_time) <= time_to_hour_value(start_time):
        print("  End time must be later than start time within the same UTC day.", file=sys.stderr)
        sys.exit(1)

    hours_tuple = tuple(int(h) for h in args.hours.split(",") if re.fullmatch(r"\d{1,2}", h.strip())) or (6, 12)

    project_root = Path(args.out).expanduser() if args.out else Path.home() / "keogram_project"
    interactive_root = project_root / "interactive_stacks"

    ymd = target_date.strftime("%Y%m%d")
    range_tag = f"{ymd}_{args.start}-{args.end}"
    out_dir = interactive_root / range_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    png_name = f"keogram_{range_tag}.png"
    png_path = out_dir / png_name
    html_path = out_dir / f"interactive_keogram_{range_tag}.html"
    keogram_meta_path = out_dir / f"keogram_meta_{range_tag}.json"
    video_meta_path = out_dir / f"video_meta_{range_tag}.json"

    try:
        stitched = stitch_hourly_keograms(
            target_date=target_date,
            start_time=start_time,
            end_time=end_time,
            station=args.station,
            camera=args.camera,
            draw_hours=hours_tuple,
        )
    except Exception as exc:
        print(f"  {exc}", file=sys.stderr)
        sys.exit(1)
    stitched.save(png_path)

    start_hour = time_to_hour_value(start_time)
    end_hour = time_to_hour_value(end_time)
    keogram_meta = {
        "date": ymd,
        "day": {
            "ymd": ymd,
            "h0": start_hour,
            "h1": end_hour,
        },
        "global_min_hour": start_hour,
        "global_max_hour": end_hour,
    }

    video_meta = build_video_meta_for_day(target_date)

    keogram_meta_path.write_text(json.dumps(keogram_meta, indent=2), encoding="utf-8")
    video_meta_path.write_text(json.dumps(video_meta, indent=2), encoding="utf-8")
    write_html(html_path, png_name, keogram_meta, video_meta, target_date, start_time, end_time)

    print(f"[saved] {png_path}")
    print(f"[saved] {keogram_meta_path}")
    print(f"[saved] {video_meta_path}")
    print(f"[saved] {html_path}")


if __name__ == "__main__":
    main()
