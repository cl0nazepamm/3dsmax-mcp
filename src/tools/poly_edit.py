"""Vertex-level poly editing: read, move (with soft falloff), set, and conform.

Operates on the Editable_Poly base object in world space (polyop with node:,
verified world-space round trip).  `conform` is the shape-fitting workhorse:
pull vertices onto a target spline (closest point, optional axis mask) or
ray-project them onto target geometry — draw the reference silhouette with
draw_spline, then conform the profile verts to it.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from ..coerce import FloatList, IntList
from ..helpers.maxscript import safe_string
from ..server import client, mcp

MAX_GET = 2000
MAX_INDICES = 5000
AXIS_VECTORS = {
    "x": ("[1,0,0]", True),
    "y": ("[0,1,0]", True),
    "z": ("[0,0,1]", True),
    "+x": ("[1,0,0]", False),
    "+y": ("[0,1,0]", False),
    "+z": ("[0,0,1]", False),
    "-x": ("[-1,0,0]", False),
    "-y": ("[0,-1,0]", False),
    "-z": ("[0,0,-1]", False),
}


def _err(message: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "error", "error": message}
    if extra:
        out["details"] = extra
    return out


def _run(script: str) -> str:
    return str(client.send_command(script).get("result", ""))


def _p3_lit(p: list[float]) -> str:
    return f"[{p[0]},{p[1]},{p[2]}]"


def _normalize_positions(positions: Any) -> tuple[list[list[float]], str]:
    """[x,y,z] triples from: JSON string, list of 3-lists, or flat number list."""
    if isinstance(positions, str):
        try:
            positions = json.loads(positions)
        except (ValueError, TypeError):
            return [], "positions is a string but not valid JSON"
    if not isinstance(positions, (list, tuple)) or not positions:
        return [], "positions must be a non-empty list"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in positions):
        if len(positions) % 3 != 0:
            return [], f"flat positions length {len(positions)} is not divisible by 3"
        positions = [list(positions[i : i + 3]) for i in range(0, len(positions), 3)]
    out: list[list[float]] = []
    for i, item in enumerate(positions):
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            return [], f"positions[{i}] must be [x,y,z]"
        try:
            out.append([float(v) for v in item])
        except (TypeError, ValueError):
            return [], f"positions[{i}] has non-numeric values"
    return out, ""


def _poly_guard(safe: str, convert: bool) -> str:
    convert_branch = (
        "(convertTo obj Editable_Poly; wasConverted = true; \"\")"
        if convert
        else '"__ERROR__|base object is not Editable_Poly (class: " + ((classof obj.baseobject) as string) + ") -- pass convert=true to collapse, or collapse_modifier_stack first"'
    )
    return (
        f'local obj = getNodeByName "{safe}"\n'
        f'if obj == undefined then "__ERROR__|object not found: {safe}" else (\n'
        "local wasConverted = false\n"
        "local guardMsg = if classof obj.baseobject != Editable_Poly then (\n"
        f"    {convert_branch}\n"
        ') else ""\n'
        'if guardMsg != "" then guardMsg else (\n'
    )


def _indices_literal(indices: list[int]) -> str:
    return "#(" + ", ".join(str(i) for i in indices) + ")"


@mcp.tool()
def edit_vertices(
    action: str,
    name: str = "",
    indices: Optional[IntList] = None,
    offset: Optional[FloatList] = None,
    positions: Any = None,
    target: str = "",
    axes: str = "xyz",
    axis: str = "",
    strength: float = 1.0,
    falloff: float = 0.0,
    spline_index: int = 1,
    in_bbox: Optional[FloatList] = None,
    near_point: Optional[FloatList] = None,
    radius: float = 0.0,
    max_verts: int = 500,
    convert: bool = False,
) -> Any:
    """Read and manipulate Editable_Poly vertices in world space.

    Actions:
    - get: vertex positions — filter by `indices`, `in_bbox` [minx,miny,minz,
      maxx,maxy,maxz], or `near_point` + `radius`; capped at max_verts.
    - move: translate `indices` (all verts if omitted) by `offset` [x,y,z].
      falloff>0 also drags surrounding verts with (1-d/r)^2 soft weighting.
    - set: place `indices` at explicit `positions` (parallel lists).
    - conform: pull `indices` (all if omitted) onto `target` — a spline (closest
      point on curve `spline_index`) or geometry (ray-cast along `axis`:
      x|y|z casts both ways, +x/-x/... one way). `axes` masks which world axes
      the displacement applies to (e.g. "xz" fits a silhouette while preserving
      width); `strength` is the fraction moved (1.0 = all the way).

    Edits hit the Editable_Poly BASE object beneath any modifiers (cage editing —
    TurboSmooth etc. update live). Non-poly bases: pass convert=true (collapses
    the stack) or use collapse_modifier_stack first.

    Use when: fitting geometry to a reference profile, shaping after blockout,
    fixing silhouettes, targeted vertex surgery no modifier expresses.
    Not when: uniform deformations (Bend/Taper/FFD via add_modifier) or
    sub-object face/edge ops (Edit_Poly workflows).
    """
    action = action.strip().lower()
    if not name:
        return _err("name is required")
    safe = safe_string(name)
    idx_list = [int(i) for i in (indices or []) if int(i) > 0]
    if len(idx_list) > MAX_INDICES:
        return _err(f"{len(idx_list)} indices exceeds the {MAX_INDICES} cap")
    guard = _poly_guard(safe, convert)
    tail = "\n)\n)"

    if action == "get":
        cap = max(1, min(int(max_verts), MAX_GET))
        filter_lines: list[str] = []
        if in_bbox is not None:
            bb = [float(v) for v in in_bbox]
            if len(bb) != 6:
                return _err("in_bbox must be [minx,miny,minz,maxx,maxy,maxz]")
            filter_lines.append(
                f"if p.x < {bb[0]} or p.y < {bb[1]} or p.z < {bb[2]} "
                f"or p.x > {bb[3]} or p.y > {bb[4]} or p.z > {bb[5]} do keep = false"
            )
        if near_point is not None:
            np3 = [float(v) for v in near_point]
            if len(np3) != 3 or radius <= 0:
                return _err("near_point needs [x,y,z] plus a positive radius")
            filter_lines.append(f"if (distance p {_p3_lit(np3)}) > {radius} do keep = false")
        loop_src = (
            f"local idxs = {_indices_literal(idx_list)}"
            if idx_list
            else "local idxs = for i = 1 to nv collect i"
        )
        script = f"""(
{guard}
    local out = stringstream ""
    local nv = polyop.getNumVerts obj
    {loop_src}
    local matched = 0
    local emitted = 0
    for i in idxs do (
        if i <= nv do (
            local p = polyop.getVert obj i node:obj
            local keep = true
            {chr(10).join("            " + fl for fl in filter_lines)}
            if keep do (
                matched += 1
                if emitted < {cap} do (
                    format "V|%|%\\n" i (p as string) to:out
                    emitted += 1
                )
            )
        )
    )
    format "META|%|%|%|%\\n" nv matched emitted (obj.modifiers.count as string) to:out
    out as string
{tail}
)"""
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        from .splines import _parse_p3  # shared point3 text parser

        verts = []
        meta = {}
        for line in raw.splitlines():
            kind, _, rest = line.partition("|")
            f = rest.split("|")
            if kind == "V" and len(f) >= 2:
                verts.append({"i": int(f[0]), "pos": _parse_p3(f[1])})
            elif kind == "META" and len(f) >= 4:
                meta = {
                    "total_verts": int(f[0]),
                    "matched": int(f[1]),
                    "returned": int(f[2]),
                    "modifiers_above": int(f[3]),
                }
        result: dict[str, Any] = {"name": name, **meta, "verts": verts}
        if meta.get("matched", 0) > meta.get("returned", 0):
            result["truncated"] = "raise max_verts or narrow the filter"
        return result

    if action == "move":
        off = [float(v) for v in (offset or [])]
        if len(off) != 3:
            return _err("move needs offset [x,y,z]")
        soft_block = ""
        if falloff > 0:
            r = float(falloff)
            soft_block = f"""
        local inSet = #{{}}
        for i in idxs do inSet[i] = true
        local mn = [1e30,1e30,1e30]
        local mx = [-1e30,-1e30,-1e30]
        for p in movedPos do (
            if p.x < mn.x do mn.x = p.x
            if p.y < mn.y do mn.y = p.y
            if p.z < mn.z do mn.z = p.z
            if p.x > mx.x do mx.x = p.x
            if p.y > mx.y do mx.y = p.y
            if p.z > mx.z do mx.z = p.z
        )
        mn -= [{r},{r},{r}]
        mx += [{r},{r},{r}]
        local nv = polyop.getNumVerts obj
        for v = 1 to nv where not inSet[v] do (
            local p = polyop.getVert obj v node:obj
            if p.x >= mn.x and p.y >= mn.y and p.z >= mn.z and p.x <= mx.x and p.y <= mx.y and p.z <= mx.z do (
                local d = 1e30
                for q in movedPos do (local dd = distance p q; if dd < d do d = dd)
                if d < {r} do (
                    local w = 1.0 - (d / {r})
                    w = w * w
                    polyop.setVert obj v (p + {_p3_lit(off)} * w) node:obj
                    nSoft += 1
                )
            )
        )"""
        loop_src = (
            f"local rawIdxs = {_indices_literal(idx_list)}\n"
            "        local nvAll = polyop.getNumVerts obj\n"
            "        local idxs = for i in rawIdxs where i <= nvAll collect i"
            if idx_list
            else "local idxs = for i = 1 to (polyop.getNumVerts obj) collect i"
        )
        script = f"""(
{guard}
    undo "Move Verts" on (
        {loop_src}
        local movedPos = #()
        for i in idxs do append movedPos (polyop.getVert obj i node:obj)
        for j = 1 to idxs.count do polyop.setVert obj idxs[j] (movedPos[j] + {_p3_lit(off)}) node:obj
        local nSoft = 0{soft_block}
        update obj
        redrawViews()
        "OK|" + (idxs.count as string) + "|" + (nSoft as string) + "|" + (wasConverted as string)
    )
{tail}
)"""
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 4 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:300]}")
        return {
            "name": name,
            "moved": int(parts[1]) if parts[1].isdigit() else 0,
            "soft_moved": int(parts[2]) if parts[2].isdigit() else 0,
            "converted": parts[3] == "true",
        }

    if action == "set":
        pos_list, perr = _normalize_positions(positions)
        if perr:
            return _err(perr)
        if not idx_list:
            return _err("set needs explicit indices (parallel to positions)")
        if len(idx_list) != len(pos_list):
            return _err(f"indices ({len(idx_list)}) and positions ({len(pos_list)}) must be parallel")
        pos_arr = "#(" + ", ".join(_p3_lit(p) for p in pos_list) + ")"
        script = f"""(
{guard}
    undo "Set Verts" on (
        local idxs = {_indices_literal(idx_list)}
        local ps = {pos_arr}
        local nv = polyop.getNumVerts obj
        local done = 0
        for j = 1 to idxs.count do (
            if idxs[j] <= nv do (
                polyop.setVert obj idxs[j] ps[j] node:obj
                done += 1
            )
        )
        update obj
        redrawViews()
        "OK|" + (done as string) + "|" + (wasConverted as string)
    )
{tail}
)"""
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 3 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:300]}")
        done = int(parts[1]) if parts[1].isdigit() else 0
        result = {"name": name, "set": done, "converted": parts[2] == "true"}
        if done < len(idx_list):
            result["warning"] = f"{len(idx_list) - done} indices out of range"
        return result

    if action == "conform":
        if not target:
            return _err("conform needs target (spline shape or geometry node)")
        ax = axes.strip().lower()
        if not ax or any(c not in "xyz" for c in ax):
            return _err("axes must be a subset of 'xyz' (e.g. 'xz')")
        mask = [1 if c in ax else 0 for c in "xyz"]
        s = float(strength)
        safe_t = safe_string(target)
        loop_src = (
            f"local idxs = {_indices_literal(idx_list)}"
            if idx_list
            else "local idxs = for i = 1 to (polyop.getNumVerts obj) collect i"
        )
        axis_token = axis.strip().lower()
        if axis_token and axis_token not in AXIS_VECTORS:
            return _err(f"axis must be one of {sorted(AXIS_VECTORS)}")
        dir_lit, both_ways = AXIS_VECTORS.get(axis_token, ("[0,0,1]", True))
        mesh_second_ray = ""
        if both_ways:
            mesh_second_ray = """
                local h2 = intersectRay tgt (ray p (-dirv))
                if h2 != undefined do (
                    local d2 = distance p h2.pos
                    if best == undefined or d2 < bestD do (best = h2.pos; bestD = d2)
                )"""
        script = f"""(
{guard}
    local tgt = getNodeByName "{safe_t}"
    if tgt == undefined then "__ERROR__|target not found: {safe_t}" else (
    local isShape = superclassof tgt == Shape
    if not isShape and superclassof tgt != GeometryClass then "__ERROR__|target must be a spline shape or geometry, got " + ((classof tgt) as string) else (
    if isShape and {spline_index} > (numSplines tgt) then "__ERROR__|target has fewer than {spline_index} splines" else (
    undo "Conform Verts" on (
        {loop_src}
        local nv = polyop.getNumVerts obj
        local moved = 0
        local skipped = 0
        local dirv = {dir_lit}
        for i in idxs do (
            if i <= nv then (
                local p = polyop.getVert obj i node:obj
                local dest = undefined
                if isShape then (
                    try (
                        local pr = nearestPathParam tgt {spline_index} p
                        dest = pathInterp tgt {spline_index} pr
                    ) catch ()
                ) else (
                    local best = undefined
                    local bestD = 1e30
                    local h1 = intersectRay tgt (ray p dirv)
                    if h1 != undefined do (best = h1.pos; bestD = distance p h1.pos){mesh_second_ray}
                    dest = best
                )
                if dest == undefined then skipped += 1
                else (
                    local d = dest - p
                    d.x *= {mask[0]}
                    d.y *= {mask[1]}
                    d.z *= {mask[2]}
                    polyop.setVert obj i (p + d * {s}) node:obj
                    moved += 1
                )
            ) else skipped += 1
        )
        update obj
        redrawViews()
        "OK|" + (moved as string) + "|" + (skipped as string) + "|" + (if isShape then "spline" else "mesh") + "|" + (wasConverted as string)
    )
    )
    )
    )
{tail}
)"""
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 5 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:300]}")
        result = {
            "name": name,
            "target": target,
            "mode": parts[3],
            "conformed": int(parts[1]) if parts[1].isdigit() else 0,
            "skipped": int(parts[2]) if parts[2].isdigit() else 0,
            "axes": ax,
            "strength": s,
            "converted": parts[4] == "true",
        }
        if parts[3] == "mesh" and result["skipped"]:
            result["note"] = "skipped verts had no ray hit along the axis — try the opposite sign or both-ways (unsigned) axis"
        return result

    return _err(f"unknown action: {action} (get | move | set | conform)")
