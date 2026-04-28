#!/usr/bin/env python3
"""Build the portable .skill file, sync to agent skills, and generate AGENTS.md."""

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "3dsmax-mcp-dev"
SKILL_SRC = SKILL_DIR / "SKILL.md"
SKILL_OUT = ROOT / "3dsmax-mcp-dev.skill"
LOCAL_AGENTS_DIR = ROOT / ".agents" / "skills" / "3dsmax-mcp-dev"
GLOBAL_SKILLS_DIR = Path.home() / ".claude" / "skills" / "3dsmax-mcp-dev"
GLOBAL_AGENTS_DIR = Path.home() / ".agents" / "skills" / "3dsmax-mcp-dev"
AGENTS_MD = ROOT / "AGENTS.md"

AGENTS_HEADER = """# 3dsmax-mcp

MCP server for AI agents to control 3ds Max. This file is auto-generated from `scripts/build_skill.py`.

## learn-from-mistakes

When you encounter a bug, unexpected behavior, or discover a MAXScript/3ds Max/MCP pitfall:
1. Fix the issue
2. Append the lesson to the relevant section in `skills/3dsmax-mcp-dev/SKILL.md`
3. One line per lesson — include the pattern or fix
4. Check for duplicates before adding

## Project Structure
- `src/server.py` — FastMCP server entry point
- `src/max_client.py` — TCP socket client (connects to 127.0.0.1:8765)
- `src/tools/` — MCP tool implementations (one file per category)
- `maxscript/mcp_server.ms` — MAXScript listener (runs inside 3ds Max)
- `maxscript/startup/mcp_autostart.ms` — auto-start loader for 3ds Max
- `native/` — C++ GUP bridge plugin (named pipe, 53 native handlers)

## Skills & Build
- `skills/3dsmax-mcp-dev/SKILL.md` — source of truth (grows via learn-from-mistakes)
- `scripts/build_skill.py` — builds `.skill` archive, copies to repo `.agents/skills/` plus user-level `.claude/skills/` and `.agents/skills/`, generates `AGENTS.md`
- `.agents/skills/` and `AGENTS.md` are gitignored — never edit them directly

## Key Patterns
- Tools registered via `@mcp.tool()` in `src/tools/*.py`
- External MCP defaults to compact `MCP_TOOL_PROFILE=core`, including controller tools; set `MCP_TOOL_PROFILE=full` to register specialty modules (`data_channel`, `effects`, `floor_plan`, `railclone`, `render`, `scattering`, `state_sets`, `tyflow`, `wire_params`, `chat`).
- Direct scene tools include `get_session_context`, `get_scene_snapshot`, `get_selection_snapshot`, and `learn_scene_patterns`; use repo/source inspection only for code, build, packaging, or debugging requests.
- All tools send MAXScript strings to 3ds Max via `client.send_command()`
- MAXScript results returned as JSON strings via manual concatenation
- Prefer OpenPBR for neutral PBR material creation/conversion; use PhysicalMaterial only as fallback or when explicitly requested.
- Viewport capture: `gw.getViewportDib()` → save to temp → `Read` tool to view
- Do not RENDER unless user explicitly asks — but `capture_multi_view` (quad view) is encouraged after scene changes
- Standalone chat (v0.7.0): `MCP Chat` macroscript opens a Win32 window; config in `%LOCALAPPDATA%\\3dsmax-mcp\\mcp_config.ini` `[llm]`, tool registry auto-generated from Python by `scripts/gen_tool_registry.py`, dispatches through the same `CommandDispatcher` so `safe_mode` applies. Token defaults are compact: `prompt_mode=compact`, `tool_profile=core`; use `prompt_mode=full` or `tool_profile=full` only when needed.
"""


def generate_agents_md():
    """Generate AGENTS.md from the repo header + inlined skill file.

    Codex/Gemini read AGENTS.md from the repo root. They don't have
    the skill system, so we inline SKILL.md directly into AGENTS.md.
    """
    # Inline SKILL.md only (pitfalls, tool reference, architecture).
    # MAXScript reference files (maxscript-*.md) are too large to inline —
    # agents can read them on demand from skills/3dsmax-mcp-dev/
    parts = [AGENTS_HEADER, "", "---", ""]

    if SKILL_SRC.exists():
        # Strip frontmatter from SKILL.md
        skill_text = SKILL_SRC.read_text("utf-8")
        if skill_text.startswith("---"):
            end = skill_text.find("---", 3)
            if end != -1:
                skill_text = skill_text[end + 3:].lstrip("\n")
        parts.append(skill_text)

    AGENTS_MD.write_text("\n".join(parts), "utf-8")
    print(f"  Generated {AGENTS_MD.name} (with inlined SKILL.md)")


def collect_skill_files():
    """Collect SKILL.md + all maxscript-*.md reference files."""
    files = [SKILL_SRC]
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

    # 3. Generate AGENTS.md
    generate_agents_md()

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
