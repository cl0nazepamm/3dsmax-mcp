"""Glob filtering for batch import / palette tools."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")


def normalize_name_pattern(pattern: str) -> str | None:
    """Return a usable glob pattern, or None when filtering is disabled."""
    cleaned = (pattern or "").strip()
    if not cleaned or cleaned == "*":
        return None
    return cleaned


def matches_name_pattern(name: str, pattern: str | None) -> bool:
    if pattern is None:
        return True
    return fnmatch.fnmatch(name.lower(), pattern.lower())


def split_patterns(pattern: str) -> list[str]:
    """Split a comma-separated glob string into individual patterns.

    Blank entries and a bare ``*`` (match-everything) are dropped, so an
    all-wildcard pattern disables filtering instead of being matched literally.
    """
    parts = [p.strip() for p in (pattern or "").split(",")]
    return [p for p in parts if p and p != "*"]


def matches_any_pattern(name: str, patterns: list[str]) -> bool:
    """True if name matches at least one glob (case-insensitive)."""
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in patterns)


def filter_by_name_pattern(
    items: list[T],
    pattern: str,
    *,
    key: Callable[[T], str],
    exclude: str = "",
) -> tuple[list[T], int]:
    """Filter items by include/exclude globs on their key.

    ``pattern`` and ``exclude`` are each a comma-separated list of globs
    (case-insensitive). An item is kept when it matches at least one include
    (or no includes are given) AND matches none of the excludes — so excludes
    win over includes. Returns (kept, filtered_out_count).
    """
    includes = split_patterns(pattern)
    excludes = split_patterns(exclude)
    if not includes and not excludes:
        return items, 0
    kept = [
        item for item in items
        if (not includes or matches_any_pattern(key(item), includes))
        and not (excludes and matches_any_pattern(key(item), excludes))
    ]
    return kept, len(items) - len(kept)
