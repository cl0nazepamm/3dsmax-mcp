#!/usr/bin/env python3
"""One-step installer for 3dsmax-mcp.

Detects 3ds Max, deploys the native bridge, installs MAXScript listener,
builds skills, and registers with AI agents.

Run:  uv run python install.py
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GUP_SRC = ROOT / "native" / "bin" / "mcp_bridge.gup"
MS_SERVER = ROOT / "maxscript" / "mcp_server.ms"
MS_AUTOSTART = ROOT / "maxscript" / "startup" / "mcp_autostart.ms"
CONFIG_SRC = ROOT / "mcp_config.ini"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "3dsmax-mcp"
CONFIG_DST = CONFIG_DIR / "mcp_config.ini"

# Common Max install locations (newest first)
MAX_DIRS = [
    Path(r"C:\Program Files\Autodesk\3ds Max 2027"),
    Path(r"C:\Program Files\Autodesk\3ds Max 2026"),
    Path(r"C:\Program Files\Autodesk\3ds Max 2025"),
    Path(r"C:\Program Files\Autodesk\3ds Max 2024"),
]


def find_max_installations() -> list[Path]:
    return [d for d in MAX_DIRS if (d / "3dsmax.exe").exists()]


def find_max() -> Path | None:
    found = find_max_installations()
    return found[0] if found else None


def select_max(found: list[Path]) -> Path | None:
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    print("\nMultiple 3ds Max installations found:")
    for i, d in enumerate(found, 1):
        print(f"  {i}) {d}")
    choice = input(f"  Select version [1]: ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(found):
            return found[idx]
    except ValueError:
        pass
    return found[0]


def copy_elevated(src: Path, dst: Path) -> bool:
    """Copy a file, elevating to admin if needed."""
    try:
        shutil.copy2(src, dst)
        return True
    except PermissionError:
        print(f"  Need admin rights for {dst.parent}")
        cmd = f'copy /Y "{src}" "{dst}"'
        result = subprocess.run(
            ["powershell", "-Command",
             f'Start-Process -FilePath cmd.exe -ArgumentList \'/c {cmd}\' -Verb RunAs -Wait'],
            capture_output=True, timeout=30,
        )
        return dst.exists()


def deploy_config() -> bool:
    print(f"\n[1/5] Config -> {CONFIG_DST}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_DST.exists():
        print("  Already exists (keeping current settings)")
        return True
    if CONFIG_SRC.exists():
        shutil.copy2(CONFIG_SRC, CONFIG_DST)
        print("  OK (safe_mode=true)")
        return True
    # Write default if source template missing
    CONFIG_DST.write_text("[mcp]\nsafe_mode = true\n", "utf-8")
    print("  OK (created default, safe_mode=true)")
    return True


def deploy_native_bridge(max_dir: Path) -> bool:
    plugins_dir = max_dir / "plugins"
    dst = plugins_dir / "mcp_bridge.gup"
    print(f"\n[2/5] Native bridge -> {dst}")
    if not GUP_SRC.exists():
        print("  SKIP: pre-built binary not found at native/bin/mcp_bridge.gup")
        return False
    if copy_elevated(GUP_SRC, dst):
        print("  OK")
        return True
    print("  FAILED")
    return False


def deploy_maxscript(max_dir: Path) -> bool:
    print(f"\n[3/5] MAXScript listener (TCP fallback)")
    scripts_dir = max_dir / "scripts"
    mcp_dir = scripts_dir / "mcp"
    startup_dir = scripts_dir / "startup"

    ok = True
    try:
        mcp_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        subprocess.run(
            ["powershell", "-Command",
             f'Start-Process -FilePath cmd.exe -ArgumentList \'/c mkdir "{mcp_dir}"\' -Verb RunAs -Wait'],
            capture_output=True, timeout=30,
        )
    dst1 = mcp_dir / "mcp_server.ms"
    dst2 = startup_dir / "mcp_autostart.ms"

    if copy_elevated(MS_SERVER, dst1):
        print(f"  OK: {dst1}")
    else:
        print(f"  FAILED: {dst1}")
        ok = False

    if copy_elevated(MS_AUTOSTART, dst2):
        print(f"  OK: {dst2}")
    else:
        print(f"  FAILED: {dst2}")
        ok = False

    return ok


def build_skills() -> bool:
    print(f"\n[4/5] Building skill files")
    print("  Where should skills be installed?")
    print("    1) Project only")
    print("    2) Global only (default)")
    print("    3) Both project and global (might cause conflict in some agents)")
    choice = input("  Choice [2]: ").strip()
    target = {"1": "local", "3": "both"}.get(choice, "global")
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_skill.py"),
                        "--target", target],
                       check=True, cwd=str(ROOT))
        print("  OK")
        return True
    except subprocess.CalledProcessError:
        print("  FAILED")
        return False


def register_agents() -> bool:
    print(f"\n[5/5] Agent registration")
    dir_str = str(ROOT)

    # Detect which agents are installed
    agents = []
    for name in ["claude", "codex", "gemini"]:
        if shutil.which(name):
            agents.append(name)

    if not agents:
        print("  No agents found on PATH (claude, codex, gemini)")
        print("  Manual registration:")
        print(f'    claude mcp add --scope user 3dsmax-mcp -- uv run --directory "{dir_str}" 3dsmax-mcp')
        return True

    for agent in agents:
        # Each agent CLI has different syntax
        if agent == "claude":
            cmd = f'{agent} mcp add --scope user 3dsmax-mcp -- uv run --directory "{dir_str}" 3dsmax-mcp'
        elif agent == "codex":
            cmd = f'{agent} mcp add 3dsmax-mcp -- uv run --directory "{dir_str}" 3dsmax-mcp'
        elif agent == "gemini":
            cmd = f'{agent} mcp add --scope user 3dsmax-mcp uv run --directory "{dir_str}" 3dsmax-mcp'
        else:
            continue
        print(f"  Registering with {agent}...")
        try:
            subprocess.run(cmd, shell=True, check=True, capture_output=True, timeout=15)
            print(f"  OK: {agent}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print(f"  SKIP: {agent} (run manually: {cmd})")

    # App configs that store mcpServers (Claude Desktop, Gemini)
    app_configs = [
        ("Claude Desktop", Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"),
        ("Gemini", Path.home() / ".gemini" / "settings.json"),
    ]
    entry = {"command": "uv", "args": ["run", "--directory", dir_str, "3dsmax-mcp"]}
    for label, config_path in app_configs:
        if not config_path.parent.exists():
            continue
        try:
            config = json.loads(config_path.read_text("utf-8")) if config_path.exists() else {}
        except Exception:
            config = {}
        servers = config.setdefault("mcpServers", {})
        if servers.get("3dsmax-mcp") != entry:
            servers["3dsmax-mcp"] = entry
            config_path.write_text(json.dumps(config, indent=2) + "\n", "utf-8")
            print(f"  OK: {label} ({config_path})")
        else:
            print(f"  Already up to date: {label}")

    return True


def main():
    print("=" * 60)
    print("  3dsmax-mcp installer")
    print("=" * 60)

    # Find Max
    found = find_max_installations()
    if found:
        max_dir = select_max(found)
        print(f"\nUsing 3ds Max at: {max_dir}")
    else:
        print("\n3ds Max not found in default locations.")
        custom = input("Enter 3ds Max install path (or press Enter to skip): ").strip()
        if custom:
            max_dir = Path(custom)
            if not (max_dir / "3dsmax.exe").exists():
                print(f"  3dsmax.exe not found in {max_dir}")
                max_dir = None
        else:
            max_dir = None

    # Deploy
    deploy_config()
    if max_dir:
        deploy_native_bridge(max_dir)
        deploy_maxscript(max_dir)
    else:
        print("\n[2/5] SKIP: no Max installation")
        print("[3/5] SKIP: no Max installation")

    build_skills()
    register_agents()

    # Summary
    print("\n" + "=" * 60)
    print("  done!")
    print("=" * 60)
    if max_dir:
       print(f"\n  restart 3dsmax to load the native bridge.")
    print(f"  the MCP server starts automatically when your agent connects.")
    print(f"\n ")
    print(f"\n  and thank you for installing 3dsmax-mcp! I hope you enjoy it! 3dsmax forever!!")

    print(f"\n  clone // Metaverse Makers. 2026")
    print()


if __name__ == "__main__":
    main()
