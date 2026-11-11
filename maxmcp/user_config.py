"""The user's mcp_config.ini: reads for the server, edits for install.py and the Windows setup."""
from __future__ import annotations

import configparser
import os
import re
from pathlib import Path

# The first of each is the default.
TOOL_PROFILES = ("script", "full", "progressive", "core")
TOOL_DOCS = ("hybrid", "signature", "complete")


def configured_value(key: str, choices: tuple[str, ...]) -> str | None:
    """`[mcp] <key>` from %LOCALAPPDATA%\\3dsmax-mcp\\mcp_config.ini, if it is one of `choices`."""
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_appdata:
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(Path(local_appdata) / "3dsmax-mcp" / "mcp_config.ini", encoding="utf-8")
        value = parser.get("mcp", key, fallback="").strip().lower()
    except (OSError, configparser.Error):
        return None
    return value if value in choices else None


def setting(env_names: tuple[str, ...], key: str, choices: tuple[str, ...]) -> str:
    """An environment override, else the installed config value, else the first of `choices`."""
    for name in env_names:
        value = os.environ.get(name)
        if value is not None:
            value = value.strip().lower()
            return value if value in choices else choices[0]
    return configured_value(key, choices) or choices[0]


def with_ini_value(text: str, section: str, key: str, value: str) -> str:
    """Return INI text with one value set, preserving unrelated comments and formatting."""
    lines = text.splitlines()
    section_start: int | None = None
    section_end = len(lines)
    section_pattern = re.compile(r"^\s*\[([^]]+)]\s*(?:[#;].*)?$", re.IGNORECASE)
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=", re.IGNORECASE)

    for index, line in enumerate(lines):
        match = section_pattern.match(line)
        if not match:
            continue
        if section_start is not None:
            section_end = index
            break
        if match.group(1).strip().lower() == section.lower():
            section_start = index

    replacement = f"{key} = {value}"
    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend((f"[{section}]", replacement))
    else:
        for index in range(section_start + 1, section_end):
            if key_pattern.match(lines[index]):
                lines[index] = replacement
                break
        else:
            lines.insert(section_end, replacement)

    return "\n".join(lines) + "\n"
