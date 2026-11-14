#!/usr/bin/env python3
"""One-step installer for 3dsmax-mcp.

Deploys the ApplicationPlugins bundle, installs user config and the agent skill,
and registers with AI agents.

Run:  uv run python install.py
Skip skill install: uv run python install.py --skip-skill
"""

import argparse
import base64
import csv
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from maxmcp.opencode_config import resolve_opencode_config, update_opencode
from maxmcp.user_config import TOOL_PROFILES, with_ini_value

ROOT = Path(__file__).resolve().parent
# Installed from a wheel this file is `maxmcp/installer.py`, so ROOT is the package
# directory; from a checkout it is `install.py` and ROOT is the repo root. What a checkout
# keeps beside the package (native/bin, skills) a wheel
# carries inside it at the same relative paths (see force-include in pyproject.toml).
IS_PACKAGED = Path(__file__).name == "installer.py"
# The config template, bundle manifest and MAXScript listener ship in the package itself.
ASSETS = (ROOT if IS_PACKAGED else ROOT / "maxmcp") / "assets"

GUP_SRCS = {
    2023: ROOT / "native" / "bin" / "mcp_bridge_2023.gup",
    2024: ROOT / "native" / "bin" / "mcp_bridge_2024.gup",
    2025: ROOT / "native" / "bin" / "mcp_bridge_2025.gup",
    2026: ROOT / "native" / "bin" / "mcp_bridge_2026.gup",
    2027: ROOT / "native" / "bin" / "mcp_bridge_2027.gup",
}

BUNDLE_PACKAGE_NAME = "3dsmax-mcp"
APPLICATION_PACKAGE_DST = (
    Path(os.environ.get("ProgramData", "")) / "Autodesk" / "ApplicationPlugins" / BUNDLE_PACKAGE_NAME
)

MS_SERVER = ASSETS / "mcp_server.ms"
CONFIG_SRC = ASSETS / "mcp_config.ini"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "3dsmax-mcp"
CONFIG_DST = CONFIG_DIR / "mcp_config.ini"


def pkg_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"

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


def choose_tool_profile(requested: str | None = None) -> str:
    """Select which tools MCP clients list (maxmcp/profiles.py)."""
    if requested:
        normalized = requested.strip().lower()
        if normalized not in TOOL_PROFILES:
            raise ValueError(f"Unsupported tool profile: {requested}")
        return normalized

    print("\nMCP tool profile (how the model reaches the 3ds Max tools):")
    print("  1) Code (default) - the model works in Python inside 3ds Max, through an API built for")
    print("     agents: the 3ds Max SDK as Python objects, and every tool as a function. One script")
    print("     does a whole task, checks its own result and is a single undo step")
    print("  2) Full (deprecated) - every tool listed for the model to call one at a time")
    print("  3) Progressive (deprecated) - common tools listed, the rest found on demand")
    print("  4) Core (deprecated) - the everyday tools listed, without the plugin and specialty ones")
    print("  Listed tools are sent with every request, so a longer list takes room from each one.")
    try:
        choice = input("  Choice [1]: ").strip()
    except EOFError:
        choice = ""
    return {"2": "full", "3": "progressive", "4": "core"}.get(choice, "code")


def set_ini_value(path: Path, section: str, key: str, value: str) -> None:
    """Update one INI value while preserving unrelated comments and formatting."""
    text = path.read_text("utf-8") if path.exists() else ""
    path.write_text(with_ini_value(text, section, key, value), "utf-8")


# Extract list of supported years from GUP_SRCS
MAX_YEARS = sorted(GUP_SRCS.keys(), reverse=True)
PACKAGE_CONTENTS_TEMPLATE = ASSETS / "PackageContents.xml.in"
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


def package_contents_xml(app_version: str) -> str:
    if PACKAGE_CONTENTS_TEMPLATE.exists():
        text = PACKAGE_CONTENTS_TEMPLATE.read_text(encoding="utf-8")
        return text.replace(APP_VERSION_PLACEHOLDER, app_version)
    raise FileNotFoundError(f"Missing bundle template: {PACKAGE_CONTENTS_TEMPLATE}")


# A bridge built with MCP_HOT_CORE (dev.bat stages one in native/bin) loads its
# core from the build tree and has no core anywhere else. Only that shell's
# loader names the core locator file.
HOT_SHELL_MARKER = "mcp_core.path".encode("utf-16-le")


def is_hot_shell(gup: Path) -> bool:
    return HOT_SHELL_MARKER in gup.read_bytes()


def stage_bundle(dest: Path, allow_hot_shell: bool = False) -> tuple[list[int], list[int]]:
    """Stage ApplicationPlugins bundle. Returns (included years, missing years).

    Raises RuntimeError for a development hot shell unless allow_hot_shell.
    """
    bin_dir = dest / "Contents" / "bin"
    scripts_dir = dest / "Contents" / "scripts"
    bin_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    included: list[int] = []
    missing: list[int] = []
    for year in sorted(GUP_SRCS.keys()):
        src = GUP_SRCS[year]
        if src.exists():
            if not allow_hot_shell and is_hot_shell(src):
                raise RuntimeError(f"{src} is a development bridge that loads its core from the build tree; "
                                   f"build the release bridge for {year} (docs/ADVANCED.md, Building the native bridge)")
            shutil.copy2(src, bin_dir / src.name)
            # run_python's sdk (_mx), built with this bridge when its build made one.
            mx_dir = src.parent / f"mx_{year}"
            modules = sorted(mx_dir.glob("_mx.*.pyd")) if mx_dir.is_dir() else []
            if modules:
                (bin_dir / mx_dir.name).mkdir(exist_ok=True)
                for module in modules:
                    shutil.copy2(module, bin_dir / mx_dir.name / module.name)
            included.append(year)
        else:
            missing.append(year)

    if MS_SERVER.exists():
        shutil.copy2(MS_SERVER, scripts_dir / "mcp_server.ms")

    (dest / "PackageContents.xml").write_text(
        package_contents_xml(pkg_version()), encoding="utf-8"
    )
    return included, missing


def running_max_processes() -> list[str]:
    """Return running 3ds Max processes; each keeps its loaded bridge locked."""
    try:
        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                text=True, errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    return [f"{row[0]} (pid {row[1]})" for row in csv.reader(result.stdout.splitlines())
            if len(row) > 1 and row[0].lower() in {"3dsmax.exe", "3dsmaxbatch.exe"}]


def package_differences(staging: Path, dest: Path) -> list[str]:
    """Return staged files that are missing from dest or differ there."""
    differences = []
    for src in sorted(p for p in staging.rglob("*") if p.is_file()):
        relative = src.relative_to(staging)
        target = dest / relative
        if not target.is_file() or not filecmp.cmp(src, target, shallow=False):
            differences.append(str(relative))
    return differences


def deploy_application_package(allow_hot_shell: bool = False) -> bool:
    print(f"\n[2/4] Application package -> {APPLICATION_PACKAGE_DST}")
    if not MS_SERVER.exists():
        print(f"  FAILED: missing {MS_SERVER}")
        return False
    # A loaded bridge cannot be replaced; a partial copy leaves a broken package.
    running = running_max_processes()
    if running:
        print(f"  FAILED: close 3ds Max first ({', '.join(running)}).")
        print(f"  {APPLICATION_PACKAGE_DST} was left unchanged.")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / BUNDLE_PACKAGE_NAME
        try:
            included, missing = stage_bundle(staging, allow_hot_shell)
        except RuntimeError as error:
            print(f"  FAILED: {error}")
            print(f"  {APPLICATION_PACKAGE_DST} was left unchanged.")
            return False
        if not included:
            # A checkout carries no bridges; a release (Windows setup, PyPI package) does.
            print("  FAILED: no bridge is built. Build one (docs/ADVANCED.md, Building the native bridge;")
            print("  it needs that 3ds Max SDK), or install a release, which carries them for 2023 to 2027.")
            print(f"  {APPLICATION_PACKAGE_DST} was left unchanged.")
            return False
        try:
            if APPLICATION_PACKAGE_DST.exists():
                shutil.rmtree(APPLICATION_PACKAGE_DST)
            shutil.copytree(staging, APPLICATION_PACKAGE_DST)
        except (PermissionError, OSError) as error:
            # ProgramData ACLs can leave an admin-owned tree behind; retry elevated
            print(f"  Could not replace {APPLICATION_PACKAGE_DST} ({error}); retrying with admin rights")
            cmd = (
                f'rmdir /S /Q "{APPLICATION_PACKAGE_DST}" & '
                f'xcopy /E /I /Y "{staging}" "{APPLICATION_PACKAGE_DST}"'
            )
            subprocess.run(
                ["powershell", "-Command",
                 f'Start-Process -FilePath cmd.exe -ArgumentList \'/c {cmd}\' -Verb RunAs -Wait'],
                capture_output=True, timeout=60,
            )
        incomplete = package_differences(staging, APPLICATION_PACKAGE_DST)
    if incomplete:
        print(f"  FAILED: {APPLICATION_PACKAGE_DST} is incomplete: {', '.join(incomplete)}")
        print("  Close 3ds Max and any program using these files, then run the installer again.")
        return False

    for year in included:
        print(f"  OK: Contents/bin/mcp_bridge_{year}.gup")
    for year in missing:
        print(f"  SKIP: Contents/bin/mcp_bridge_{year}.gup (not built)")
    print("  OK: Contents/scripts/mcp_server.ms")
    print(f"  OK: {APPLICATION_PACKAGE_DST}")
    return True


# The Python tag of each 3ds Max that does not embed the server's Python (3.13, as 2027 does).
MAX_PYTHON_TAGS = {2023: "cp39", 2024: "cp310", 2025: "cp311", 2026: "cp311"}


def fetch_max_packages(years: list[int]) -> bool:
    """The tool layer's packages for the Python of each installed 3ds Max before 2027.

    A wheel carries them all; a checkout fetches the ones it needs here. Without them
    run_python still works on those releases, in the MCP server instead of inside 3ds Max.
    """
    script = ROOT / "scripts" / "release" / "vendor_inmax.py"
    tags = sorted({MAX_PYTHON_TAGS[year] for year in years if year in MAX_PYTHON_TAGS})
    if IS_PACKAGED or not tags or not script.is_file():
        return True
    print("\n  Packages for scripts that run inside 3ds Max 2023-2026")
    try:
        subprocess.run([sys.executable, str(script), "--only", *tags], check=True, cwd=str(ROOT))
        return True
    except (subprocess.CalledProcessError, OSError):
        print("  SKIP: could not fetch them, so on those releases run_python runs in the MCP server")
        print("        instead of inside 3ds Max. Retry: uv run python scripts/release/vendor_inmax.py")
        return False


def deploy_config(tool_profile: str = TOOL_PROFILES[0]) -> bool:
    print(f"\n[1/4] User config -> {CONFIG_DIR}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_DST.exists():
        print("  mcp_config.ini: preserved (already exists)")
    elif CONFIG_SRC.exists():
        shutil.copy2(CONFIG_SRC, CONFIG_DST)
        print("  mcp_config.ini: installed template")
    else:
        CONFIG_DST.write_text("[mcp]\nsafe_mode = false\n", "utf-8")
        print("  mcp_config.ini: created default (safe_mode=false)")

    set_ini_value(CONFIG_DST, "mcp", "tool_profile", tool_profile)
    print(f"  MCP tools:       {tool_profile}")

    return True


SKILL_NAME = "3dsmax-mcp-dev"
SKILL_SRC = ROOT / "skills" / SKILL_NAME


def skill_destinations(target: str) -> list[Path]:
    """Where the agent skill goes: this project, the user's home, or both."""
    local = [ROOT / ".agents" / "skills" / SKILL_NAME]
    home = [Path.home() / ".claude" / "skills" / SKILL_NAME, Path.home() / ".agents" / "skills" / SKILL_NAME]
    return {"local": local, "both": local + home}.get(target, home)


def install_skill(skip_skill: bool = False) -> bool:
    """Copy the skill and its references (the .md files of skills/3dsmax-mcp-dev) to where agents read them."""
    print("\n[3/4] Agent skill")
    if skip_skill:
        print("  SKIP (--skip-skill)")
        return True
    files = sorted(SKILL_SRC.glob("*.md"))
    if not (SKILL_SRC / "SKILL.md").exists():
        print(f"  FAILED: missing {SKILL_SRC / 'SKILL.md'}")
        return False
    print("  Where should the skill be installed?")
    print("    1) Project only")
    print("    2) Global only (default)")
    print("    3) Both project and global (might cause conflict in some agents)")
    try:
        choice = input("  Choice [2]: ").strip()
    except EOFError:
        choice = ""
    ok = True
    for dest in skill_destinations({"1": "local", "3": "both"}.get(choice, "global")):
        try:
            # An install before 0.5 left a link here; files copied into it would land in its target.
            if dest.is_symlink() or dest.is_junction():
                dest.unlink()
            dest.mkdir(parents=True, exist_ok=True)
            for file in files:
                shutil.copy2(file, dest / file.name)
            print(f"  OK: {dest} ({len(files)} files)")
        except OSError as error:
            print(f"  FAILED: {dest} ({error})")
            ok = False
    return ok


def console_script_path() -> Path | None:
    """Find the installed `3dsmax-mcp` console script next to the running interpreter."""
    exe = Path(sys.executable)
    candidates = (
        exe.with_name("3dsmax-mcp.exe"),          # venv: Scripts/python.exe next to it
        exe.parent / "Scripts" / "3dsmax-mcp.exe",  # system install: Scripts/ subdir
    )
    return next((c for c in candidates if c.exists()), None)


def mcp_server_command(repo_dir: str) -> list[str]:
    """Return the launch command for checkout and wheel installs."""
    if IS_PACKAGED:
        script = console_script_path()
        if script is not None:
            return [str(script)]
        # User-site and Microsoft Store installs may place console scripts away
        # from sys.executable. The installed module remains an absolute, uv-free
        # fallback in every pip layout.
        return [sys.executable, "-m", "maxmcp.server"]
    return ["uv", "run", "--directory", repo_dir, "3dsmax-mcp"]


def mcp_server_entry(repo_dir: str) -> dict:
    command = mcp_server_command(repo_dir)
    entry = {"command": command[0]}
    if len(command) > 1:
        entry["args"] = command[1:]
    return entry


def agent_registration_command(agent: str, server_command: list[str],
                               env: dict[str, str] | None = None) -> list[str] | None:
    """Build one agent CLI registration command around the shared server launch."""
    if agent == "claude":
        # -e is variadic, so it must follow the server name.
        env_args = [arg for key, value in (env or {}).items() for arg in ("-e", f"{key}={value}")]
        return [agent, "mcp", "add", "--scope", "user", "3dsmax-mcp", *env_args, "--", *server_command]
    if agent == "codex":
        return [agent, "mcp", "add", "3dsmax-mcp", "--", *server_command]
    if agent == "gemini":
        return [agent, "mcp", "add", "--scope", "user", "3dsmax-mcp", *server_command]
    return None


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
            text = config_path.read_text("utf-8-sig") if config_path.exists() else ""
            config = json.loads(text) if text.strip() else {}
            servers = config.setdefault("mcpServers", {}) if isinstance(config, dict) else None
            if not isinstance(servers, dict):
                raise ValueError("mcpServers is not an object")
        except (OSError, ValueError) as exc:
            # Never replace settings we cannot read: they may hold other servers.
            print(f"  SKIP: {label} (left unchanged, cannot read {config_path}: {exc})")
            print(f'    Add manually under "mcpServers": "3dsmax-mcp": {json.dumps(entry)}')
            continue
        if servers.get("3dsmax-mcp") == entry:
            print(f"  Already up to date: {label}")
            continue
        servers["3dsmax-mcp"] = entry
        try:
            replace_file(config_path, (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError as exc:
            print(f"  SKIP: {label} ({config_path}: {exc})")
            continue
        print(f"  OK: {label} ({config_path})")
    register_opencode(repo_dir)


def replace_file(path: Path, content: bytes) -> None:
    """Write through a sibling temp file so an interrupted write cannot truncate settings."""
    fd, temporary = tempfile.mkstemp(prefix=".3dsmax-mcp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def register_opencode(repo_dir: str) -> bool:
    path = resolve_opencode_config(Path.home())
    temporary = None
    try:
        before = path.read_bytes() if path.exists() else None
        content = update_opencode(before.decode("utf-8-sig") if before is not None else "{}\n", {
            "type": "local", "command": mcp_server_command(repo_dir), "enabled": True,
        })
        if content == before:
            print(f"  Already up to date: OpenCode ({path})")
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".3dsmax-mcp-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        if (path.read_bytes() if path.exists() else None) != before:
            raise OSError("Settings changed during installation; close OpenCode and retry")
        if before is not None:
            backup = path.with_suffix(path.suffix + ".3dsmax-mcp.bak")
            if not backup.exists():
                backup.write_bytes(before)
        os.replace(temporary, path)
        print(f"  OK: OpenCode ({path})")
        return True
    except (OSError, ValueError) as exc:
        print(f"  SKIP: OpenCode ({path}): {exc}")
        return False
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def run_agent_command(command: list[str]) -> str | None:
    """Run an agent CLI command; return its output on failure, None on success."""
    try:
        # Agent CLIs installed through npm are commonly .cmd shims on
        # Windows, so keep shell execution while quoting the argv once.
        subprocess.run(subprocess.list2cmdline(command), shell=True, check=True,
                       capture_output=True, timeout=15)
    except subprocess.TimeoutExpired:
        return "timed out"
    except subprocess.CalledProcessError as exc:
        parts = [p.decode("utf-8", "replace") if isinstance(p, bytes) else p
                 for p in (exc.stderr, exc.stdout) if p]
        return "\n".join(parts).strip() or f"exit code {exc.returncode}"
    return None


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), text)


def claude_user_env() -> dict[str, str]:
    """Return the env of the existing Claude Code user entry, or {} if unreadable."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    path = (Path(base) if base else Path.home()) / ".claude.json"
    try:
        env = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["3dsmax-mcp"].get("env")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}


def register_agents() -> bool:
    print("\n[4/4] Agent registration")
    dir_str = str(ROOT)
    server_command = mcp_server_command(dir_str)

    agents = []
    for name in ["claude", "codex", "gemini"]:
        if shutil.which(name):
            agents.append(name)

    if not agents:
        print("  No agents found on PATH (claude, codex, gemini)")
        print("  Manual registration:")
        manual = agent_registration_command("claude", server_command)
        print(f"    {subprocess.list2cmdline(manual or [])}")
        print(f'    Cursor: {Path.home() / ".cursor" / "mcp.json"} (updated below if writable)')

    for agent in agents:
        cmd = agent_registration_command(agent, server_command)
        if cmd is None:
            continue
        display = subprocess.list2cmdline(cmd)
        print(f"  Registering with {agent}...")
        error = run_agent_command(cmd)
        if error and agent == "claude" and "already exists" in error:
            # `claude mcp add` never overwrites, so an existing entry would keep its
            # old launch command. Replace it, keeping env such as MCP_TOOL_PROFILE.
            env = claude_user_env()
            error = run_agent_command([agent, "mcp", "remove", "3dsmax-mcp", "--scope", "user"])
            if not error:
                cmd = agent_registration_command(agent, server_command, env)
                display = subprocess.list2cmdline(cmd)
                error = run_agent_command(cmd)
                if error:
                    print(f"  FAILED: {agent} entry was removed but not re-added "
                          f"({_first_line(error)}); run manually: {display}")
                    continue
        if error:
            print(f"  SKIP: {agent} ({_first_line(error)}; run manually: {display})")
        else:
            print(f"  OK: {agent}")

    if not IS_PACKAGED:
        warn_if_uv_missing()
    register_app_mcp_configs(dir_str)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install 3dsmax-mcp")
    parser.add_argument(
        "--skip-skill",
        "--skip-skills",
        action="store_true",
        help="Skip installing the agent skill.",
    )
    parser.add_argument(
        "--tool-profile",
        choices=TOOL_PROFILES,
        help="Which tools MCP clients list; omit to choose interactively.",
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

    tool_profile = choose_tool_profile(args.tool_profile)

    deploy_config(tool_profile=tool_profile)
    package_ok = deploy_application_package()
    fetch_max_packages([year for year in map(max_year_for, found) if year])
    install_skill(skip_skill=args.skip_skill)
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
    print(f"  MCP tool profile: {tool_profile}")
    print("\n ")
    print("\n  and thank you for installing 3dsmax-mcp!")
    print("\n  clone.llc // Metaverse Makers. 2026")
    print()
    return 0 if package_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
