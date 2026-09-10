#!/usr/bin/env python3
"""Remove installed 3dsmax-mcp state before reinstalling or migrating.

Run: python uninstall.py [--dry-run]
Close Max and AI clients first. Preserves source, backups, Python and pip packages.
Requires only Python's standard library and the adjacent install.py.
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import install

ROOT = Path(__file__).resolve().parent
NAME = "3dsmax-mcp"
SKILL = "3dsmax-mcp-dev"
PLUGIN = "3dsmax-mcp@3dsmax-codex"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{56A47E4C-9E1D-488E-AF71-45A379AC1386}_is1"
SERVER_NAMES = {NAME, "3dsmax_mcp", "3dsmax-mcp-codex"}


def dedupe_max_dirs(dirs: list[Path]) -> list[Path]:
    return list({os.path.normcase(str(d.resolve())): d for d in dirs}.values())


def find_max_installations() -> list[Path]:
    return dedupe_max_dirs(install.find_max_installations())


def ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def powershell(script: str, *, elevated: bool = False) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    if elevated:
        # Elevate machine file/uninstall work only, never the original user's settings.
        wrapper = (
            "$ErrorActionPreference='Stop'; "
            "$p=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru "
            f"-ArgumentList '-NoProfile -NonInteractive -EncodedCommand {encoded}'; exit $p.ExitCode"
        )
        encoded = base64.b64encode(wrapper.encode("utf-16-le")).decode("ascii")
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True, timeout=120)


def validate_target(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if absolute == Path(absolute.anchor) or absolute == Path.home():
        raise ValueError(f"Refusing broad removal: {absolute}")
    if absolute.is_relative_to(ROOT):
        allowed = {Path(f".agents/skills/{SKILL}"), Path(f".claude/skills/{SKILL}"),
                   Path(f".codex/skills/{SKILL}"), Path(f"{SKILL}.skill")}
        if absolute.relative_to(ROOT) not in allowed:
            raise ValueError(f"Preserving source checkout: {absolute}")
    for parent in absolute.parents:
        if parent.is_symlink() or parent.is_junction():
            raise ValueError(f"Refusing removal through redirected parent: {absolute}")
    return absolute


def remove_path(path: Path) -> None:
    path = validate_target(path)
    if not os.path.lexists(path):
        return
    try:
        if path.is_symlink():
            path.unlink()
        elif path.is_junction():
            path.rmdir()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except PermissionError:
        if os.name != "nt":
            raise
        # Stay in PowerShell end-to-end, with an exact literal target validated above.
        result = powershell(
            "$ErrorActionPreference='Stop'; " + f"$p={ps_literal(path)}; "
            "if (Test-Path -LiteralPath $p) { $i=Get-Item -LiteralPath $p -Force; "
            "if ($i.Attributes -band [IO.FileAttributes]::ReparsePoint) { Remove-Item -LiteralPath $p -Force } "
            "else { Remove-Item -LiteralPath $p -Recurse -Force } }", elevated=True)
        if result.returncode:
            raise OSError(f"Could not remove {path} (locked or elevation declined)")
    if os.path.lexists(path):
        raise OSError(f"Still present after removal: {path}")


def clean_json(data: object, context: str = "") -> int:
    removed = 0
    if isinstance(data, dict):
        for key in list(data):
            target = context in {"mcpServers", "mcp_servers", "servers"} and key in SERVER_NAMES
            target |= context in {"plugins", "enabledPlugins"} and key == PLUGIN
            if target:
                del data[key]
                removed += 1
            else:
                removed += clean_json(data[key], key)
    elif isinstance(data, list):
        for index in range(len(data) - 1, -1, -1):
            value = data[index]
            if context in {"allow", "deny", "ask", "allowedTools", "enabledMcpjsonServers", "disabledMcpjsonServers"} and isinstance(value, str) and (
                value in SERVER_NAMES or value.startswith(("mcp__3dsmax-mcp__", "mcp__3dsmax_mcp__"))
            ):
                del data[index]
                removed += 1
            else:
                removed += clean_json(value, context)
    return removed


def clean_toml(text: str) -> str:
    """Keep formatting. Refuse edits unless the full parsed result matches the intended change."""
    expected = tomllib.loads(text)
    targets = []
    for group, names in (("mcp_servers", SERVER_NAMES), ("plugins", {PLUGIN})):
        table = expected.get(group, {})
        if not isinstance(table, dict):
            raise ValueError(f"Invalid {group} configuration")
        for name in names:
            if name in table:
                del table[name]
                targets.append((group, name))
    if not targets:
        return text
    skip, output = False, []
    for line in text.splitlines(keepends=True):
        if re.match(r"^\s*\[[^[]", line):
            try:
                header = tomllib.loads(line + "\n__uninstall_probe__ = 0\n")
                keys = []
                while isinstance(header, dict) and len(header) == 1:
                    key = next(iter(header))
                    keys.append(key)
                    header = header[key]
                skip = any(tuple(keys[:2]) == target for target in targets)
            except tomllib.TOMLDecodeError:
                pass
        elif re.match(r"^\s*\[\[", line):
            skip = False
        if not skip:
            output.append(line)
    updated = "".join(output)
    parsed = tomllib.loads(updated)
    for group, _ in targets:
        if expected.get(group) == {} and group not in parsed:
            expected.pop(group)
    if parsed != expected:
        raise ValueError("Unsupported inline/multiline MCP TOML layout; remove its entry in your client and retry")
    return updated


def config_paths(home: Path) -> list[Path]:
    paths = [home / ".claude.json", home / ".claude/settings.json", home / ".claude/settings.local.json",
             home / ".cursor/mcp.json", home / ".gemini/settings.json",
             Path(os.environ.get("CODEX_HOME", home / ".codex")) / "config.toml",
             *install.claude_desktop_config_paths(),
             ROOT / ".mcp.json", ROOT / ".claude/settings.json", ROOT / ".claude/settings.local.json",
             ROOT / ".cursor/mcp.json", ROOT / ".codex/config.toml"]
    return list(dict.fromkeys(paths))


def read_json(text: str) -> object:
    """Accept client JSON with comments/trailing commas, without touching strings."""
    string = r'"(?:[^"\\]|\\.)*"'
    text = re.sub(string + r'|//[^\r\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else ' ', text)
    text = re.sub(string + r'|,(\s*[}\]])',
                  lambda m: m[1] if m[1] is not None else m[0], text)
    return json.loads(text)


def config_edits(home: Path) -> list[tuple[Path, bytes, bytes]]:
    edits = []
    for path in config_paths(home):
        if not path.is_file():
            continue
        before = path.read_bytes()
        text = before.decode("utf-8-sig")
        try:
            if path.suffix == ".toml":
                updated = clean_toml(text)
            else:
                data = read_json(text)
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
                updated = json.dumps(data, indent=2, ensure_ascii=False) + "\n" if clean_json(data) else text
        except ValueError as exc:
            raise ValueError(f"Cannot safely update {path}: {exc}") from exc
        if updated != text:
            edits.append((path, before, updated.encode("utf-8")))
    return edits


def write_config(path: Path, before: bytes, after: bytes) -> None:
    if path.read_bytes() != before:
        raise OSError(f"Client changed {path}; close the client and retry")
    fd, temporary = tempfile.mkstemp(prefix=".mcp-uninstall-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(after)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def removal_paths(home: Path) -> list[Path]:
    roaming, local = Path(os.environ["APPDATA"]), Path(os.environ["LOCALAPPDATA"])
    # Keep user preferences and setup backups; remove only generated client hints/logs.
    paths = [install.APPLICATION_PACKAGE_DST, local / NAME / "mcp-client.json", local / NAME / "setup.log",
             roaming / f"Autodesk/ApplicationPlugins/{NAME}",
             roaming / "Autodesk/ApplicationPlugins/3dsmax-mcp-codex", ROOT / f"{SKILL}.skill"]
    for base in (home, ROOT):
        paths.extend(base / client / "skills" / SKILL for client in (".agents", ".claude", ".codex"))
    paths.append(home / ".claude" / f"{SKILL}.skill")
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    paths.append(codex_home / "plugins/cache/3dsmax-codex/3dsmax-mcp")
    for max_dir in find_max_installations():
        paths.extend(install.legacy_install_paths(max_dir))
    profiles = local / "Autodesk/3dsMax"
    for pattern in ("*/*/scripts/startup/mcp_autostart.ms", "*/*/scripts/mcp/mcp_server.ms",
                    "*/*/usermacros/MCP-MCP_*.mcr"):
        paths.extend(profiles.glob(pattern))
    return list(dict.fromkeys(paths))


def windows_packages() -> list[tuple[Path, Path]]:
    if os.name != "nt":
        return []
    import winreg
    packages = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                key = winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with key:
                try:
                    folder = Path(winreg.QueryValueEx(key, "InstallLocation")[0])
                    command = winreg.QueryValueEx(key, "UninstallString")[0]
                except FileNotFoundError as exc:
                    raise ValueError("Incomplete Windows uninstall registration; repair it via Windows Installed apps") from exc
            match = re.fullmatch(r'"([^"\r\n]+)"\s*', command) or re.fullmatch(r'([^"\r\n]+\.exe)', command)
            if not match:
                raise ValueError("Unexpected Windows uninstall command; use Windows Installed apps")
            executable = Path(match.group(1))
            if executable.parent.resolve() != folder.resolve() or not re.fullmatch(r"unins\d+\.exe", executable.name, re.I):
                raise ValueError("Windows uninstall registration does not match its installation folder")
            if Path(sys.executable).resolve().is_relative_to(folder.resolve()):
                raise ValueError("Run uninstall.py using Python outside the installed 3dsmax-mcp runtime")
            packages.append((folder, executable))
    return list(dict.fromkeys(packages))


def run_command(args: list[str]) -> None:
    if args[0].lower().endswith((".cmd", ".bat")) and os.name == "nt":
        result = powershell("& " + " ".join(ps_literal(arg) for arg in args) + "; exit $LASTEXITCODE")
    else:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise OSError(f"Command failed ({result.returncode}): {subprocess.list2cmdline(args)}")


def uninstall_windows_package(folder: Path, executable: Path) -> None:
    if not executable.is_file():
        raise OSError(f"Missing Windows uninstaller: {executable}; repair/uninstall it via Windows Installed apps")
    result = powershell(
        "$ErrorActionPreference='Stop'; "
        f"$p=Start-Process -FilePath {ps_literal(executable)} "
        "-ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -WindowStyle Hidden -Wait -PassThru; exit $p.ExitCode",
        elevated=True)
    if result.returncode or any(p[0] == folder for p in windows_packages()):
        raise OSError(f"Windows package uninstall incomplete: {folder}")


def max_running() -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq 3dsmax.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True, check=True, timeout=20)
    return any(row and row[0].lower() == "3dsmax.exe" for row in csv.reader(io.StringIO(result.stdout)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List changes without modifying the machine")
    args = parser.parse_args(argv)
    home = Path.home()
    try:
        if os.name != "nt":
            raise ValueError("This uninstaller targets Windows")
        if not args.dry_run and max_running():
            raise ValueError("Close 3ds Max and AI clients before uninstalling; no processes were closed")
        # Preflight configuration and removal targets before any mutation.
        edits = config_edits(home)
        packages = windows_packages()
        paths = [validate_target(p) for p in removal_paths(home) if os.path.lexists(p)]
        codex = shutil.which("codex")
        codex_config = Path(os.environ.get("CODEX_HOME", home / ".codex")) / "config.toml"
        codex_data = tomllib.loads(codex_config.read_text("utf-8-sig")) if codex_config.exists() else {}
        plugin_installed = PLUGIN in codex_data.get("plugins", {})
        if plugin_installed and not codex:
            raise ValueError("Codex plugin is registered but its CLI is unavailable; remove the plugin in Codex first")
        uv_tool = Path(os.environ.get("UV_TOOL_DIR", Path(os.environ["APPDATA"]) / "uv/tools")) / NAME
        uv = shutil.which("uv")
        if uv_tool.exists() and not uv:
            raise ValueError("A uv tool installation exists; put uv on PATH to uninstall it")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FAILED: {exc}")
        return 1
    if args.dry_run:
        for path, _, _ in edits:
            print(f"Update client configuration: {path}")
        for _, executable in packages:
            print(f"Run Windows uninstaller: {executable}")
        for path in paths:
            print(f"Remove: {path}")
        if plugin_installed:
            print(f"Remove Codex plugin: {PLUGIN}")
        if uv_tool.exists():
            print(f"Remove uv tool: {NAME}")
        print("Dry run complete. Nothing changed.")
        return 0
    failures = []
    def attempt(label, action):
        try:
            action()
            return True
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            failures.append(f"{label}: {exc}")
            return False
    if plugin_installed:
        if not attempt("Codex plugin", lambda: run_command([codex, "plugin", "remove", PLUGIN])):
            print("FAILED: " + failures[0])
            return 1
        # The CLI edits config.toml; replan without overwriting its changes.
        try:
            edits = config_edits(home)
        except (OSError, ValueError) as exc:
            print(f"FAILED: {exc}")
            return 1
    for path, before, after in edits:
        attempt(str(path), lambda p=path, b=before, a=after: write_config(p, b, a))
    if uv_tool.exists():
        attempt("uv tool", lambda: run_command([uv, "tool", "uninstall", NAME]))
    for folder, executable in packages:
        attempt(str(folder), lambda f=folder, e=executable: uninstall_windows_package(f, e))
    for path in paths:
        attempt(str(path), lambda p=path: remove_path(p))
    try:
        if config_edits(home):
            failures.append("Client registrations remain; close your AI clients and rerun")
    except (OSError, ValueError) as exc:
        failures.append(str(exc))
    if failures:
        for failure in failures:
            print(f"FAILED: {failure}")
        print("Cleanup incomplete. Resolve the failures above and rerun.")
        return 1
    print("uninstalled 3dsmax-mcp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
