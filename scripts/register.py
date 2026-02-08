#!/usr/bin/env python3
"""Register 3dsmax-mcp as a global MCP server in ~/.claude.json."""

import json
import shutil
import site
import sysconfig
from pathlib import Path

CLAUDE_CONFIG = Path.home() / ".claude.json"
SERVER_NAME = "3dsmax-mcp"
EXE_NAME = "3dsmax-mcp.exe" if sysconfig.get_platform().startswith("win") else "3dsmax-mcp"


def find_exe() -> str | None:
    """Find the 3dsmax-mcp executable on PATH or in common pip script dirs."""
    found = shutil.which("3dsmax-mcp")
    if found:
        return found

    # Fallback: check pip script directories (user + site-packages)
    candidates = []
    # User scripts (pip install --user / pip install -e .)
    user_scripts = Path(site.getusersitepackages()).parent / "Scripts" \
        if sysconfig.get_platform().startswith("win") \
        else Path(sysconfig.get_path("scripts", "posix_user"))
    candidates.append(user_scripts / EXE_NAME)
    # Site-packages scripts
    candidates.append(Path(sysconfig.get_path("scripts")) / EXE_NAME)

    for p in candidates:
        if p.exists():
            return str(p)
    return None


def register():
    # Find the executable
    exe = find_exe()
    if exe is None:
        print("ERROR: '3dsmax-mcp' not found on PATH.")
        print("Install first:  pip install -e .  (from the project root)")
        raise SystemExit(1)

    exe = str(Path(exe).resolve())

    # Load or create config
    if CLAUDE_CONFIG.exists():
        config = json.loads(CLAUDE_CONFIG.read_text("utf-8"))
    else:
        config = {}

    servers = config.setdefault("mcpServers", {})

    entry = {
        "command": exe,
        "args": [],
    }

    old = servers.get(SERVER_NAME)
    servers[SERVER_NAME] = entry

    CLAUDE_CONFIG.write_text(json.dumps(config, indent=2) + "\n", "utf-8")

    if old == entry:
        print(f"'{SERVER_NAME}' already registered in {CLAUDE_CONFIG} (no changes)")
    elif old is not None:
        print(f"Updated '{SERVER_NAME}' in {CLAUDE_CONFIG}")
    else:
        print(f"Registered '{SERVER_NAME}' in {CLAUDE_CONFIG}")

    print(f"  command: {exe}")


if __name__ == "__main__":
    register()
