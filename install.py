#!/usr/bin/env python3
"""One-step installer for 3dsmax-mcp.

Removes legacy Max install-dir copies, deploys the ApplicationPlugins bundle,
installs user config, builds skills, and registers with AI agents.

Run:  uv run python install.py
Skip skill install: uv run python install.py --skip-skill
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GUP_SRCS = {
    2023: ROOT / "native" / "bin" / "mcp_bridge_2023.gup",
    2024: ROOT / "native" / "bin" / "mcp_bridge_2024.gup",
    2025: ROOT / "native" / "bin" / "mcp_bridge_2025.gup",
    2026: ROOT / "native" / "bin" / "mcp_bridge_2026.gup",
    2027: ROOT / "native" / "bin" / "mcp_bridge_2027.gup",
}

BUNDLE_SRC = ROOT / "bundle"
BUNDLE_PACKAGE_NAME = "3dsmax-mcp"
APPLICATION_PACKAGE_DST = (
    Path(os.environ.get("ProgramData", "")) / "Autodesk" / "ApplicationPlugins" / BUNDLE_PACKAGE_NAME
)

MS_SERVER = ROOT / "maxscript" / "mcp_server.ms"
CONFIG_SRC = ROOT / "mcp_config.ini"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "3dsmax-mcp"
CONFIG_DST = CONFIG_DIR / "mcp_config.ini"


def pkg_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"

# v0.7.0 standalone chat — .env for the API key, SKILL.md for the system prompt.
ENV_SRC = ROOT / ".env.example"
ENV_DST = CONFIG_DIR / ".env"
SKILL_SRC = ROOT / "skills" / "3dsmax-mcp-dev" / "SKILL.md"
SKILL_DIR = CONFIG_DIR / "skill"
SKILL_DST = SKILL_DIR / "SKILL.md"

INSTALLER_ANSI_BANNER = base64.b64decode(
    "G1swOzMwbSAgIBtbMTszN23c3Nzc3BtbMDszMG0gG1sxOzM3bdzc3Nzc3BtbMDszMG0gICAbWzE7Mzdt3Nzc3NwbWzA7MzBtIBtb"
    "MTszN23c3NwbWzA7MzBtICAgIBtbMTszN23c3NwbWzA7MzBtICAgG1sxOzM3bdzcG1swOzMwbSAgIBtbMTszN23c3BtbMDszMG0g"
    "ICAbWzE7Mzdt3NzcG1swOzMwbSAgG1sxOzM3bdzc3NwbWzA7MzBtICAgG1sxOzM3bdzc3NwbWzA7MzBtIBtbMTszN23c3Nzc3Btb"
    "MDszMG0gIBtbMTszN23c3Nzc3NwbWzBtDQobWzA7MzBtICAbWzE7Mzdt29vfG1swOzM0bdwbWzE7Mzdt39vb29vf39/f29zb2xtb"
    "MDszNG3c3BtbMTszN23f29vb29vcG1swOzMwbSAgG1sxOzM3bdvb29sbWzA7MzRt3BtbMDszMG0gG1sxOzM3bdvb29sbWzA7MzBt"
    "ICAbWzE7Mzdt39vb3Nzb3xtbMDszNG3c3BtbMDszMG0gG1sxOzM3bdvb29sbWzA7MzRt3BtbMDszMG0gIBtbMTszN23b29vb29vf"
    "G1swOzM0bdwbWzE7Mzdt39/b29vbG1swOzM0bdzcG1sxOzM3bd/b2xtbMG0NChtbMDszMG0gICAbWzA7MzRt29sbWzE7MzZt29vb"
    "39vbG1swOzM0bdvf398bWzE7MzZt29vf29vb3Nzc29vf2xtbMDszNG3cG1sxOzM2bdzb39vbG1swOzM0bdsbWzE7MzZt29sbWzA7"
    "MzRt29sbWzE7MzZt29wbWzA7MzBtICAbWzA7MzRt3xtbMTszNm3b29vfG1swOzM0bdsbWzE7MzZt3Nzc29sbWzA7MzRt2xtbMTsz"
    "Nm3b2xtbMDszMG0gG1sxOzM2bdvb39vb2xtbMDszNG3b298bWzA7MzBtIBtbMDszNG3f3xtbMTszNm3f29vc3Nzb2xtbMDszNG3b"
    "G1swbQ0KG1swOzMwbSAgG1sxOzM2bdzcG1swOzMwbSAgG1swOzM0bdsbWzE7MzZt29vb2xtbMDszNG3bG1swOzMwbSAgG1sxOzM2"
    "bdzb29zcG1swOzM0bdvbG1sxOzM2bd/b29vbG1swOzM0bdsbWzE7MzZt29vb2xtbMDszNG3bG1sxOzM2bdvb3Nvb29vb29wbWzA7"
    "MzBtIBtbMTszNm3c29/b29zf39/b2xtbMDszNG3bG1sxOzM2bd/b29sbWzA7MzRt29sbWzE7MzZt29vb3BtbMDszMG0gICAgG1sx"
    "OzM2bdvb29vf398bWzA7MzRt3NvbG1swbQ0KG1swOzMwbSAgG1swOzM2bd/b29vb29/b29vb29vfG1swOzM0bdsbWzA7MzZt39vb"
    "29vb39vbG1swOzM0bdsbWzA7MzZt39vbG1swOzM0bdvbG1swOzM2bdvb29sbWzA7MzRt29vb2xtbMDszNm3b29vb3xtbMDszNG3b"
    "3xtbMDszNm3f29wbWzA7MzRt398bWzA7MzZt29sbWzA7MzRt2xtbMDszMG0gG1swOzM2bd/b3xtbMDszNG3bG1swOzMwbSAbWzA7"
    "MzZt29/f29vb29vf39vbG1swOzM0bdvf398bWzBtDQobWzA7MzBtICAgG1swOzM0bd/b29vb29/b29vb29vfG1swOzMwbSAbWzA7"
    "MzRt39vb29vb39vbG1swOzMwbSAbWzA7MzRt39vbG1swOzMwbSAgG1swOzM0bdvb29sbWzA7MzBtICAgIBtbMDszNG3b29vb3xtb"
    "MDszMG0gIBtbMDszNG3f29wbWzA7MzBtICAbWzA7MzRt29sbWzA7MzBtICAbWzA7MzRt39vfG1swOzMwbSAgG1swOzM0bdvf39vb"
    "29vb39/b2xtbMG0NCg=="
)
ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
REDIRECTED_BANNER_TRANSLATION = str.maketrans({"█": "#", "▀": "^", "▄": "_", "·": "."})


def installer_banner() -> str:
    """Return the ANSI card, stripping terminal controls for redirected output."""
    banner = INSTALLER_ANSI_BANNER.decode("cp437")
    if sys.stdout.isatty():
        return banner
    return ANSI_CSI_RE.sub("", banner).translate(REDIRECTED_BANNER_TRANSLATION)

# Extract list of supported years from GUP_SRCS
MAX_YEARS = sorted(GUP_SRCS.keys(), reverse=True)
PACKAGE_CONTENTS_TEMPLATE = BUNDLE_SRC / "PackageContents.xml.in"
APP_VERSION_PLACEHOLDER = "__APP_VERSION__"


def max_dir_for_year(year: int) -> Path:
    env = os.environ.get(f"ADSK_3DSMAX_x64_{year}", "").strip()
    if env:
        return Path(env)
    return Path(rf"C:\Program Files\Autodesk\3ds Max {year}")


def max_year_for(max_dir: Path) -> int | None:
    for year in MAX_YEARS:
        if max_dir_for_year(year) == max_dir:
            return year
    try:
        return int(max_dir.name.split()[-1])
    except (ValueError, IndexError):
        return None


def gup_src_for(max_dir: Path) -> Path | None:
    year = max_year_for(max_dir)
    if year is None:
        return None
    src = GUP_SRCS.get(year)
    if src and src.exists():
        return src
    return None


def find_max_installations() -> list[Path]:
    return [d for d in (max_dir_for_year(y) for y in MAX_YEARS) if (d / "3dsmax.exe").exists()]


def find_max() -> Path | None:
    found = find_max_installations()
    return found[0] if found else None


def legacy_install_paths(max_dir: Path) -> list[Path]:
    return [
        max_dir / "plugins" / "mcp_bridge.gup",
        max_dir / "scripts" / "mcp" / "mcp_server.ms",
        max_dir / "scripts" / "startup" / "mcp_autostart.ms",
    ]


def legacy_mcp_script_dir(max_dir: Path) -> Path:
    return max_dir / "scripts" / "mcp"


def delete_elevated(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        path.unlink()
        return True
    except PermissionError:
        print(f"  Need admin rights for {path}")
        cmd = f'del /F "{path}"'
        subprocess.run(
            ["powershell", "-Command",
             f'Start-Process -FilePath cmd.exe -ArgumentList \'/c {cmd}\' -Verb RunAs -Wait'],
            capture_output=True, timeout=30,
        )
        return not path.exists()


def _legacy_max_dirs(extra_dirs: list[Path] | None = None) -> list[Path]:
    seen: set[str] = set()
    dirs: list[Path] = []
    for candidate in [*find_max_installations(), *(extra_dirs or [])]:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        dirs.append(candidate)
    return dirs


def remove_legacy_installations(extra_dirs: list[Path] | None = None) -> bool:
    """Remove old-format installs from every known Max dir. Returns False if any remain."""
    print("\nPre-flight: removing legacy installation files")
    max_dirs = _legacy_max_dirs(extra_dirs)
    if not max_dirs:
        print("  No 3ds Max installations scanned (legacy paths only checked when Max is found)")

    for max_dir in max_dirs:
        print(f"  Scanning: {max_dir}")
        for path in legacy_install_paths(max_dir):
            if not path.exists():
                continue
            if delete_elevated(path):
                print(f"    Removed: {path}")
            else:
                print(f"    FAILED: {path}")

        mcp_dir = legacy_mcp_script_dir(max_dir)
        if mcp_dir.exists() and mcp_dir.is_dir() and not any(mcp_dir.iterdir()):
            try:
                mcp_dir.rmdir()
                print(f"    Removed empty: {mcp_dir}")
            except OSError:
                pass

    remaining = [
        path
        for max_dir in max_dirs
        for path in legacy_install_paths(max_dir)
        if path.exists()
    ]
    if remaining:
        print("\n  ERROR: legacy files still present:")
        for path in remaining:
            print(f"    {path}")
        print("  Close 3ds Max and re-run the installer.")
        return False

    print("  OK: no legacy installation files remain")
    return True


def package_contents_xml(app_version: str) -> str:
    if PACKAGE_CONTENTS_TEMPLATE.exists():
        text = PACKAGE_CONTENTS_TEMPLATE.read_text(encoding="utf-8")
        return text.replace(APP_VERSION_PLACEHOLDER, app_version)
    raise FileNotFoundError(f"Missing bundle template: {PACKAGE_CONTENTS_TEMPLATE}")


def stage_bundle(dest: Path) -> tuple[list[int], list[int]]:
    """Stage ApplicationPlugins bundle. Returns (included years, missing years)."""
    bin_dir = dest / "Contents" / "bin"
    scripts_dir = dest / "Contents" / "scripts"
    bin_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    included: list[int] = []
    missing: list[int] = []
    for year in sorted(GUP_SRCS.keys()):
        src = GUP_SRCS[year]
        if src.exists():
            shutil.copy2(src, bin_dir / src.name)
            included.append(year)
        else:
            missing.append(year)

    if MS_SERVER.exists():
        shutil.copy2(MS_SERVER, scripts_dir / "mcp_server.ms")

    (dest / "PackageContents.xml").write_text(
        package_contents_xml(pkg_version()), encoding="utf-8"
    )
    return included, missing


def deploy_application_package() -> bool:
    print(f"\n[2/4] Application package -> {APPLICATION_PACKAGE_DST}")
    if not MS_SERVER.exists():
        print(f"  FAILED: missing {MS_SERVER}")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / BUNDLE_PACKAGE_NAME
        included, missing = stage_bundle(staging)
        if APPLICATION_PACKAGE_DST.exists():
            shutil.rmtree(APPLICATION_PACKAGE_DST)
        shutil.copytree(staging, APPLICATION_PACKAGE_DST)

    for year in included:
        print(f"  OK: Contents/bin/mcp_bridge_{year}.gup")
    for year in missing:
        print(f"  SKIP: Contents/bin/mcp_bridge_{year}.gup (not built)")
    print("  OK: Contents/scripts/mcp_server.ms")
    print(f"  OK: {APPLICATION_PACKAGE_DST}")
    return True


def deploy_config(skip_skill: bool = False) -> bool:
    print(f"\n[1/4] User config -> {CONFIG_DIR}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_DST.exists():
        print("  mcp_config.ini: preserved (already exists)")
    elif CONFIG_SRC.exists():
        shutil.copy2(CONFIG_SRC, CONFIG_DST)
        print("  mcp_config.ini: installed template")
    else:
        CONFIG_DST.write_text("[mcp]\nsafe_mode = true\n", "utf-8")
        print("  mcp_config.ini: created default (safe_mode=true)")

    if ENV_DST.exists():
        print("  .env:           preserved (already exists)")
    elif ENV_SRC.exists():
        shutil.copy2(ENV_SRC, ENV_DST)
        print("  .env:           installed template (edit to add OPENROUTER_API_KEY)")
    else:
        print("  .env:           SKIP (no .env.example in repo)")

    if skip_skill:
        print("  skill/SKILL.md: SKIP (--skip-skill)")
    elif SKILL_SRC.exists():
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SKILL_SRC, SKILL_DST)
        print("  skill/SKILL.md: refreshed")
    else:
        print(f"  skill/SKILL.md: SKIP (source not found at {SKILL_SRC})")

    return True


def build_skills(skip_skill: bool = False) -> bool:
    print("\n[3/4] Building skill files")
    if skip_skill:
        print("  SKIP (--skip-skill)")
        return True
    print("  Where should skills be installed?")
    print("    1) Project only")
    print("    2) Global only (default)")
    print("    3) Both project and global (might cause conflict in some agents)")
    choice = input("  Choice [2]: ").strip()
    target = {"1": "local", "3": "both"}.get(choice, "global")
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_skill.py"), "--target", target],
            check=True,
            cwd=str(ROOT),
        )
        print("  OK")
        return True
    except subprocess.CalledProcessError:
        print("  FAILED")
        return False


def mcp_server_entry(repo_dir: str) -> dict:
    return {"command": "uv", "args": ["run", "--directory", repo_dir, "3dsmax-mcp"]}


def claude_desktop_config_paths() -> list[Path]:
    """Return Claude Desktop config paths for classic and Microsoft Store installs."""
    paths: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    packages_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
    if packages_dir.is_dir():
        for pkg_dir in sorted(packages_dir.glob("Claude_*")):
            add(
                pkg_dir
                / "LocalCache"
                / "Roaming"
                / "Claude"
                / "claude_desktop_config.json"
            )

    add(Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json")
    return paths


def _claude_desktop_label(config_path: Path) -> str:
    parts = {part.lower() for part in config_path.parts}
    if "packages" in parts and any(part.lower().startswith("claude_") for part in config_path.parts):
        return "Claude Desktop (Microsoft Store)"
    return "Claude Desktop"


def app_mcp_config_paths() -> list[tuple[str, Path]]:
    targets = [(_claude_desktop_label(path), path) for path in claude_desktop_config_paths()]
    targets.extend(
        [
            ("Gemini", Path.home() / ".gemini" / "settings.json"),
            ("Cursor", Path.home() / ".cursor" / "mcp.json"),
        ]
    )
    return targets


def warn_if_uv_missing() -> None:
    if shutil.which("uv"):
        return
    print("  WARNING: 'uv' not found on PATH.")
    print("  Claude Desktop / Cursor may fail to launch the MCP server until uv is available.")
    print("  Install with: pip install uv")
    print("  If uv is already installed, add your Python Scripts folder to PATH.")


def register_app_mcp_configs(repo_dir: str) -> None:
    entry = mcp_server_entry(repo_dir)
    for label, config_path in app_mcp_config_paths():
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            print(f"  SKIP: {label} (cannot create {config_path.parent})")
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


def register_agents() -> bool:
    print("\n[4/4] Agent registration")
    dir_str = str(ROOT)

    agents = []
    for name in ["claude", "codex", "gemini"]:
        if shutil.which(name):
            agents.append(name)

    if not agents:
        print("  No agents found on PATH (claude, codex, gemini)")
        print("  Manual registration:")
        print(f'    claude mcp add --scope user 3dsmax-mcp -- uv run --directory "{dir_str}" 3dsmax-mcp')
        print(f'    Cursor: {Path.home() / ".cursor" / "mcp.json"} (updated below if writable)')

    for agent in agents:
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

    warn_if_uv_missing()
    register_app_mcp_configs(dir_str)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install 3dsmax-mcp")
    parser.add_argument(
        "--skip-skill",
        "--skip-skills",
        action="store_true",
        help="Skip copying SKILL.md to the chat config and skip build_skill.py.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    sys.stdout.write(installer_banner())
    print(f"  3dsmax-mcp installer v{pkg_version()}\n")

    found = find_max_installations()
    if found:
        print("\nDetected 3ds Max installations:")
        for max_dir in found:
            year = max_year_for(max_dir)
            label = f" ({year})" if year else ""
            print(f"  {max_dir}{label}")
    else:
        print("\nNo 3ds Max installations detected (2023–2027).")

    if not remove_legacy_installations():
        return 1

    deploy_config(skip_skill=args.skip_skill)
    package_ok = deploy_application_package()
    build_skills(skip_skill=args.skip_skill)
    register_agents()

    print("\n" + "=" * 60)
    print("  done!")
    print("=" * 60)
    if found:
        print("\n  restart 3ds Max to load the application package.")
    elif package_ok:
        print(f"\n  application package installed to {APPLICATION_PACKAGE_DST}")
    if not package_ok:
        print("\n  application package install failed; see messages above.")
    print("  the MCP server starts automatically when your agent connects.")
    print("\n ")
    print("\n  and thank you for installing 3dsmax-mcp! I hope you enjoy it! 3dsmax forever!!")
    print("\n  clone // Metaverse Makers. 2026")
    print()
    return 0 if package_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
