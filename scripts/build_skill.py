#!/usr/bin/env python3
"""Build the portable .skill file and sync the general-use agent skill."""

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "3dsmax-mcp-dev"
SKILL_SRC = SKILL_DIR / "SKILL.md"
PROCEDURAL_GRAPHS_REF = SKILL_DIR / "procedural-graphs.md"
SKILL_OUT = ROOT / "3dsmax-mcp-dev.skill"
LOCAL_AGENTS_DIR = ROOT / ".agents" / "skills" / "3dsmax-mcp-dev"
GLOBAL_SKILLS_DIR = Path.home() / ".claude" / "skills" / "3dsmax-mcp-dev"
GLOBAL_AGENTS_DIR = Path.home() / ".agents" / "skills" / "3dsmax-mcp-dev"
def collect_skill_files():
    """Collect the core skill and its bundled reference files."""
    files = [SKILL_SRC, PROCEDURAL_GRAPHS_REF]
    for ref in ("tyflow-graphs.md", "curve-construction.md"):
        path = SKILL_DIR / ref
        if path.exists():
            files.append(path)
    for md in sorted(SKILL_DIR.glob("maxscript-*.md")):
        files.append(md)
    return files


def build(target="both"):
    if not SKILL_SRC.exists():
        print(f"ERROR: source not found: {SKILL_SRC}")
        raise SystemExit(1)

    skill_files = collect_skill_files()

    # 1. Build .skill ZIP archive
    with zipfile.ZipFile(SKILL_OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.mkdir("./")
        for f in skill_files:
            zf.write(f, f"./{f.name}")
    print(f"  Built {SKILL_OUT.name} ({len(skill_files)} files)")

    # 2. Select install targets
    local_dests = [
        (".agents/skills", LOCAL_AGENTS_DIR),
    ]
    global_dests = [
        ("~/.claude/skills", GLOBAL_SKILLS_DIR),
        ("~/.agents/skills", GLOBAL_AGENTS_DIR),
    ]

    if target == "local":
        dests = local_dests
    elif target == "global":
        dests = global_dests
    else:
        dests = local_dests + global_dests

    for label, dest in dests:
        # Clean stale symlinks/junctions from older installs (pre-0.5)
        if dest.is_symlink() or dest.is_junction():
            print(f"  Replacing old symlink: {dest}")
            dest.unlink()
        dest.mkdir(parents=True, exist_ok=True)
        try:
            for f in skill_files:
                shutil.copy2(f, dest / f.name)
            print(f"  Copied to {label}/")
        except PermissionError:
            print(f"  WARN: {label} locked, skipped")

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build and install 3dsmax-mcp-dev skill")
    parser.add_argument(
        "--target",
        choices=["local", "global", "both"],
        default="both",
        help="Where to install: 'local' (project only), 'global' (~/ only), 'both' (default)",
    )
    args = parser.parse_args()
    build(target=args.target)
