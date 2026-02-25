#!/usr/bin/env python3
"""
Root-level CLI to run the Basil X daily post pipeline.

Usage:
  python run_daily.py

Reads BASIL_RSS_FEEDS (comma-separated) or falls back to BBC Politics and
Sky Politics. Prints one 9am Daily Brief and writes it to
out/daily_brief_YYYY-MM-DD.md. No X API.
"""

import sys
from datetime import date
from pathlib import Path

# Allow running from repo root when basil_x is a package
try:
    from basil_x.daily_posts import run_daily_one
except ImportError:
    sys.path.insert(0, ".")
    from basil_x.daily_posts import run_daily_one


def main() -> None:
    brief = run_daily_one()
    today = date.today().isoformat()
    print("--- Daily Brief ---")
    print(brief)

    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"daily_brief_{today}.md"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(f"# Daily Brief {today}\n\n")
        f.write(brief)
        f.write("\n")
    print(f"\nWrote {out_file}")


if __name__ == "__main__":
    main()
