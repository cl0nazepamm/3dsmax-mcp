"""Texture file detection helpers shared by material tools."""

import re
from pathlib import Path


_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr",
    ".tga", ".hdr", ".bmp", ".dds", ".tx",
}

# Channel patterns are priority-ordered. They intentionally cover verbose,
# short-form, and single-letter token styles used by common texture libraries.
_DEFAULT_CHANNEL_PATTERNS: dict[str, list[str]] = {
    "diffuse":       [
        "_basecolor", "_base_color", "basecolor", "base color", "_albedo", "albedo",
        "_diffuse", "diffuse", "_diff", "diff", "_color", "color", "_col", "col",
        "_rgb", "rgb", "_clr", "clr", "_alb", "alb", "_dif", "dif", "_d",
    ],
    "orm":           [
        "_occlusionroughnessmetallic", "occlusion roughness metallic",
        "_ambientocclusionroughnessmetallic", "ambient occlusion roughness metallic",
        "_orm", "orm", "_arm", "arm",
    ],
    "ao":            [
        "_ambientocclusion", "ambient occlusion", "_ambient_occlusion",
        "_occlusion", "occlusion", "_amb_occ", "amb occ", "_ao", "ao",
    ],
    "roughness":     ["_roughness", "roughness", "_rough", "rough", "_rgh", "rgh", "_r"],
    "glossiness":    [
        "_glossiness", "glossiness", "_smoothness", "smoothness", "_gloss", "gloss",
        "_smooth", "smooth", "_gls", "gls", "_g",
    ],
    "metallic":      [
        "_metallic", "metallic", "_metalness", "metalness", "_metal", "metal",
        "_met", "met", "_mtl", "mtl", "_m",
    ],
    "normal":        [
        "_normalgl", "normalgl", "normal gl", "_normaldx", "normaldx", "normal dx",
        "_normal", "normal", "_norm", "norm", "_nrm", "nrm", "_nor", "nor", "_n",
    ],
    "displacement":  [
        "_displacement", "displacement", "_displace", "displace", "_height", "height",
        "_depth", "depth", "_hght", "hght", "_hgt", "hgt", "_disp", "disp", "_dis", "dis", "_h",
    ],
    "bump":          ["_bump", "bump", "_bmp", "bmp", "_b"],
    "opacity":       [
        "_opacity", "opacity", "_alpha", "alpha", "_alphamasked", "alphamasked",
        "_opa", "opa", "_alph", "alph", "_o",
    ],
    "emission":      [
        "_emissive", "emissive", "_emission", "emission", "_emisive", "emisive",
        "_illumination", "illumination", "_illum", "illum", "_emit", "emit",
        "_light", "light", "_emi", "emi", "_ill", "ill", "_lght", "lght", "_e",
    ],
    "translucency":  [
        "_translucency", "translucency", "_translucent", "translucent",
        "_transmission", "transmission", "_transparency", "transparency",
        "_transparancy", "transparancy", "_trans", "trans", "_trns", "trns", "_t",
    ],
    "ior":           ["_ior", "ior", "_i"],
    "specular":      [
        "_specular", "specular", "_spec", "spec", "_spc", "spc",
        "_reflection", "reflection", "_reflect", "reflect", "_refl", "refl", "_ref", "ref", "_s",
    ],
}

_TEXTURE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_COMMON_VARIANT_TOKENS = {
    "2k", "4k", "8k", "16k", "1k", "512", "1024", "2048", "4096", "8192",
    "png", "jpg", "jpeg", "tif", "tiff", "exr", "tga", "hdr", "bmp", "dds", "tx",
    "map", "tex", "texture", "textures",
}
_NORMAL_VARIANT_TOKENS = {
    "gl", "ogl", "opengl", "open", "dx", "directx",
    "normalmap", "normalbump",
}

_COLOR_CHANNELS = {"diffuse", "specular", "emission"}


def scan_texture_files(folder: str, recursive: bool = True) -> list[Path]:
    """Return image files under *folder*, optionally scanning subfolders."""
    root = Path(folder)
    if not root.is_dir():
        return []

    iterator = root.rglob("*") if recursive else root.iterdir()
    files = [
        path for path in iterator
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
    ]
    return sorted(files, key=lambda path: str(path).lower())


def _scan_texture_folder(folder: str) -> list[Path]:
    """Return all image files in *folder* (non-recursive)."""
    p = Path(folder)
    if not p.is_dir():
        return []
    return [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS]


def _texture_tokens(value: str) -> list[str]:
    """Split a texture stem or alias into normalized name tokens."""
    return _TEXTURE_TOKEN_RE.findall(value.lower())


def _pattern_match_score(
    stem: str,
    pattern: str,
) -> tuple[int, tuple[int, int] | None] | None:
    """Return a match score and token span for a channel pattern.

    Exact token-sequence matches are preferred. Compact suffix matching exists
    for filenames like ``woodBaseColor`` but gets a lower score and no span.
    """
    tokens = _texture_tokens(stem)
    pattern_tokens = _texture_tokens(pattern)
    if not tokens or not pattern_tokens:
        return None

    pattern_compact = "".join(pattern_tokens)
    token_count = len(pattern_tokens)

    for start in range(0, len(tokens) - token_count + 1):
        if tokens[start:start + token_count] != pattern_tokens:
            continue
        at_end = start + token_count == len(tokens)
        # Single-letter aliases are useful, but they should not outrank normal
        # production naming like basecolor/roughness/metalness.
        single_letter_penalty = 80 if len(pattern_compact) == 1 else 0
        score = (token_count * 100) + len(pattern_compact) + (25 if at_end else 0) - single_letter_penalty
        return score, (start, start + token_count)

    stem_compact = "".join(tokens)
    if len(pattern_compact) >= 4 and stem_compact.endswith(pattern_compact):
        return len(pattern_compact), None

    return None


def _detect_texture_channel(
    path: Path,
    patterns: dict[str, list[str]],
) -> tuple[str, str, str] | None:
    """Return ``(channel, material_key, alias)`` for a texture filename."""
    stem = path.stem
    tokens = _texture_tokens(stem)
    best: tuple[int, int, str, tuple[int, int] | None, str] | None = None

    for priority, (channel, aliases) in enumerate(patterns.items()):
        for alias in aliases:
            scored = _pattern_match_score(stem, alias)
            if scored is None:
                continue
            score, span = scored
            candidate = (score, -priority, channel, span, alias)
            if best is None or candidate > best:
                best = candidate

    if best is None:
        return None

    _, _, channel, span, alias = best
    if span is not None:
        start, end = span
        if channel in {"normal", "bump"}:
            while end < len(tokens) and tokens[end] in _NORMAL_VARIANT_TOKENS:
                end += 1
            while start > 0 and tokens[start - 1] in _NORMAL_VARIANT_TOKENS:
                start -= 1
        key_tokens = tokens[:start] + tokens[end:]
    else:
        key_tokens = tokens

    key_tokens = [token for token in key_tokens if token not in _COMMON_VARIANT_TOKENS]
    material_key = "_".join(key_tokens).strip("_")
    if not material_key:
        material_key = path.parent.name.lower() or path.stem.lower()

    return channel, material_key, alias


def _match_textures_to_channels(
    files: list[Path],
    patterns: dict[str, list[str]],
) -> dict[str, Path]:
    """Match texture files to PBR channels using suffix patterns.

    Longest match wins. Each file is claimed by at most one channel.
    Roughness takes priority over glossiness (dict ordering).
    """
    matched: dict[str, Path] = {}
    for f in files:
        detected = _detect_texture_channel(f, patterns)
        if detected is None:
            continue
        channel, _, _ = detected
        if channel not in matched:
            matched[channel] = f

    return matched


def _group_texture_files_for_pbr(
    files: list[Path],
    patterns: dict[str, list[str]],
) -> tuple[list[dict], list[Path], list[str]]:
    """Group texture files into material sets using channel name detection."""
    grouped: dict[str, dict[str, Path]] = {}
    aliases: dict[str, dict[str, str]] = {}
    unmatched: list[Path] = []
    duplicate_notes: list[str] = []

    for path in files:
        detected = _detect_texture_channel(path, patterns)
        if detected is None:
            unmatched.append(path)
            continue

        channel, material_key, alias = detected
        channels = grouped.setdefault(material_key, {})
        aliases.setdefault(material_key, {})

        if channel in channels:
            duplicate_notes.append(f"{path.name} duplicate {channel} for {material_key}")
            continue

        channels[channel] = path
        aliases[material_key][channel] = alias

    groups = [
        {"name": name, "channels": channels, "aliases": aliases.get(name, {})}
        for name, channels in grouped.items()
        if channels
    ]
    groups.sort(key=lambda item: item["name"])
    return groups, unmatched, duplicate_notes


def _renderer_from_material_class(material_class: str) -> str | None:
    class_lower = (material_class or "").strip().lower()
    if not class_lower or class_lower in {"openpbr", "openpbrmaterial", "openpbr_material", "openpbr_mtl"}:
        return "openpbr"
    if class_lower in {"materialx", "material_x", "mtlx", "openpbr_materialx", "openpbr+materialx"} or "materialx" in class_lower:
        return "materialx"
    # Octane variants: match before generic substring checks below.
    if (class_lower in {"open_pbr_surf__mtl", "open_pbr_surf_mtl", "octanepbr", "octane_pbr",
                        "octane_openpbr", "octane_open_pbr", "octaneopenpbr"}
            or "open_pbr_surf" in class_lower
            or "openpbrsurf" in class_lower):
        return "octane_pbr"
    if (class_lower in {"universal_material", "octaneuniversal", "octane_universal",
                        "octane_universal_material"}
            or "universal_material" in class_lower):
        return "octane_universal"
    if (class_lower in {"octane", "octane_standard", "octane_std", "octanestd",
                        "octanestandard", "octane_std_surface", "octanestdsurface",
                        "std_surface_mtl", "std_surface", "octane_surface"}
            or "std_surface_mtl" in class_lower
            or class_lower.startswith("octane")):
        return "octane_standard"
    if class_lower in {"physical", "physicalmaterial", "autodeskphysical"} or "physical" in class_lower:
        return "physical"
    if class_lower in {"arnold", "ai_standard_surface", "standard_surface"} or "ai_standard" in class_lower:
        return "arnold"
    if class_lower in {"redshift", "rs_standard_material", "rsstandardmaterial"} or "redshift" in class_lower:
        return "redshift"
    if class_lower in {"vray", "v-ray", "vraymtl", "v_ray_mtl", "vray_mtl"} or "vray" in class_lower:
        return "vray"
    return None
