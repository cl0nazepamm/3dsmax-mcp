#!/usr/bin/env python3
"""Build the portable .skill file, sync to agent skills, and generate AGENTS.md."""

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
AGENTS_MD = ROOT / "AGENTS.md"

AGENTS_HEADER = """# 3dsmax-mcp

MCP server for AI agents to control 3ds Max. This file is auto-generated from `scripts/build_skill.py`.

## Skill scope

- `skills/3dsmax-mcp-dev/SKILL.md` and its bundled references are exclusively for agent-facing 3ds Max usage: tool selection, scene workflows, and runtime usage pitfalls.
- Never record software-development bugs, implementation details, build failures, bridge internals, code discoveries, or postmortem lessons in skill files.
- Keep development knowledge in the relevant code, tests, development documentation, issue, or commit instead.

## Project Structure
- `maxmcp/server.py` — FastMCP server entry point
- `maxmcp/max_client.py` — TCP socket client (connects to 127.0.0.1:8765)
- `maxmcp/tools/` — MCP tool implementations (one file per category)
- `maxscript/mcp_server.ms` — MAXScript listener (runs inside 3ds Max as a bundle post-start-up script)
- `bundle/PackageContents.xml.in` — ApplicationPlugins manifest template (installed to `%ProgramData%\\Autodesk\\ApplicationPlugins\\3dsmax-mcp`)
- `native/` — C++ GUP bridge plugin (named pipe, direct SDK handlers)

## Skills & Build
- `skills/3dsmax-mcp-dev/SKILL.md` — agent-facing 3ds Max usage guide and reference router
- `skills/3dsmax-mcp-dev/procedural-graphs.md` — agent-facing Data Channel and MCG usage workflows/pitfalls
- `scripts/build_skill.py` — builds `.skill` archive, copies to repo `.agents/skills/` plus user-level `.claude/skills/` and `.agents/skills/`, generates `AGENTS.md`
- `.agents/skills/` and `AGENTS.md` are gitignored — never edit them directly

## Key Patterns
- Tools registered via `@mcp.tool()` in `maxmcp/tools/*.py`
- External MCP keeps `MCP_TOOL_PROFILE=full` as the eager compatibility default. Use `MCP_TOOL_PROFILE=progressive` for context-limited local or smaller models that benefit from the compact `list_toolsets` / `describe_toolset` / `call_tool` surface with lazy operational schemas; `core` remains the smaller eager profile.
- Direct scene reads use `query_scene` and `get_session_context`; use repo/source inspection only for code, build, packaging, or debugging requests.
- Tool wrappers send either native JSON commands or MAXScript fallback strings through `client.send_command()`
- Prefer native C++ handlers for deterministic scene, identity, transaction, and animation operations
- Prefer OpenPBR for neutral PBR material creation/conversion; use PhysicalMaterial only as fallback or when explicitly requested.
- Viewport capture: `gw.getViewportDib()` → save to temp → `Read` tool to view
- Do not RENDER unless user explicitly asks — but `capture_multi_view` (quad view) is encouraged after scene changes
- Standalone chat (v0.7.0): `MCP Chat` macroscript opens a Win32 window; config in `%LOCALAPPDATA%\\3dsmax-mcp\\mcp_config.ini` `[llm]`, tool registry auto-generated from Python by `scripts/gen_tool_registry.py`, dispatches through the same `CommandDispatcher` so `safe_mode` applies. Prompt defaults stay compact, tool coverage defaults to full (`prompt_mode=compact`, `tool_profile=full`), scene is not auto-injected (`include_scene_snapshot=false`); use `tool_profile=core` only when a smaller tool list is needed.
"""


def generate_agents_md():
    """Generate AGENTS.md from the repo header + inlined skill file.

    Codex/Gemini read AGENTS.md from the repo root. They don't have
    the skill system, so we inline SKILL.md and route bundled references back
    to their source paths in the checkout.
    """
    # Bundled references are intentionally not inlined; agents read them only
    # when the core skill routes the current task there.
    parts = [AGENTS_HEADER, "", "---", ""]

    if SKILL_SRC.exists():
        # Strip frontmatter from SKILL.md
        skill_text = SKILL_SRC.read_text("utf-8")
        if skill_text.startswith("---"):
            end = skill_text.find("---", 3)
            if end != -1:
                skill_text = skill_text[end + 3:].lstrip("\n")
        for ref in ("procedural-graphs.md", "tyflow-graphs.md"):
            skill_text = skill_text.replace(
                f"]({ref})",
                f"](skills/3dsmax-mcp-dev/{ref})",
            )
        parts.append(skill_text)

    AGENTS_MD.write_text("\n".join(parts), "utf-8")
    print(f"  Generated {AGENTS_MD.name} (with inlined SKILL.md)")


def collect_skill_files():
    """Collect the core skill and its bundled reference files."""
    files = [SKILL_SRC, PROCEDURAL_GRAPHS_REF]
    for ref in ("tyflow-graphs.md",):
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
