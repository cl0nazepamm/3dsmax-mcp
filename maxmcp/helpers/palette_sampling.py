"""Selection helpers for palette_laydown sampling modes."""

from __future__ import annotations

import random
import re
from pathlib import Path

from ..tools.material_detection import (
    _DEFAULT_CHANNEL_PATTERNS,
    _group_texture_files_for_pbr,
    scan_texture_files,
)

_PREVIEW_STEM_RE = re.compile(
    r"(^|_)(preview|previewclose|previewmaps|preview_close|preview_maps|puzzlematte)(_|$)",
    re.IGNORECASE,
)

_SAMPLE_ALIASES: dict[str, str] = {
    "first": "first",
    "alphabetical": "first",
    "alpha": "first",
    "random": "random",
    "one_per_subfolder": "one_per_subfolder",
    "one-per-subfolder": "one_per_subfolder",
    "per_subfolder": "one_per_subfolder",
    "per-subfolder": "one_per_subfolder",
    "random_per_subfolder": "random_per_subfolder",
    "random-per-subfolder": "random_per_subfolder",
    "random_per_folder": "random_per_subfolder",
    "random-per-folder": "random_per_subfolder",
}

_OVERFLOW_ALIASES: dict[str, str] = {
    "truncate": "truncate",
    "palette_only": "truncate",
    "palette-only": "truncate",
    "palette_then_library": "palette_then_library",
    "palette-then-library": "palette_then_library",
    "library": "palette_then_library",
    "palette_and_library": "palette_then_library",
}


def normalize_sample_mode(value: str) -> str:
    key = (value or "first").strip().lower()
    if key not in _SAMPLE_ALIASES:
        supported = ", ".join(sorted({v for v in _SAMPLE_ALIASES.values()}))
        raise ValueError(f"Unsupported sample_mode: {value!r}. Use one of: {supported}")
    return _SAMPLE_ALIASES[key]


def normalize_overflow_mode(value: str) -> str:
    key = (value or "truncate").strip().lower()
    if key not in _OVERFLOW_ALIASES:
        supported = ", ".join(sorted({v for v in _OVERFLOW_ALIASES.values()}))
        raise ValueError(f"Unsupported overflow_mode: {value!r}. Use one of: {supported}")
    return _OVERFLOW_ALIASES[key]


def is_preview_asset(path: Path) -> bool:
    return bool(_PREVIEW_STEM_RE.search(path.stem))


def _make_rng(random_seed: int | None) -> random.Random:
    return random.Random(random_seed)


def _prefer_pbr_groups(groups: list[dict]) -> list[dict]:
    with_core = [
        group for group in groups
        if "diffuse" in group["channels"]
        and (
            "roughness" in group["channels"]
            or "glossiness" in group["channels"]
            or "normal" in group["channels"]
            or "bump" in group["channels"]
        )
    ]
    if with_core:
        return with_core
    with_diffuse = [group for group in groups if "diffuse" in group["channels"]]
    if with_diffuse:
        return with_diffuse
    return groups


def _pick_one(items: list, *, pick_random: bool, rng: random.Random):
    if not items:
        return None
    if pick_random:
        return rng.choice(items)
    return items[0]


def _clone_group(group: dict, *, source_subfolder: str) -> dict:
    return {
        "name": str(group["name"]),
        "channels": dict(group["channels"]),
        "aliases": dict(group.get("aliases", {})),
        "source_subfolder": source_subfolder,
    }


def subfolders_with_images(root: Path, recursive: bool) -> list[Path]:
    if not root.is_dir():
        return []
    subfolders: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda path: str(path).lower()):
        if not child.is_dir():
            continue
        if scan_texture_files(str(child), recursive):
            subfolders.append(child)
    return subfolders


def collect_one_sample_per_subfolder(
    texture_folder: str,
    *,
    recursive: bool,
    pick_random: bool,
    random_seed: int | None,
    slot_content: str,
) -> tuple[list[dict] | list[Path], dict[str, object], int, int]:
    """Return one texture set or image per immediate child folder."""
    root = Path(texture_folder)
    subfolders = subfolders_with_images(root, recursive)
    if not subfolders:
        return [], {"subfolder_count": 0, "used_subfolder_sampling": False}, 0, 0

    rng = _make_rng(random_seed)
    picked: list[dict] | list[Path] = []
    sources: list[str] = []
    unmatched_total = 0
    duplicate_total = 0

    for subfolder in subfolders:
        files = [
            path for path in scan_texture_files(str(subfolder), recursive)
            if not is_preview_asset(path)
        ]
        if not files:
            continue

        if slot_content == "pbr_material":
            groups, unmatched, duplicates = _group_texture_files_for_pbr(
                files, _DEFAULT_CHANNEL_PATTERNS,
            )
            unmatched_total += len(unmatched)
            duplicate_total += len(duplicates)
            candidates = _prefer_pbr_groups(groups)
            choice = _pick_one(candidates, pick_random=pick_random, rng=rng)
            if choice is None:
                continue
            picked.append(_clone_group(choice, source_subfolder=subfolder.name))
            sources.append(subfolder.name)
        else:
            candidates = sorted(files, key=lambda path: str(path).lower())
            choice = _pick_one(candidates, pick_random=pick_random, rng=rng)
            if choice is None:
                continue
            picked.append(choice)
            sources.append(subfolder.name)

    meta: dict[str, object] = {
        "subfolder_count": len(sources),
        "used_subfolder_sampling": True,
        "source_subfolders": sources,
    }
    if random_seed is not None:
        meta["random_seed"] = random_seed
    return picked, meta, unmatched_total, duplicate_total


def split_palette_and_library(
    items: list,
    *,
    max_slots: int,
    overflow_mode: str,
) -> tuple[list, list]:
    if overflow_mode != "palette_then_library" or len(items) <= max_slots:
        return items[:max_slots], []
    return items[:max_slots], items[max_slots:]
