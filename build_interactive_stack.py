#!/usr/bin/env python3
"""
Run interactive stack pipeline in order:
  build_keogram_meta.py -> build_video_meta.py -> stack_keograms.py -> build_stack_html.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def prompt_year_month() -> str:
    prompt = "Enter target month as YYYYMM (e.g., 202512): "
    while True:
        raw = input(prompt).strip()
        m = re.fullmatch(r"(\d{4})(\d{2})", raw)
        if not m:
            print("  Please enter 6 digits in the form YYYYMM.", file=sys.stderr)
            continue

        month = int(m.group(2))
        if 1 <= month <= 12:
            return raw

        print("  Month must be between 01 and 12.", file=sys.stderr)


def run_script(script_path: Path, args: list[str] | None = None, input_text: str | None = None) -> None:
    cmd = [sys.executable, str(script_path)]
    if args:
        cmd.extend(args)
    result = subprocess.run(cmd, input=input_text, text=True)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    ym = prompt_year_month()
    scripts_dir = Path(__file__).resolve().parent

    run_script(scripts_dir / "build_keogram_meta.py", args=["--month", ym])
    run_script(scripts_dir / "build_video_meta.py", args=["--month", ym])
    run_script(scripts_dir / "stack_keograms.py", args=["--month", ym])
    run_script(scripts_dir / "build_keogram_html.py", args=["--month", ym])


if __name__ == "__main__":
    main()
