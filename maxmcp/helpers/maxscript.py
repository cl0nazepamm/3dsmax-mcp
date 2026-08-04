"""Shared escaping helpers for building MAXScript strings."""

import re


def safe_string(s: str) -> str:
    """Escape a Python string for embedding in a MAXScript double-quoted string literal.

    Handles backslash and double-quote — the two characters that break
    MAXScript "..." strings.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


def safe_name(s: str) -> str:
    """Escape a Python string for use in a MAXScript $'...' name selector.

    Handles backslash, double-quote, and single-quote.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def safe_value(val: str) -> str:
    """Auto-protect file paths in MAXScript value expressions.

    Detects quoted strings containing backslashes and converts them to
    MAXScript verbatim strings (@"...") so backslashes aren't interpreted
    as escape sequences (e.g. \\t becoming tab).
    """
    v = val.strip()
    if v.startswith('@"'):
        return v
    if v.startswith('"') and v.endswith('"') and '\\' in v:
        return '@' + v
    return val


def normalize_subanim_path(path: str) -> str:
    """Normalize a sub-anim path for MAXScript execute() compatibility.

    Sub-anim names from getSubAnimName may contain spaces (e.g. "Z Position",
    "X Rotation") which break MAXScript's execute("$'name'[#Z Position]").
    This normalizes tokens inside [#...] brackets:
      - Spaces → underscores
      - Lowercase

    Examples:
        "[#Transform][#Position][#Z Position]" → "[#transform][#position][#z_position]"
        "baseObject[#radius]" → "baseObject[#radius]"
        "[#transform][#position]" → "[#transform][#position]"
    """
    def _fix_token(m: re.Match) -> str:
        token = m.group(1)
        token = token.replace(" ", "_").lower()
        return f"[#{token}]"

    return re.sub(r'\[#([^\]]+)\]', _fix_token, path)
