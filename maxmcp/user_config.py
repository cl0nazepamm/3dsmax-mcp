"""Edits to the user's mcp_config.ini shared by install.py and the Windows setup."""
from __future__ import annotations

import re

TOOL_PROFILES = ("full", "progressive", "core")


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
