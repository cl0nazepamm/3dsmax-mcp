"""Read-only image-to-base-cage targeting with view and mesh consistency guards."""
from __future__ import annotations

import math
from typing import Any

from ..coerce import DictValue
from ..helpers.component_pick import component_distance, surface_distance
from ..helpers.mesh import integer, selection_script
from ..server import mcp


_COMPONENT_LIMIT = 1000
_VERTEX_LIMIT = 10000
_CHUNK_SIZE = 1000  # Also works with installed GUPs whose project cap is 1000.


# Eager tool discovery can import this module while mesh_ops or viewport is
# still importing server.py. Resolve their callables only after registration.
def inspect_mesh(**kwargs):
    from .mesh_ops import inspect_mesh as inspect
    return inspect(**kwargs)


def agent_viewport(**kwargs):
    from .viewport import agent_viewport as viewport
    return viewport(**kwargs)


def _same_mesh(reference, other):
    if (other.get("mesh_token") != reference["mesh_token"] or
        other.get("handle") != reference["handle"] or
        other.get("counts") != reference["counts"]):
        raise RuntimeError("STALE_MESH: mesh changed during targeting; capture and pick again")


def _same_view(reply, expected, width=None, height=None):
    if reply.get("view_token") != expected:
        raise RuntimeError("STALE_VIEW: capture again before targeting")
    if width is not None and (reply.get("width") != width or reply.get("height") != height):
        raise RuntimeError("STALE_VIEW: viewport dimensions changed during targeting")


@mcp.tool()
def pick_component(
    x: float,
    y: float,
    expected_view: str,
    name: str = "",
    handle: int = 0,
    level: str = "edge",
    selection: DictValue | None = None,
    tolerance: float = 0.025,
    limit: int = 5,
    prefer_surface: bool = True,
) -> dict[str, Any]:
    """Target Editable Poly base-cage IDs from an AGENT VIEWPORT capture.

    x/y: normalized image coordinates, top-left origin. expected_view is the
    single capture's view_token. With no name/handle, the nearest surface hit
    chooses the node; explicit targets also support silhouettes and cage work.
    level: vertex | edge | face. selection uses inspect_mesh filters. tolerance
    is a fraction of the shorter image side (0..0.25); limit is 1..100 results.
    Screen tolerance uses full segments and face polygons, including interiors.
    prefer_surface=true ranks matches by world distance to the cursor's surface
    hit when it belongs to this node; false ranks screen distance first. This
    helps separate a visible rim from hidden cage edges that project over it.
    Returned IDs use mesh_edit expected_mesh=result.mesh_token.
    Nothing is selected, moved, converted or added to either viewport.

    Projection does not prove component visibility. Front/back and overlapping
    candidates can coincide; modifiers may move the visible surface away from
    the base cage. Nonplanar face-distance estimates are marked approximate.
    Inspect candidates and surface evidence before editing.
    Reads at most 1000 filtered components and 10000 unique vertices; incomplete
    inspection, unprojectable geometry and output truncation are explicit.
    """
    for key, value in (("x", x), ("y", y)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{key} must be a finite normalized coordinate in 0..1")
    if not isinstance(expected_view, str) or not expected_view.strip():
        raise ValueError("expected_view must be the capture's nonempty view_token")
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    integer(handle, "handle", low=0, high=2**63 - 1)
    integer(limit, "limit", high=100)
    if type(prefer_surface) is not bool:
        raise ValueError("prefer_surface must be a boolean")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or not 0 <= tolerance <= 0.25:
        raise ValueError("tolerance must be a finite fraction in 0..0.25")
    if not isinstance(level, str):
        raise ValueError("level must be vertex, edge, or face")
    level = level.strip().lower()
    selection_script(selection, level, default_all=True)  # Validate before any bridge call.

    surface = agent_viewport(action="pick", x=x, y=y, expected_view=expected_view)
    _same_view(surface, expected_view)
    width, height = surface.get("width"), surface.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width < 2 or height < 2:
        raise RuntimeError("Invalid native viewport dimensions")
    hit = surface.get("hit")
    automatic = not name and not handle
    if automatic and not hit:
        return {"status": "no_surface_hit", "view_token": expected_view,
            "image": {"x": x, "y": y, "width": width, "height": height},
            "candidates": [], "surface_hit": None, "complete": True,
            "truncated": False, "ambiguous": False,
            "hint": "Pass an explicit name or handle to target a silhouette or base cage."}
    if automatic:
        name, handle = hit["name"], int(hit["handle"])

    mesh = inspect_mesh(name=name, handle=handle, level=level, selection=selection, limit=_COMPONENT_LIMIT)
    components = mesh["components"]
    target = {"name": mesh["name"], "handle": mesh["handle"]}
    matches_surface = bool(hit and int(hit["handle"]) == mesh["handle"])
    required = sorted({v for component in components for v in component["vertices"]})
    ids = required[:_VERTEX_LIMIT]
    positions = {}
    if level == "vertex":
        positions = {row["id"]: row["center"] for row in components}
    else:
        for offset in range(0, len(ids), _CHUNK_SIZE):
            chunk = ids[offset:offset + _CHUNK_SIZE]
            vertices = inspect_mesh(**target, level="vertex", selection={"indices": chunk}, limit=_CHUNK_SIZE)
            _same_mesh(mesh, vertices)
            rows = vertices["components"]
            if vertices.get("truncated") or {row["id"] for row in rows} != set(chunk):
                raise RuntimeError("Incomplete base vertex readback; capture and pick again")
            positions.update({row["id"]: row["center"] for row in rows})

    projected = {}
    depths = {}
    for offset in range(0, len(ids), _CHUNK_SIZE):
        chunk = ids[offset:offset + _CHUNK_SIZE]
        reply = agent_viewport(action="project", points=[positions[i] for i in chunk], expected_view=expected_view)
        _same_view(reply, expected_view, width, height)
        pixels = reply.get("pixels")
        if not isinstance(pixels, list) or len(pixels) != len(chunk):
            raise RuntimeError("Incomplete native projection readback")
        details = reply.get("projections")
        for index, (vertex_id, pixel) in enumerate(zip(chunk, pixels)):
            if pixel is not None and (not isinstance(pixel, (list, tuple)) or len(pixel) != 2 or
                any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in pixel)):
                raise RuntimeError("Invalid native projection coordinates")
            projected[vertex_id] = pixel
            if isinstance(details, list) and len(details) == len(chunk):
                detail = details[index]
                if detail.get("in_front") is False:
                    projected[vertex_id] = None
                depth = detail.get("depth")
                if isinstance(depth, (int, float)) and math.isfinite(depth):
                    depths[vertex_id] = depth
    if not ids:
        # Even an empty filter must not return a result from a now-stale image.
        _same_view(agent_viewport(action="ray", x=x, y=y, expected_view=expected_view), expected_view, width, height)

    point = [x * (width - 1), y * (height - 1)]
    shorter = min(width, height)
    threshold = tolerance * shorter
    ranked = []
    unprojectable = 0
    for row in components:
        vertex_ids = row["vertices"]
        pixels = [projected.get(i) for i in vertex_ids]
        required_count = {"vertex": 1, "edge": 2, "face": 3}[level]
        if len(pixels) < required_count or any(p is None for p in pixels):
            unprojectable += 1
            continue
        distance, closest = component_distance(point, pixels, level)
        if distance > threshold:
            continue
        candidate = {"id": row["id"], "level": level, "vertices": vertex_ids,
            "center": row["center"], "distance_pixels": distance,
            "distance_normalized": distance / shorter,
            "closest_image": [closest[0] / (width - 1), closest[1] / (height - 1)]}
        alignment = 0.0
        hit_distance = 0.0
        if "normal" in row:
            candidate["normal"] = row["normal"]
        if matches_surface:
            hit_distance, closest_world, approximate = surface_distance(
                hit["point"], [positions[i] for i in vertex_ids], level)
            candidate["surface_distance"] = hit_distance
            candidate["closest_world_to_surface_hit"] = closest_world
            candidate["surface_distance_approximate"] = approximate
            if "normal" in row and "normal" in hit:
                alignment = sum(a * b for a, b in zip(row["normal"], hit["normal"]))
                candidate["normal_alignment_to_surface_hit"] = alignment
        if all(i in depths for i in vertex_ids):
            candidate["vertex_depth_range"] = [min(depths[i] for i in vertex_ids), max(depths[i] for i in vertex_ids)]
        key = (hit_distance, distance, -alignment, row["id"]) if prefer_surface and matches_surface else (
            distance, hit_distance, -alignment, row["id"])
        ranked.append((key, candidate))
    ranked.sort(key=lambda entry: entry[0])
    inspection_truncated = bool(mesh.get("truncated")) or len(required) > _VERTEX_LIMIT
    candidate_truncated = len(ranked) > limit
    limitations = ["Surface correspondence is inferred from base geometry; proximity does not prove component visibility."]
    if mesh.get("modifiers_above", 0):
        limitations.append("Modifiers are present: the visible surface can differ from this Editable Poly base cage.")
    if not matches_surface:
        limitations.append("The cursor's nearest surface hit does not belong to the explicit target, or there is no surface hit.")
    if unprojectable:
        limitations.append("Components with missing or behind-eye vertices were omitted; near-plane crossing polygons are not clipped.")
    if any(entry[1].get("surface_distance_approximate") for entry in ranked):
        limitations.append("Nonplanar or degenerate face distances use an approximate plane/boundary correspondence.")
    return {"status": "candidates" if ranked else "no_component_in_tolerance", **target,
        "level": level, "cage": "base", "mesh_token": mesh["mesh_token"], "view_token": expected_view,
        "modifiers_above": mesh.get("modifiers_above", 0), "instance_count": mesh.get("instance_count", 1),
        "image": {"x": x, "y": y, "width": width, "height": height},
        "tolerance_pixels": threshold, "surface_hit": hit, "surface_matches_target": matches_surface,
        "candidates": [entry[1] for entry in ranked[:limit]], "matched_candidates": len(ranked),
        "ambiguous": len(ranked) > 1, "visibility_checked": False,
        "ranking": "surface distance within screen tolerance" if prefer_surface and matches_surface else "screen distance",
        "complete": not inspection_truncated and not candidate_truncated and unprojectable == 0,
        "truncated": inspection_truncated or candidate_truncated,
        "inspection_truncated": inspection_truncated, "candidates_truncated": candidate_truncated,
        "scope": {"matched_components": mesh.get("matched", len(components)),
            "inspected_components": len(components), "required_vertices": len(required),
            "vertices_read": len(positions), "omitted_unprojectable_components": unprojectable,
            "component_limit": _COMPONENT_LIMIT, "vertex_limit": _VERTEX_LIMIT},
        "limitations": limitations}
