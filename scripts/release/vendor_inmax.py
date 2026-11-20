"""Fetch the packages the tool layer needs inside a 3ds Max whose Python is not the server's.

The MCP server runs Python 3.13, which 3ds Max 2027 embeds too, so there the tool layer
loads the server's own packages. Earlier releases embed earlier Pythons, and pydantic and
Pillow are compiled per Python:

    3ds Max 2023         Python 3.9    maxmcp/inmax/site/cp39
    3ds Max 2024         Python 3.10   maxmcp/inmax/site/cp310
    3ds Max 2025, 2026   Python 3.11   maxmcp/inmax/site/cp311

Each folder is installed from the hash-pinned list beside it (cp39.txt and so on). The
lists are tracked; the folders are not, and a wheel carries them (`artifacts` in
pyproject.toml). run_python's launcher puts the folder for Max's Python on its path.

Run:  uv run python scripts/release/vendor_inmax.py           install what is missing or out of date
      uv run python scripts/release/vendor_inmax.py --lock    resolve the lists again, then install
      uv run python scripts/release/vendor_inmax.py --check   exit 1 if a folder is missing or out of date
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "maxmcp" / "inmax" / "site"
PLATFORM = "x86_64-pc-windows-msvc"
# The Python each 3ds Max embeds, where it is not the server's (3.13, as in 3ds Max 2027).
MAX_PYTHONS = {"cp39": "3.9", "cp310": "3.10", "cp311": "3.11"}
STAMP = ".installed-from"


def _uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is not on PATH; install it from https://docs.astral.sh/uv/")
    return uv


def _server_pydantic() -> str:
    """The pydantic the server is locked to: every 3ds Max validates tool arguments with the same one."""
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(r'^name = "pydantic"\nversion = "([^"]+)"', lock, re.MULTILINE)
    if not match:
        raise RuntimeError("uv.lock names no pydantic version")
    return match.group(1)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_current(tag: str) -> bool:
    """Whether the folder for one Python was installed from the list as it is now."""
    stamp = SITE / tag / STAMP
    listing = SITE / f"{tag}.txt"
    return listing.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == _digest(listing)


def missing() -> list[str]:
    """The Pythons whose folder is absent or out of date."""
    return [tag for tag in MAX_PYTHONS if not is_current(tag)]


def lock(tag: str) -> None:
    """Resolve one Python's list again, with hashes."""
    requirements = (
        f"pydantic=={_server_pydantic()}\n"
        "pillow>=10.0\n"
        # pydantic reads `X | None` annotations with it before Python 3.10.
        'eval-type-backport>=0.2 ; python_version < "3.10"\n'
    )
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "requirements.in"
        source.write_text(requirements, encoding="utf-8")
        subprocess.run(
            [_uv(), "pip", "compile", str(source), "--python-version", MAX_PYTHONS[tag], "--python-platform", PLATFORM,
             "--only-binary", ":all:", "--generate-hashes", "--no-header", "--no-annotate", "--quiet",
             "--output-file", str(SITE / f"{tag}.txt")],
            cwd=ROOT, check=True)


def install(tag: str) -> None:
    """Replace one Python's folder with what its list pins."""
    listing = SITE / f"{tag}.txt"
    target = SITE / tag
    if target.exists():
        if target.is_symlink() or target.parent != SITE:
            raise RuntimeError(f"Refusing to replace {target}")
        shutil.rmtree(target)
    subprocess.run(
        [_uv(), "pip", "install", "--target", str(target), "--python-version", MAX_PYTHONS[tag],
         "--python-platform", PLATFORM, "--only-binary", ":all:", "--require-hashes", "--no-deps", "--quiet",
         "-r", str(listing)],
        cwd=ROOT, check=True)
    (target / STAMP).write_text(_digest(listing) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", action="store_true", help="resolve the pinned lists again before installing")
    parser.add_argument("--check", action="store_true", help="only report; exit 1 if anything is missing")
    parser.add_argument("--only", nargs="+", choices=sorted(MAX_PYTHONS), metavar="TAG",
                        help="install only these Pythons (cp39 cp310 cp311); default all")
    args = parser.parse_args()
    if args.check:
        stale = missing()
        for tag in stale:
            print(f"missing or out of date: {SITE / tag}")
        return 1 if stale else 0
    if args.lock:
        for tag in MAX_PYTHONS:
            lock(tag)
    for tag in args.only or MAX_PYTHONS:
        if is_current(tag):
            print(f"{tag}: up to date")
            continue
        install(tag)
        print(f"{tag}: installed Python {MAX_PYTHONS[tag]} packages into {SITE / tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
