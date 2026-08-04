"""smart_import: batch-import a folder of 3D assets onto a grid, auto-assigning
materials by mesh-stem prefix using the same PBR detection logic as
palette_laydown.

For each mesh file in the input folder:
  * .max         -> mergeMaxFile (no material override; the .max keeps its materials)
  * .usd/.usda/  -> importFile + (if a sibling .mtlx exists at the same stem,
    .usdc/.usdz     also importFile that .mtlx); no texture-folder material wiring
  * other meshes -> importFile + build a single PBR material from textures whose
                    stem starts with the mesh stem (longest-prefix wins), or from
                    maps co-located in the same asset folder (mesh.fbx + tex/*).

Imported assets are placed on a flat XY grid spaced by `grid_spacing` units.
Dedup: if any scene object's name starts with the mesh stem, the file is skipped.
"""

import math
import re
from pathlib import Path
from typing import Optional

from ..helpers.material_tripback import (
    unsupported_material_class_result,
    wrap_material_tool_result,
)
from ..helpers.maxscript import safe_string
from ..helpers.path_filter import filter_by_name_pattern

from ..server import mcp, client
from ._pbr_material_builder import (
    _ms_path,
    pbr_helpers_preamble_lines,
    pbr_per_group_lines,
    pbr_renderer_setup_lines,
)
from .material_detection import (
    _DEFAULT_CHANNEL_PATTERNS,
    _IMAGE_EXTENSIONS,
    _detect_texture_channel,
    _renderer_from_material_class,
    _texture_tokens,
)

_LOD_SUFFIX_RE = re.compile(r"_LOD(\d+)$", re.IGNORECASE)
_MEGASCANS_ASSET_ID_RE = re.compile(r"^([a-z0-9]+)_LOD\d+$", re.IGNORECASE)
_RESOLUTION_SCORES = {
    "16k": 16000, "8192": 8192, "8k": 8000,
    "4096": 4096, "4k": 4000,
    "2048": 2048, "2k": 2000,
    "1024": 1024, "1k": 1000,
    "512": 512,
}


# Mesh file extensions 3ds Max can import or merge. (.max → mergeMaxFile; everything
# else → importFile, which dispatches by extension to the appropriate plugin.)
_MESH_EXTENSIONS: tuple[str, ...] = (
    ".max",
    ".fbx",
    ".obj",
    ".3ds",
    ".dae",
    ".dwg",
    ".dxf",
    ".stl",
    ".ply",
    ".abc",
    ".usd",
    ".usda",
    ".usdc",
    ".usdz",
    ".glb",
    ".gltf",
    ".skp",
    ".ifc",
    ".step",
    ".stp",
    ".iges",
    ".igs",
)

_USD_EXTENSIONS = {".usd", ".usda", ".usdc", ".usdz"}


def _scan_mesh_files(folder: Path, recursive: bool) -> list[Path]:
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    files = [
        p for p in iterator
        if p.is_file() and p.suffix.lower() in _MESH_EXTENSIONS
    ]
    return sorted(files, key=lambda p: str(p).lower())


def _scan_image_files(folder: Path, recursive: bool) -> list[Path]:
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    files = [
        p for p in iterator
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
    ]
    return sorted(files, key=lambda p: str(p).lower())


def _mesh_stem_key(stem: str) -> str:
    """Token-normalized lowercase key for a mesh stem (e.g. 'WoodFloor' -> 'wood_floor')."""
    return "_".join(_texture_tokens(stem))


_ASSET_TEXTURE_DIRS = frozenset({"tex", "textures", "maps", "map", "materials"})


def _lod_mesh_asset_token(stem: str) -> str | None:
    """Asset token from ``assetid_LOD0``-style mesh names (6+ char prefix before ``_LOD``)."""
    match = _MEGASCANS_ASSET_ID_RE.match(stem)
    if match:
        candidate = match.group(1).lower()
        # Short variant names (Var1_LOD0) are not shared texture keys.
        if len(candidate) >= 6 and candidate.isalnum():
            return candidate
    return None


def _bundle_id_from_folder_name(name: str) -> str | None:
    """Trailing id from bundle folder names like ``category_label_xikkdhjja``."""
    lower = name.lower().strip()
    if re.match(r"^tmp[a-z0-9]+$", lower):
        return None
    if " " in lower:
        tail = lower.split()[-1]
        if tail.isalnum() and len(tail) >= 6:
            return tail
    parts = [part for part in lower.split("_") if part]
    if len(parts) >= 2 and parts[-1].isalnum() and len(parts[-1]) >= 6:
        return parts[-1]
    return None


def _bundle_has_texture_tree(folder: Path) -> bool:
    return any((folder / sub).is_dir() for sub in _ASSET_TEXTURE_DIRS)


def _shared_bundle_id_from_path(mesh_path: Path) -> str | None:
    """Shared texture key from mesh stem or a parent bundle folder with a texture tree."""
    stem_id = _lod_mesh_asset_token(mesh_path.stem)
    if stem_id:
        return stem_id
    for parent in (mesh_path.parent, mesh_path.parent.parent):
        folder_id = _bundle_id_from_folder_name(parent.name)
        if folder_id and _bundle_has_texture_tree(parent):
            return folder_id
    return None


def _mesh_material_key(mesh_path: Path) -> str:
    """Normalized key used to pair a mesh with texture material keys."""
    bundle_id = _shared_bundle_id_from_path(mesh_path)
    if bundle_id:
        return bundle_id
    return _mesh_stem_key(mesh_path.stem)


_GENERIC_MESH_STEMS = frozenset({
    "mesh", "model", "geo", "geometry", "asset", "low", "high",
})


def _asset_root_for_path(path: Path) -> Path:
    """Folder that owns an asset — parent of mesh, or grandparent when under tex/."""
    parent = path.parent
    if parent.name.lower() in _ASSET_TEXTURE_DIRS:
        return parent.parent
    return parent


def _path_under_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _material_name_for_mesh(mesh_path: Path) -> str:
    bundle_id = _shared_bundle_id_from_path(mesh_path)
    if bundle_id:
        return bundle_id
    if mesh_path.stem.lower() in _GENERIC_MESH_STEMS:
        return mesh_path.parent.name
    return mesh_path.stem


def _strict_mesh_matches_material_key(mesh_key: str, material_key: str) -> bool:
    if not mesh_key or not material_key:
        return False
    if material_key == mesh_key:
        return True
    if material_key.startswith(mesh_key + "_"):
        return True
    if mesh_key.startswith(material_key + "_"):
        return True
    return False


def _fuzzy_mesh_matches_material_key(mesh_key: str, material_key: str) -> bool:
    if _strict_mesh_matches_material_key(mesh_key, material_key):
        return False
    mesh_compact = mesh_key.replace("_", "")
    mat_compact = material_key.replace("_", "")
    shorter, longer = sorted((mesh_compact, mat_compact), key=len)
    return len(shorter) >= 4 and shorter in longer


def _mesh_matches_material_key(mesh_key: str, material_key: str) -> bool:
    """True when a texture material key belongs to a mesh (strict prefix or fuzzy stem)."""
    return (
        _strict_mesh_matches_material_key(mesh_key, material_key)
        or _fuzzy_mesh_matches_material_key(mesh_key, material_key)
    )


def _add_texture_to_group(
    group: dict,
    tex_path: Path,
    patterns: dict[str, list[str]],
) -> None:
    detected = _detect_texture_channel(tex_path, patterns)
    if detected is None:
        return
    channel, _, alias = detected
    existing = group["channels"].get(channel)
    if existing is None or _texture_resolution_score(tex_path) > _texture_resolution_score(existing):
        group["channels"][channel] = tex_path
        group["aliases"][channel] = alias


def _merge_material_group(existing: dict, incoming: dict) -> None:
    for channel, tex_path in incoming["channels"].items():
        prev = existing["channels"].get(channel)
        if prev is None or _texture_resolution_score(tex_path) > _texture_resolution_score(prev):
            existing["channels"][channel] = tex_path
            existing["aliases"][channel] = incoming["aliases"].get(channel, "")


def _colocated_texture_groups(
    mesh_paths: list[Path],
    texture_paths: list[Path],
    patterns: dict[str, list[str]],
) -> dict[Path, dict]:
    """Match textures co-located in the same asset folder as a sole mesh.

    Handles layouts like ``container/mesh.fbx`` + ``container/tex/albedo.png`` where
    stem prefix matching would fail.
    """
    meshes_by_root: dict[Path, list[Path]] = {}
    for mesh_path in mesh_paths:
        meshes_by_root.setdefault(_asset_root_for_path(mesh_path), []).append(mesh_path)

    groups: dict[Path, dict] = {}
    for asset_root, meshes in meshes_by_root.items():
        if len(meshes) != 1:
            continue
        mesh_path = meshes[0]
        local_textures = [t for t in texture_paths if _path_under_root(t, asset_root)]
        if not local_textures:
            continue
        group = {
            "name": _material_name_for_mesh(mesh_path),
            "channels": {},
            "aliases": {},
        }
        for tex_path in local_textures:
            _add_texture_to_group(group, tex_path, patterns)
        if group["channels"]:
            groups[mesh_path] = group
    return groups


def _texture_resolution_score(path: Path) -> int:
    return max((_RESOLUTION_SCORES.get(token, 0) for token in _texture_tokens(path.stem)), default=0)


def _filter_mesh_lods(mesh_paths: list[Path], lod_filter: str) -> tuple[list[Path], list[Path]]:
    """Return (kept, skipped) mesh paths. ``lod0`` keeps only *_LOD0 (meshes without LOD tag kept)."""
    if lod_filter.lower() in {"all", "none", ""}:
        return mesh_paths, []

    kept: list[Path] = []
    skipped: list[Path] = []
    for path in mesh_paths:
        match = _LOD_SUFFIX_RE.search(path.stem)
        if match is None:
            kept.append(path)
        elif int(match.group(1)) == 0:
            kept.append(path)
        else:
            skipped.append(path)
    return kept, skipped


def _match_textures_to_meshes(
    mesh_paths: list[Path],
    texture_paths: list[Path],
    patterns: dict[str, list[str]],
) -> dict[Path, dict]:
    """Group textures into one PBR group per mesh using longest-prefix-wins matching.

    Returns ``{mesh_path: {"name": stem, "channels": {channel: tex_path}, "aliases": {...}}}``
    for meshes that have at least one matched channel.
    """
    mesh_keys: dict[Path, str] = {p: _mesh_material_key(p) for p in mesh_paths}

    groups: dict[Path, dict] = {}
    for tex_path in texture_paths:
        detected = _detect_texture_channel(tex_path, patterns)
        if detected is None:
            continue
        channel, material_key, alias = detected

        best_mesh: Optional[Path] = None
        best_strict_len = 0
        best_fuzzy_mesh: Optional[Path] = None
        best_fuzzy_len = 0
        for mesh_path, mesh_key in mesh_keys.items():
            if _strict_mesh_matches_material_key(mesh_key, material_key):
                if len(mesh_key) > best_strict_len:
                    best_mesh = mesh_path
                    best_strict_len = len(mesh_key)
            elif _fuzzy_mesh_matches_material_key(mesh_key, material_key):
                if len(mesh_key) > best_fuzzy_len:
                    best_fuzzy_mesh = mesh_path
                    best_fuzzy_len = len(mesh_key)
        if best_mesh is None:
            best_mesh = best_fuzzy_mesh
        if best_mesh is None:
            continue

        group = groups.setdefault(best_mesh, {
            "name": _material_name_for_mesh(best_mesh),
            "channels": {},
            "aliases": {},
        })
        _add_texture_to_group(group, tex_path, patterns)

    for mesh_path, group in _colocated_texture_groups(mesh_paths, texture_paths, patterns).items():
        existing = groups.get(mesh_path)
        if existing is None:
            groups[mesh_path] = group
        else:
            _merge_material_group(existing, group)

    key_to_group: dict[str, dict] = {}
    for mesh_path, group in groups.items():
        if not group["channels"]:
            continue
        key = mesh_keys[mesh_path]
        existing = key_to_group.get(key)
        if existing is None:
            key_to_group[key] = group
        else:
            _merge_material_group(existing, group)

    shared: dict[Path, dict] = {}
    for mesh_path in mesh_paths:
        key = mesh_keys[mesh_path]
        group = key_to_group.get(key)
        if group and group["channels"]:
            shared[mesh_path] = group
    return shared


def _grid_cells(count: int, spacing: float) -> list[tuple[float, float]]:
    """Return (x, y) positions for *count* cells laid out in a roughly square XY grid."""
    if count <= 0:
        return []
    cols = max(1, int(math.ceil(math.sqrt(count))))
    cells: list[tuple[float, float]] = []
    for idx in range(count):
        col = idx % cols
        row = idx // cols
        cells.append((col * spacing, row * spacing))
    return cells


def _build_smart_import_maxscript(
    plan: list[dict],
    *,
    renderer: str,
    include_displacement: bool,
) -> str:
    """Compose the full smart_import MAXScript from a plan of import items.

    Each plan item:
        {
            "path": Path,
            "stem": str,
            "kind": "max" | "usd" | "other",
            "mtlx_path": Optional[Path],     # USD with sibling .mtlx
            "material_group": Optional[dict], # PBR group from _match_textures_to_meshes
            "grid_x": float,
            "grid_y": float,
        }
    """
    needs_uber = any(
        item.get("material_group") and "orm" in item["material_group"]["channels"]
        for item in plan
    )
    has_material = any(item.get("material_group") for item in plan)

    lines: list[str] = list(pbr_helpers_preamble_lines())
    lines.extend([
        "local imported = #()",
        "local skipped = #()",
        "local errors = #()",
        "fn mcp_sceneHasNamePrefix prefix = (",
        "    for n in objects do (",
        '        if (matchPattern n.name pattern:(prefix + "*") ignoreCase:true) do return true',
        "    )",
        "    false",
        ")",
        "fn mcp_placeOnGrid newNodes cellX cellY = (",
        "    local roots = for n in newNodes where n.parent == undefined collect n",
        "    if roots.count == 0 do return undefined",
        "    local cx = 0.0",
        "    local cy = 0.0",
        "    for n in roots do (cx += n.pos.x; cy += n.pos.y)",
        "    cx /= roots.count",
        "    cy /= roots.count",
        "    local dx = cellX - cx",
        "    local dy = cellY - cy",
        "    for n in roots do (n.pos = [n.pos.x + dx, n.pos.y + dy, n.pos.z])",
        ")",
    ])
    if has_material:
        lines.extend(pbr_renderer_setup_lines(renderer, needs_uberbitmap_osl=needs_uber))

    for idx, item in enumerate(plan, start=1):
        stem_safe = safe_string(item["stem"])
        path_literal = safe_string(_ms_path(item["path"]))
        kind = item["kind"]
        grid_x = item["grid_x"]
        grid_y = item["grid_y"]
        material_group = item.get("material_group")
        mtlx_path = item.get("mtlx_path")

        lines.append("try (")
        lines.append(f'    local assetStem = "{stem_safe}"')
        lines.append("    if (mcp_sceneHasNamePrefix assetStem) then (")
        lines.append('        append skipped (assetStem + " (already in scene)")')
        lines.append("    ) else (")
        lines.append("        local existingObjects = #()")
        lines.append("        for n in objects do append existingObjects n")
        if kind == "max":
            lines.append(
                f'        mergeMaxFile @"{path_literal}" '
                "#autoRenameDups #useSceneMtlDups #alwaysReparent quiet:true"
            )
        else:
            lines.append(f'        importFile @"{path_literal}" #noPrompt')
        if kind == "usd" and mtlx_path is not None:
            mtlx_literal = safe_string(_ms_path(mtlx_path))
            lines.append(f'        try (importFile @"{mtlx_literal}" #noPrompt) catch ()')
        lines.append("        local newNodes = #()")
        lines.append("        for n in objects do (if (findItem existingObjects n) == 0 do append newNodes n)")

        if material_group is not None:
            mat_var = f"mat_{idx}"
            mat_name = safe_string(material_group.get("name") or item["stem"])
            # pbr_per_group_lines emits 4-space-prefixed lines (designed to live inside
            # `try ( ... )` at outer-1 indent). Here they're inside `else ( ... )` at
            # outer-2 indent, so we add 4 more spaces of prefix.
            for ln in pbr_per_group_lines(
                material_group,
                idx=idx,
                mat_var=mat_var,
                mat_name=mat_name,
                renderer=renderer,
                include_displacement=include_displacement,
            ):
                lines.append("    " + ln)
            lines.append(f"        for n in newNodes do try (n.material = {mat_var}) catch ()")
            lines.append(
                f'        append imported (assetStem + " -> " + {mat_var}.name '
                '+ " [" + channelList + "] (" + (newNodes.count as string) + " nodes)")'
            )
        else:
            channel_note = (
                "USD+MaterialX" if (kind == "usd" and mtlx_path is not None)
                else "USD (no .mtlx)" if kind == "usd"
                else "no matched textures"
            )
            lines.append(
                f'        append imported (assetStem + " (" + (newNodes.count as string) '
                f'+ " nodes, {channel_note})")'
            )

        lines.append(f"        mcp_placeOnGrid newNodes {grid_x} {grid_y}")
        lines.append("    )")
        lines.append(f') catch (append errors ("{stem_safe}: " + (getCurrentException())))')

    lines.extend([
        'local msg = "smart_import: " + (imported.count as string) + " imported, " + (skipped.count as string) + " skipped"',
        'if imported.count > 0 do msg += " | Imported: [" + (imported as string) + "]"',
        'if skipped.count > 0 do msg += " | Skipped: [" + (skipped as string) + "]"',
        'if errors.count > 0 do (',
        '    msg += " | Errors: "',
        '    for i = 1 to errors.count do (',
        '        if i > 1 do msg += "; "',
        '        msg += errors[i]',
        '    )',
        ')',
        "msg",
    ])
    return "(\n    " + "\n    ".join(lines) + "\n)"


@mcp.tool()
def smart_import(
    folder: str,
    texture_folder: str = "",
    recursive: bool = True,
    material_class: str = "",
    include_displacement: bool = True,
    grid_spacing: float = 200.0,
    lod_filter: str = "lod0",
    name_pattern: str = "",
    exclude_pattern: str = "",
) -> str:
    """Batch-import 3D meshes from a folder, auto-assigning materials by stem matching.

    folder:
        Directory to scan for mesh files. Supported extensions: .max .fbx .obj .3ds
        .dae .dwg .dxf .stl .ply .abc .usd .usda .usdc .usdz .glb .gltf .skp .ifc
        .step .stp .iges .igs. .max is merged with mergeMaxFile and keeps its existing
        materials; .usd* is imported with sibling .mtlx if present; everything else
        gets a PBR material built from textures with matching names.
    texture_folder:
        Where to look for textures to wire to the imported meshes. Defaults to *folder*
        when empty. Textures are matched by longest-stem-prefix against mesh stems and
        grouped by PBR channel using the same detection patterns as palette_laydown.
        Shared-atlas assets (``assetid_LOD0.fbx`` + ``assetid_4K_Albedo.jpg``) match
        on the shared asset id; highest resolution map wins per channel. Per-asset
        folders with a generic mesh name (``mesh.fbx``) and a sibling ``tex/`` folder
        also match by co-location — no shared stem required.
    recursive:
        Scan subfolders for meshes and textures (default true — asset libraries
        often store one asset per subfolder).
    material_class:
        Renderer for auto-built PBR materials. Pass any value from tripback
        ``supported_material_classes`` / ``hint.renderers`` — OpenPBR (default),
        Physical, Arnold, Redshift, V-Ray, MaterialX, Octane variants, etc.
        Empty defaults to OpenPBR. Ignored for .max and .usd* assets.
    include_displacement:
        Wire height/displacement maps when present (default true).
    grid_spacing:
        XY spacing in scene units between imported asset centers. The grid is
        roughly square (ceil(sqrt(N)) columns).
    lod_filter:
        ``lod0`` (default) — import only ``*_LOD0`` meshes; skip ``*_LOD1+``; meshes
        without a LOD suffix are kept. ``all`` — import every LOD file found.
    name_pattern:
        Optional include glob(s) on mesh filename stems (case-insensitive).
        Comma-separated for several, e.g. ``*wood*``, ``oak_*,birch_*``, ``*_LOD0``.
        Empty or ``*`` imports every mesh.
    exclude_pattern:
        Optional exclude glob(s) on mesh stems (case-insensitive, comma-separated).
        Anything matching is skipped even if it also matched name_pattern — e.g.
        ``*_broken*`` or ``*debris*,*lowpoly*``. The "don't import that" filter.

    Dedup: a mesh file is skipped if any scene object's name starts with its stem.
    """
    root = Path(folder)
    if not root.is_dir():
        return f"Folder not found: {folder}"

    renderer = _renderer_from_material_class(material_class)
    if renderer is None:
        return unsupported_material_class_result(material_class, tool="smart_import")

    mesh_paths = _scan_mesh_files(root, recursive)
    if not mesh_paths:
        return f"No mesh files found in: {folder}"

    mesh_paths, lod_skipped = _filter_mesh_lods(mesh_paths, lod_filter)
    if not mesh_paths:
        return (
            f"No mesh files left after lod_filter={lod_filter!r} "
            f"({len(lod_skipped)} LOD1+ files skipped)"
        )

    mesh_paths, name_filtered = filter_by_name_pattern(
        mesh_paths, name_pattern, key=lambda p: p.stem, exclude=exclude_pattern,
    )
    if not mesh_paths:
        return (
            f"No mesh files matched name_pattern={name_pattern!r} / "
            f"exclude_pattern={exclude_pattern!r} ({name_filtered} filtered out)"
        )

    tex_root = Path(texture_folder) if texture_folder else root
    if not tex_root.is_dir():
        return f"Texture folder not found: {texture_folder}"
    texture_paths = _scan_image_files(tex_root, recursive)

    # USD / .max meshes never get texture-folder materials; only the "other" group does.
    material_eligible = [p for p in mesh_paths if p.suffix.lower() not in (_USD_EXTENSIONS | {".max"})]
    mesh_to_group = _match_textures_to_meshes(material_eligible, texture_paths, _DEFAULT_CHANNEL_PATTERNS)

    cells = _grid_cells(len(mesh_paths), grid_spacing)

    plan: list[dict] = []
    for (mesh_path, (gx, gy)) in zip(mesh_paths, cells):
        ext = mesh_path.suffix.lower()
        if ext == ".max":
            kind = "max"
        elif ext in _USD_EXTENSIONS:
            kind = "usd"
        else:
            kind = "other"

        mtlx_path: Optional[Path] = None
        if kind == "usd":
            candidate = mesh_path.with_suffix(".mtlx")
            if candidate.is_file():
                mtlx_path = candidate

        plan.append({
            "path": mesh_path,
            "stem": mesh_path.stem,
            "kind": kind,
            "mtlx_path": mtlx_path,
            "material_group": mesh_to_group.get(mesh_path),
            "grid_x": gx,
            "grid_y": gy,
        })

    body = _build_smart_import_maxscript(
        plan,
        renderer=renderer,
        include_displacement=include_displacement,
    )
    maxscript = f"""(
    try (
        {body}
    ) catch (
        "smart_import error: " + (getCurrentException())
    )
)"""
    response = client.send_command(maxscript)
    result = response.get("result", "")
    if lod_skipped and isinstance(result, str) and result.startswith("smart_import:"):
        result += f" | LOD skipped: {len(lod_skipped)}"
    extra: dict = {}
    if lod_skipped:
        extra["lod_skipped"] = len(lod_skipped)
    if name_filtered:
        extra["name_pattern"] = name_pattern
        if exclude_pattern:
            extra["exclude_pattern"] = exclude_pattern
        extra["name_filtered_out"] = name_filtered
    return wrap_material_tool_result(
        str(result),
        material_class=material_class,
        renderer=renderer,
        tool="smart_import",
        **extra,
    )
