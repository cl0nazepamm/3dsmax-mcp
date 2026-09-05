"""Deterministic linear-time checks on an indexed triangle snapshot.

Topology is defined by vertex indices, not rounded positions. This deliberately
does not weld the mesh or infer intended solidity, normals, or intersections.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


def analyze_triangles(
    vertices: list[list[float]],
    faces: list[list[int]],
    *,
    limit: int = 12,
    area_epsilon: float = 0.0,
) -> dict[str, Any]:
    """Return complete counts and bounded samples; faces contain 1-based IDs."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    if (isinstance(area_epsilon, bool) or not isinstance(area_epsilon, (int, float))
            or not math.isfinite(area_epsilon) or area_epsilon < 0):
        raise ValueError("area_epsilon must be a nonnegative finite number")
    for p in vertices:
        if (not isinstance(p, (list, tuple)) or len(p) != 3
                or any(isinstance(v, bool) or not isinstance(v, (float, int))
                       or not math.isfinite(v) for v in p)):
            raise ValueError("Snapshot contains invalid or non-finite vertex coordinates")
    for face in faces:
        if (not isinstance(face, (list, tuple)) or len(face) != 3
                or any(isinstance(v, bool) or not isinstance(v, int)
                       or not 1 <= v <= len(vertices) for v in face)):
            raise ValueError("Snapshot contains invalid triangle vertex indices")

    bounds = None
    if vertices:
        low = [min(p[a] for p in vertices) for a in range(3)]
        high = [max(p[a] for p in vertices) for a in range(3)]
        bounds = {"min": low, "max": high}
        # Translation independent; Max's floating point coordinates do not
        # warrant treating tiny triangles at arbitrary scale as exact geometry.
        automatic_epsilon = max(math.dist(low, high) ** 2 * 1e-12, 1e-24)
    else:
        automatic_epsilon = 1e-24
    epsilon = float(area_epsilon) if area_epsilon > 0 else automatic_epsilon

    issues: dict[str, dict[str, Any]] = {
        key: {"count": 0, "samples": []}
        for key in ("boundary_edges", "non_manifold_edges", "winding_conflicts",
                    "degenerate_faces", "duplicate_faces", "isolated_vertices")
    }

    def record(kind: str, row: dict[str, Any]) -> None:
        bucket = issues[kind]
        bucket["count"] += 1
        if len(bucket["samples"]) < limit:
            bucket["samples"].append(row)

    def center(ids: tuple[int, ...] | list[int]) -> list[float]:
        return [math.fsum(vertices[i - 1][a] / len(ids) for i in ids) for a in range(3)]

    parents = list(range(len(faces)))
    sizes = [1] * len(faces)

    def root(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = root(a), root(b)
        if ra != rb:
            if sizes[ra] < sizes[rb]:
                ra, rb = rb, ra
            parents[rb] = ra
            sizes[ra] += sizes[rb]

    edges: dict[tuple[int, int], list[tuple[int, bool]]] = defaultdict(list)
    first_triangle: dict[tuple[int, ...], int] = {}
    used: set[int] = set()
    degenerate: set[int] = set()
    areas: list[float] = []
    for fid, face in enumerate(faces, 1):
        used.update(face)
        a, b, c = (vertices[v - 1] for v in face)
        ab = [b[k] - a[k] for k in range(3)]
        ac = [c[k] - a[k] for k in range(3)]
        cross = [ab[1] * ac[2] - ab[2] * ac[1],
                 ab[2] * ac[0] - ab[0] * ac[2],
                 ab[0] * ac[1] - ab[1] * ac[0]]
        area = math.hypot(*cross) * 0.5
        areas.append(area)
        if len(set(face)) != 3 or area <= epsilon:
            degenerate.add(fid)
            record("degenerate_faces", {
                "face": fid, "vertices": list(face), "center": center(face),
                "area": area,
                "reason": "repeated_vertex" if len(set(face)) != 3 else "area_at_or_below_epsilon",
            })
        signature = tuple(sorted(face))
        if signature in first_triangle:
            record("duplicate_faces", {"face": fid, "other_face": first_triangle[signature],
                                       "vertices": list(face), "center": center(face)})
        else:
            first_triangle[signature] = fid
        face_edges = set()
        for u, v in zip(face, [*face[1:], face[0]]):
            if u == v:
                continue
            key = (min(u, v), max(u, v))
            # Repeated vertices can traverse one edge twice in the same face.
            # Incidence counts must remain distinct-face counts.
            if key not in face_edges:
                edges[key].append((fid, u < v))
                face_edges.add(key)

    component_open_faces: set[int] = set()
    component_non_manifold_faces: set[int] = set()
    component_winding_faces: set[int] = set()
    for pair, incident in edges.items():
        for fid, _ in incident[1:]:
            union(incident[0][0] - 1, fid - 1)
        # Bound an individual sample even for pathological edge fans.
        row = {"vertices": list(pair), "center": center(pair),
               "faces": [fid for fid, _ in incident[:limit]], "face_count": len(incident)}
        if len(incident) == 1:
            record("boundary_edges", row)
            component_open_faces.add(incident[0][0])
        elif len(incident) > 2:
            record("non_manifold_edges", row)
            component_non_manifold_faces.update(fid for fid, _ in incident)
        elif (incident[0][1] == incident[1][1]
              and not any(fid in degenerate for fid, _ in incident)):
            record("winding_conflicts", row)
            component_winding_faces.update(fid for fid, _ in incident)

    components: dict[int, dict[str, Any]] = {}
    for fid, area in enumerate(areas, 1):
        component_id = root(fid - 1)
        if component_id not in components:
            components[component_id] = {
                "first_face": fid, "sample_center": center(faces[fid - 1]),
                "faces": 0, "area": 0.0,
                "has_boundary": False, "has_non_manifold_edge": False,
                "has_winding_conflict": False, "has_degenerate_face": False,
            }
        component = components[component_id]
        component["faces"] += 1
        component["area"] += area
        component["has_boundary"] |= fid in component_open_faces
        component["has_non_manifold_edge"] |= fid in component_non_manifold_faces
        component["has_winding_conflict"] |= fid in component_winding_faces
        component["has_degenerate_face"] |= fid in degenerate
    ordered_components = sorted(components.values(), key=lambda row: row["first_face"])
    for vid, p in enumerate(vertices, 1):
        if vid not in used:
            record("isolated_vertices", {"vertex": vid, "point": list(p)})
    for bucket in issues.values():
        bucket["truncated"] = bucket["count"] > len(bucket["samples"])

    return {
        "counts": {"vertices": len(vertices), "triangles": len(faces), "unique_edges": len(edges),
                   "edge_connected_components": len(components)},
        "bounds": bounds,
        "empty": not faces,
        "area": math.fsum(areas),
        "area_epsilon": epsilon,
        "area_epsilon_mode": "explicit" if area_epsilon > 0 else "bbox_diagonal_squared_times_1e-12",
        "issues": issues,
        "components": {"count": len(components), "samples": ordered_components[:limit],
                       "truncated": len(components) > limit,
                       "connectivity": "shared vertex-index edge; isolated vertices excluded"},
    }
