#!/usr/bin/env python3
"""Build the portable .skill file and sync to .claude/skills/ for local discovery."""

import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_SRC = ROOT / "skills" / "3dsmax-mcp-dev" / "SKILL.md"
SKILL_OUT = ROOT / "3dsmax-mcp-dev.skill"
CLAUDE_SKILLS_DIR = ROOT / ".claude" / "skills" / "3dsmax-mcp-dev"


def build():
    if not SKILL_SRC.exists():
        print(f"ERROR: source not found: {SKILL_SRC}")
        raise SystemExit(1)

    # 1. Build .skill ZIP archive (matches existing format: ./ + ./SKILL.md)
    with zipfile.ZipFile(SKILL_OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.mkdir("./")
        zf.write(SKILL_SRC, "./SKILL.md")
    print(f"Built {SKILL_OUT}")

    # 2. Copy to .claude/skills/ so Claude Code auto-discovers it in-project
    CLAUDE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILL_SRC, CLAUDE_SKILLS_DIR / "SKILL.md")
    print(f"Copied to {CLAUDE_SKILLS_DIR / 'SKILL.md'}")


if __name__ == "__main__":
    build()
