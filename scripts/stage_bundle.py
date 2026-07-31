#!/usr/bin/env python3
"""Stage the 3dsmax-mcp ApplicationPlugins bundle from repo sources.

Run:  uv run python scripts/stage_bundle.py
      uv run python scripts/stage_bundle.py --dest bundle
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import install  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage 3dsmax-mcp application package")
    parser.add_argument(
        "--dest",
        default=str(ROOT / "bundle"),
        help="Bundle root directory (default: repo bundle/)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dest = Path(args.dest)
    included, missing = install.stage_bundle(dest)
    print(f"Staged bundle -> {dest}")
    if included:
        print(f"  GUPs: {', '.join(str(y) for y in included)}")
    if missing:
        print(f"  Missing: {', '.join(str(y) for y in missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
