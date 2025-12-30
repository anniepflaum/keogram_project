#!/usr/bin/env python3
"""
Build an interactive keogram HTML page for a given month (YYYYMM).
The output mirrors existing interactive_stacks keogram pages by embedding
the month-specific keogram/video metadata JSON directly into the HTML.
"""

import argparse
import calendar
import json
import re
import sys
from pathlib import Path
from string import Template


BASE_DIR = Path.home() / "Documents" / "keogram_project"
INTERACTIVE_ROOT = BASE_DIR / "interactive_stacks"


HTML_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>PKR DASC stacked keogram $year-$month</title>
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
    #container {
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
    #info {
      margin-top: 0.75rem;
      font-size: 0.9rem;
      color: #ccc;
      white-space: pre-line;
    }
    #info strong {
      color: #fff;
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
        transform: translate(8px, 8px); /* small offset from cursor */
        transition: opacity 0.05s linear;
        z-index: 20;
    }

  </style>
</head>
<body>
  <h1>PKR stacked keogram – $month_name $year</h1>
  <p>Click anywhere on the stacked keogram to open the corresponding all-sky movie ~15 minutes before that time (UT).</p>

  <div id="keogram-container" style="position: relative; display: inline-block;">
    <img
      id="keogram"
      src="$png_name"
      alt="PKR DASC stacked keogram"
    >
    <canvas
      id="keogram-overlay"
      style="position: absolute; top: 0; left: 0; pointer-events: none;"
    ></canvas>
  </div>

  <div id="hover-tooltip"></div>

  <div id="info">
    Click on the image to select a day and time.
  </div>

  <video id="player" style="display:none;"></video>

  <script>
    const YEAR = $year;
    const MONTH = $month_int;

    // JSON files should be in the same folder as this HTML.
    const keogramMeta = $keogram_meta_json

    const videoMeta = $video_meta_json

    const videoBase = "https://optics.gi.alaska.edu/realtime/data/MPEG/PKR_DASC_512/";

    const img       = document.getElementById("keogram");
    const overlay   = document.getElementById("keogram-overlay");
    const container = document.getElementById("keogram-container");
    const infoBox   = document.getElementById("info");
    const tooltip   = document.getElementById("hover-tooltip");

    function init() {
      const img       = document.getElementById("keogram");
      const infoBox   = document.getElementById("info");
      const tooltip   = document.getElementById("hover-tooltip");
      const player    = document.getElementById("player");

      if (!img || !infoBox || !tooltip || !player) {
        console.error("Missing DOM elements needed for init().");
        return;
      }

      // --- Build lookup of videos by ymd ---
      const videoByYmd = {};
      if (videoMeta && Array.isArray(videoMeta.videos)) {
        for (const v of videoMeta.videos) {
          const key = String(v.ymd);
          videoByYmd[key] = v;
        }
      }
      console.log("Video entries by day:", Object.keys(videoByYmd));

      // --- Global hour range from meta ---
      const rawH0 = keogramMeta.global_h0 ?? keogramMeta.global_min_hour ?? 0;
      const rawH1 = keogramMeta.global_h1 ?? keogramMeta.global_max_hour ?? 24;

      let globalH0 = Number(rawH0);
      let globalH1 = Number(rawH1);

      console.log(
        "Global hour range from meta (raw):",
        rawH0,
        rawH1,
        " -> parsed:",
        globalH0,
        globalH1
      );

      if (!Number.isFinite(globalH0) || !Number.isFinite(globalH1) || globalH0 >= globalH1) {
        console.warn("Global hour values invalid; defaulting to 0–24.");
        globalH0 = 0;
        globalH1 = 24;
      }

      const days    = keogramMeta.days;
      const numRows = days.length;

      // ---------------- CLICK HANDLER ----------------
      function handleClick(ev) {
        const rect   = img.getBoundingClientRect();
        const clickX = ev.clientX - rect.left;
        const clickY = ev.clientY - rect.top;

        const relX = clickX / rect.width;   // 0..1 time
        const relY = clickY / rect.height;  // 0..1 stacked days

        // Map Y to row index (nearest row center)
        let rowFloat = relY * numRows;
        let rowIndex = Math.round(rowFloat - 0.5);
        if (rowIndex < 0) rowIndex = 0;
        if (rowIndex >= numRows) rowIndex = numRows - 1;

        const dayEntry = days[rowIndex];
        const ymd      = String(dayEntry.ymd);

        console.log("Click mapping:", {
          clickX: clickX.toFixed(1),
          clickY: clickY.toFixed(1),
          relX: relX.toFixed(3),
          relY: relY.toFixed(3),
          rowFloat: rowFloat.toFixed(3),
          rowIndex,
          ymd
        });

        if (!/^\\d{8}$$/.test(ymd)) {
          console.error("Bad ymd format:", ymd);
          alert("Internal error: bad date code " + ymd);
          return;
        }

        // X → UT using globalH0/globalH1
        const fracHour      = globalH0 + relX * (globalH1 - globalH0);
        const hourInt       = Math.floor(fracHour);
        const minutesFloat  = (fracHour - hourInt) * 60;
        const minuteInt     = Math.floor(minutesFloat);
        const secondsFloat  = (minutesFloat - minuteInt) * 60;
        let   secondInt     = Math.round(secondsFloat);

        let h = hourInt;
        let m = minuteInt;
        let s = secondInt;

        if (s >= 60) {
          s -= 60;
          m += 1;
        }
        if (m >= 60) {
          m -= 60;
          h += 1;
        }

        const year     = parseInt(ymd.slice(0, 4), 10);
        const monthIdx = parseInt(ymd.slice(4, 6), 10) - 1; // JS months 0–11
        const day      = parseInt(ymd.slice(6, 8), 10);

        const clickMs = Date.UTC(year, monthIdx, day, h, m, s);
        if (!Number.isFinite(clickMs)) {
          console.error("Invalid clickMs from date pieces:", { year, monthIdx, day, h, m, s });
          alert("Internal error computing click time – see console.");
          return;
        }

        // --- Video metadata for that day ---
        const vid = videoByYmd[ymd];
        if (!vid) {
          console.warn("No video entry for day", ymd);
          alert("No video available for this day (" + ymd + ").");
          return;
        }

        const videoStartMs = Date.parse(vid.start_utc);
        const videoEndMs   = Date.parse(vid.end_utc);
        if (!Number.isFinite(videoStartMs) || !Number.isFinite(videoEndMs) || videoEndMs <= videoStartMs) {
          console.error("Bad video timestamps for", ymd, vid);
          alert("Video metadata for " + ymd + " is incomplete.");
          return;
        }

        const realSpanMs = videoEndMs - videoStartMs;

        // 30-minute UT window centered on click
        const HALF_WINDOW_MIN = 15;
        let windowStartMs = clickMs - HALF_WINDOW_MIN * 60 * 1000;
        let windowEndMs   = clickMs + HALF_WINDOW_MIN * 60 * 1000;

        // Clamp to video coverage
        if (windowStartMs < videoStartMs) windowStartMs = videoStartMs;
        if (windowEndMs > videoEndMs)     windowEndMs   = videoEndMs;
        if (windowEndMs < windowStartMs)  windowEndMs   = windowStartMs;

        // Normalize positions within full video span
        const startRawPos  = (windowStartMs - videoStartMs) / realSpanMs;
        const endRawPos    = (windowEndMs   - videoStartMs) / realSpanMs;
        const startNormPos = Math.min(1, Math.max(0, startRawPos));
        const endNormPos   = Math.min(1, Math.max(0, endRawPos));

        const clickIso    = new Date(clickMs).toISOString();
        const winStartIso = new Date(windowStartMs).toISOString();
        const winEndIso   = new Date(windowEndMs).toISOString();
        const startIso    = new Date(videoStartMs).toISOString();
        const endIso      = new Date(videoEndMs).toISOString();

        console.log("Time mapping (window):", {
          ymd,
          clickIso,
          winStartIso,
          winEndIso,
          startIso,
          endIso,
          realSpanSec: realSpanMs / 1000,
          startNormPos,
          endNormPos
        });

        // Display clicked UT in info box
        const clickDate = new Date(clickMs);
        const HH = clickDate.getUTCHours().toString().padStart(2, "0");
        const MM = clickDate.getUTCMinutes().toString().padStart(2, "0");
        const SS = clickDate.getUTCSeconds().toString().padStart(2, "0");

        infoBox.textContent =
          "Day " + ymd +
          " | clicked UT ≈ " + HH + ":" + MM + ":" + SS +
          "\\nVideo: " + vid.video_file +
          "\\nWindow ≈ [ " + winStartIso + "  →  " + winEndIso + " ] (UT, ~" +
          (2 * HALF_WINDOW_MIN) + " min span).";

        const videoUrl = videoBase + vid.video_file;

        // Use hidden <video> to read full duration, then open new tab with #t=start,end
        player.pause();
        player.removeAttribute("src");
        player.load();

        player.src = videoUrl;
        player.load();

        player.onloadedmetadata = () => {
          const duration = player.duration;

          let startSec = 0;
          let endSec   = duration;

          if (Number.isFinite(duration) && duration > 0) {
            startSec = startNormPos * duration;
            endSec   = endNormPos   * duration;

            // Ensure at least a tiny span
            if (endSec < startSec + 0.5) {
              endSec = Math.min(duration, startSec + 0.5);
            }
          }

          console.log("Video metadata loaded for #t fragment:", {
            duration,
            startNormPos,
            endNormPos,
            startSec,
            endSec
          });

          const openUrl =
            videoUrl +
            "#t=" +
            startSec.toFixed(1) + "," +
            endSec.toFixed(1);

          window.open(openUrl, "_blank");
        };
      }

      // ---------------- HOVER HANDLER ----------------
      function handleMouseMove(ev) {
        const rect = img.getBoundingClientRect();
        const x    = ev.clientX - rect.left;
        const y    = ev.clientY - rect.top;

        const relX = x / rect.width;
        const relY = y / rect.height;

        // Map Y to row index (same as click)
        let rowFloat = relY * numRows;
        let rowIndex = Math.round(rowFloat - 0.5);
        if (rowIndex < 0) rowIndex = 0;
        if (rowIndex >= numRows) rowIndex = numRows - 1;

        const dayEntry = days[rowIndex];
        const ymd      = String(dayEntry.ymd);
        if (!/^\\d{8}$$/.test(ymd)) {
          return;
        }

        // X → UT using global hour range
        const fracHour      = globalH0 + relX * (globalH1 - globalH0);
        const hourInt       = Math.floor(fracHour);
        const minutesFloat  = (fracHour - hourInt) * 60;
        const minuteInt     = Math.floor(minutesFloat);
        const secondsFloat  = (minutesFloat - minuteInt) * 60;
        let   secondInt     = Math.round(secondsFloat);

        let h = hourInt;
        let m = minuteInt;
        let s = secondInt;

        if (s >= 60) {
          s -= 60;
          m += 1;
        }
        if (m >= 60) {
          m -= 60;
          h += 1;
        }

        const year     = parseInt(ymd.slice(0, 4), 10);
        const monthIdx = parseInt(ymd.slice(4, 6), 10) - 1;
        const day      = parseInt(ymd.slice(6, 8), 10);

        const hoverMs   = Date.UTC(year, monthIdx, day, h, m, s);
        const hoverDate = new Date(hoverMs);

        const Y  = hoverDate.getUTCFullYear();
        const M  = (hoverDate.getUTCMonth() + 1).toString().padStart(2, "0");
        const D  = hoverDate.getUTCDate().toString().padStart(2, "0");
        const HH = hoverDate.getUTCHours().toString().padStart(2, "0");
        const MM = hoverDate.getUTCMinutes().toString().padStart(2, "0");
        const SS = hoverDate.getUTCSeconds().toString().padStart(2, "0");

        const label = `$${Y}-$${M}-$${D} $${HH}:$${MM}:$${SS} UT`;

        tooltip.textContent   = label;
        tooltip.style.left    = `$${ev.pageX}px`;
        tooltip.style.top     = `$${ev.pageY}px`;
        tooltip.style.opacity = "1";
      }

      function initAfterImageLoads() {
        img.addEventListener("click", handleClick);
        img.addEventListener("mousemove", handleMouseMove);
        img.addEventListener("mouseleave", () => {
          tooltip.style.opacity = "0";
        });
      }

      if (img.complete) {
        initAfterImageLoads();
      } else {
        img.addEventListener("load", initAfterImageLoads);
      }

      console.log("Keogram interactive init complete.");
    }
 
    init();

  </script>
</body>
</html>
"""
)


def parse_year_month(raw: str) -> tuple[int, int, str]:
    m = re.fullmatch(r"(\d{4})(\d{2})", raw)
    if not m:
        raise ValueError("Please enter 6 digits in the form YYYYMM.")

    year = int(m.group(1))
    month = int(m.group(2))
    if 1 <= month <= 12:
        return year, month, raw

    raise ValueError("Month must be between 01 and 12.")


def prompt_year_month() -> tuple[int, int, str]:
    """Prompt for YYYYMM and return (year, month, ym)."""
    prompt = "Enter target month as YYYYMM (e.g., 202512): "
    while True:
        raw = input(prompt).strip()
        try:
            return parse_year_month(raw)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)


def load_json(path: Path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Missing required file: {path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Failed to load JSON {path}: {e}", file=sys.stderr)
        sys.exit(1)


def render_html(year: int, month: int, ym: str, keo_meta: dict, video_meta: dict) -> str:
    month_name = calendar.month_name[month]
    png_name = f"stacked_keograms_{ym}.png"

    keo_json = json.dumps(keo_meta, indent=2)
    video_json = json.dumps(video_meta, indent=2)

    return HTML_TEMPLATE.substitute(
        year=year,
        month=f"{month:02d}",
        month_int=month,
        month_name=month_name,
        png_name=png_name,
        keogram_meta_json=keo_json,
        video_meta_json=video_json,
    )


def main():
    parser = argparse.ArgumentParser(description="Build interactive keogram HTML for a month.")
    parser.add_argument("--month", help="Target month in YYYYMM format")
    args = parser.parse_args()

    if args.month:
        try:
            year, month, ym = parse_year_month(args.month)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        year, month, ym = prompt_year_month()

    out_dir = INTERACTIVE_ROOT / ym
    out_dir.mkdir(parents=True, exist_ok=True)

    keo_meta_path = out_dir / f"keogram_meta_{ym}.json"
    video_meta_path = out_dir / f"video_meta_{ym}.json"

    keo_meta = load_json(keo_meta_path)
    video_meta = load_json(video_meta_path)

    html_text = render_html(year, month, ym, keo_meta, video_meta)

    out_html = out_dir / f"keogram_{ym}.html"
    with open(out_html, "w") as f:
        f.write(html_text)

    print(f"Wrote {out_html}")


if __name__ == "__main__":
    main()
