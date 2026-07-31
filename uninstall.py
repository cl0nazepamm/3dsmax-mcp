#!/usr/bin/env python3
"""uninstall 3dsmax-mcp. Removes application package, legacy Max files, skills, and agent registrations.

Run:  uv run python uninstall.py
"""

import json
import shutil
import subprocess
from pathlib import Path

import install

ROOT = Path(__file__).resolve().parent


def dedupe_max_dirs(dirs: list[Path]) -> list[Path]:
    """Collapse duplicate install paths when multiple year keys resolve to the same folder."""
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = str(d.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)
    return unique


def find_max_installations() -> list[Path]:
    """All Max installs for 2023-2027: ADSK_3DSMAX_x64_{year} env var, then default path."""
    return dedupe_max_dirs(install.find_max_installations())


def delete_elevated(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        path.unlink()
        return True
    except PermissionError:
        cmd = f'del /F "{path}"'
        subprocess.run(
            ["powershell", "-Command",
             f'Start-Process -FilePath cmd.exe -ArgumentList \'/c {cmd}\' -Verb RunAs -Wait'],
            capture_output=True, timeout=30,
        )
        return not path.exists()


def rmdir(path: Path):
    """Remove a directory, symlink, or junction."""
    if path.is_symlink() or path.is_junction():
        path.unlink()
        return
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=True)


def remove_max_deployment(max_dir: Path) -> None:
    """Remove native bridge and MAXScript listener from one Max installation."""
    gup = max_dir / "plugins" / "mcp_bridge.gup"
    ms_server = max_dir / "scripts" / "mcp" / "mcp_server.ms"
    ms_auto = max_dir / "scripts" / "startup" / "mcp_autostart.ms"
    ms_dir = max_dir / "scripts" / "mcp"

    for f in [gup, ms_server, ms_auto]:
        if f.exists():
            if delete_elevated(f):
                print(f"  Deleted: {f}")
            else:
                print(f"  FAILED: {f}")
        else:
            print(f"  Already gone: {f.name}")

    if ms_dir.exists() and not any(ms_dir.iterdir()):
        try:
            ms_dir.rmdir()
        except Exception:
            pass


def main():
    print("=" * 60)
    print("  3dsmax-mcp uninstaller")
    print("=" * 60)

    # 1. Remove native bridge + MAXScript from Max
    max_dirs = find_max_installations()
    if max_dirs:
        print(f"\n[1/4] Removing native bridge + MAXScript from {len(max_dirs)} installation(s)")
        for max_dir in max_dirs:
            print(f"\n  {max_dir}")
            remove_max_deployment(max_dir)
    else:
        print("\n[1/4] SKIP: 3ds Max not found")

    # 2. remove skill files, symlinks, junctions, and .skill archives
    print("\n[2/4] Removing skill files")
    SKILL_NAME = "3dsmax-mcp-dev"

    skill_dirs = [
        ROOT / ".claude" / "skills" / SKILL_NAME,
        ROOT / ".agents" / "skills" / SKILL_NAME,
        Path.home() / ".claude" / "skills" / SKILL_NAME,
        Path.home() / ".agents" / "skills" / SKILL_NAME,
    ]

    scan_parents = [
        ROOT / ".claude" / "skills",
        ROOT / ".agents" / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".agents" / "skills",
    ]
    for parent in scan_parents:
        if not parent.exists():
            continue
        for entry in parent.iterdir():
            if entry.name == SKILL_NAME:
                if entry not in skill_dirs:
                    skill_dirs.append(entry)
            if entry.is_symlink() or entry.is_junction():
                try:
                    target = str(entry.resolve())
                    if SKILL_NAME in target and entry not in skill_dirs:
                        skill_dirs.append(entry)
                except Exception:
                    pass

    for d in skill_dirs:
        if d.exists() or d.is_symlink() or d.is_junction():
            kind = "symlink" if d.is_symlink() else "junction" if d.is_junction() else "dir"
            rmdir(d)
            print(f"  Removed ({kind}): {d}")

    gen_files = [ROOT / "AGENTS.md", ROOT / f"{SKILL_NAME}.skill"]
    home_skill = Path.home() / ".claude" / f"{SKILL_NAME}.skill"
    if home_skill.exists():
        gen_files.append(home_skill)

    for f in gen_files:
        if f.exists():
            f.unlink()
            print(f"  Removed: {f}")

    print("\n[4/5] Unregistering from agents")
    agent_cmds = {
        "claude": "claude mcp remove --scope user 3dsmax-mcp",
        "codex": "codex mcp remove 3dsmax-mcp",
        "gemini": "gemini mcp remove --scope user 3dsmax-mcp",
    }
    for agent, cmd in agent_cmds.items():
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, timeout=15)
            if result.returncode == 0:
                print(f"  Removed from {agent}")
            else:
                print(f"  Not registered in {agent}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    app_configs = [
        (install._claude_desktop_label(path), path)
        for path in install.claude_desktop_config_paths()
    ]
    app_configs.extend(
        [
            ("Gemini", Path.home() / ".gemini" / "settings.json"),
            ("Cursor", Path.home() / ".cursor" / "mcp.json"),
        ]
    )
    for label, config_path in app_configs:
        if not config_path.exists():
            continue
        try:
            config = json.loads(config_path.read_text("utf-8"))
            servers = config.get("mcpServers", {})
            if "3dsmax-mcp" in servers:
                del servers["3dsmax-mcp"]
                config_path.write_text(json.dumps(config, indent=2) + "\n", "utf-8")
                print(f"  Removed from {label} ({config_path})")
        except Exception:
            pass

    print("\n[5/5] Cleaning build artifacts")
    for d in [ROOT / ".claude" / "skills", ROOT / ".agents" / "skills"]:
        if d.exists() and not any(d.iterdir()):
            d.rmdir()

    print("\n" + "=" * 60)
    print("  deinstalled! restart 3ds Max to unload the bridge.")
    print("  the repo itself is untouched. you can run install.py to reinstall.")
    print(" ")
    print("  clone // Metaverse Makers. 2026 ")
    print("=" * 60)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
