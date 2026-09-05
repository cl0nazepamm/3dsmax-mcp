"""Spline authoring and knot-level editing.

Draw spline shapes from point lists (corner/smooth/bezier knots, closed loops,
multi-spline shapes for holes), read knots back, and nudge individual knots —
the agent loop for tracing a reference silhouette and refining it against
captures.  All coordinates are world-space (verified: addKnot / getKnotPoint /
pathInterp all operate in the working coordinate system regardless of node pos).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..coerce import DictList
from ..helpers.maxscript import safe_string
from ..server import client, mcp

KNOT_TYPES = {
    "corner": "#corner",
    "smooth": "#smooth",
    "bezier": "#bezier",
    "beziercorner": "#bezierCorner",
    "bezier_corner": "#bezierCorner",
}
SEG_TYPES = {"line": "#line", "curve": "#curve"}
MAX_POINTS = 2000
MAX_KNOTS_OUT = 400
_P3_RE = re.compile(r"\[([-+\d.eE]+),([-+\d.eE]+),([-+\d.eE]+)\]")


def _err(message: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "error", "error": message}
    if extra:
        out["details"] = extra
    return out


def _run(script: str) -> str:
    return str(client.send_command(script).get("result", ""))


def _p3(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def _p3_lit(p: list[float]) -> str:
    return f"[{p[0]},{p[1]},{p[2]}]"


def _parse_p3(token: str) -> list[float]:
    m = _P3_RE.match(token.strip())
    if not m:
        return [0.0, 0.0, 0.0]
    try:
        return [float(m.group(i)) for i in (1, 2, 3)]
    except ValueError:
        return [0.0, 0.0, 0.0]


def _parse_float(token: str) -> float:
    try:
        return float(token)
    except (TypeError, ValueError):
        return 0.0


def _normalize_points(points: Any, default_type: str) -> tuple[list[dict[str, Any]], str]:
    """Absorb every plausible caller shape: JSON string, list of [x,y,z], flat
    coord list, list of dicts ({pos, type, seg, in_vec/in, out_vec/out})."""
    if isinstance(points, str):
        try:
            points = json.loads(points)
        except (ValueError, TypeError):
            return [], "points is a string but not valid JSON"
    if isinstance(points, dict):
        points = [points]
    if not isinstance(points, (list, tuple)) or not points:
        return [], "points must be a non-empty list"
    # Flat coordinate list: [x1,y1,z1,x2,y2,z2,...]
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in points):
        if len(points) % 3 != 0:
            return [], f"flat points list length {len(points)} is not divisible by 3"
        points = [list(points[i : i + 3]) for i in range(0, len(points), 3)]
    out: list[dict[str, Any]] = []
    for i, item in enumerate(points):
        if isinstance(item, dict):
            pos = _p3(item.get("pos") or item.get("position") or item.get("p"))
            if pos is None:
                return [], f"point {i + 1}: dict needs pos [x,y,z]"
            ktype = str(item.get("type") or default_type).strip().lower()
            if ktype not in KNOT_TYPES:
                return [], f"point {i + 1}: type must be one of {sorted(set(KNOT_TYPES))}"
            in_vec = _p3(item.get("in_vec") if item.get("in_vec") is not None else item.get("in"))
            out_vec = _p3(item.get("out_vec") if item.get("out_vec") is not None else item.get("out"))
            seg = str(item.get("seg") or item.get("segment") or "").strip().lower()
            if seg and seg not in SEG_TYPES:
                return [], f"point {i + 1}: seg must be line or curve"
            out.append({"pos": pos, "type": ktype, "in": in_vec, "out": out_vec, "seg": seg})
        else:
            pos = _p3(item)
            if pos is None:
                return [], f"point {i + 1}: expected [x,y,z], dict, or flat number list"
            out.append({"pos": pos, "type": default_type, "in": None, "out": None, "seg": ""})
    if len(out) > MAX_POINTS:
        return [], f"{len(out)} points exceeds the {MAX_POINTS}-point cap"
    for i, p in enumerate(out):
        if p["type"] in {"bezier", "beziercorner", "bezier_corner"} and (p["in"] is None or p["out"] is None):
            return [], (
                f"point {i + 1}: bezier knots need in_vec and out_vec (absolute handle "
                "positions, not directions) — use type 'smooth' for auto tangents"
            )
    return out, ""


def _knot_lines(var: str, spline_idx: str, pts: list[dict[str, Any]]) -> list[str]:
    lines = []
    for p in pts:
        ktype = KNOT_TYPES[p["type"]]
        seg = SEG_TYPES[p["seg"]] if p["seg"] else ("#line" if p["type"] == "corner" else "#curve")
        if p["in"] is not None and p["out"] is not None:
            lines.append(
                f"addKnot {var} {spline_idx} {ktype} {seg} {_p3_lit(p['pos'])} {_p3_lit(p['in'])} {_p3_lit(p['out'])}"
            )
        else:
            lines.append(f"addKnot {var} {spline_idx} {ktype} {seg} {_p3_lit(p['pos'])}")
    return lines


def _render_lines(var: str, thickness: float, sides: int) -> list[str]:
    if thickness <= 0:
        return []
    return [
        f"{var}.render_renderable = true",
        f"{var}.render_displayRenderMesh = true",
        f"{var}.render_thickness = {thickness}",
        f"{var}.render_sides = {max(3, sides)}",
    ]


def _summary_tail(var: str) -> str:
    return (
        f'local sTot = numSplines {var}\n'
        f"local kTot = numKnots {var}\n"
        f"local cl = 0.0\n"
        f"try (cl = curveLength {var} 1) catch ()\n"
        f'"OK|" + {var}.name + "|" + (sTot as string) + "|" + (kTot as string) + "|" + (cl as string) + '
        f'"|" + ({var}.min as string) + "|" + ({var}.max as string)'
    )


def _parse_summary(raw: str) -> dict[str, Any] | None:
    parts = raw.split("|")
    if len(parts) < 6 or parts[0] != "OK":
        return None
    return {
        "name": parts[1],
        "splines": int(parts[2]) if parts[2].isdigit() else 0,
        "knots": int(parts[3]) if parts[3].isdigit() else 0,
        "length": _parse_float(parts[4]),
        "bbox_min": _parse_p3(parts[5]),
        "bbox_max": _parse_p3(parts[6]) if len(parts) > 6 else [0, 0, 0],
    }


def _editable_guard(convert: bool) -> str:
    return (
        "local wasConverted = false\n"
        "if not (isKindOf obj.baseObject SplineShape) do (\n"
        + ("" if convert else 'if obj.modifiers.count > 0 do throw "Editable spline base required; convert=true explicitly collapses the stack"\n')
        + "convertToSplineShape obj; wasConverted = true\n)\n"
    )


@mcp.tool()
def draw_spline(
    action: str = "create",
    name: str = "",
    points: Any = None,
    closed: bool = False,
    knot_type: str = "smooth",
    knots: Optional[DictList] = None,
    spline_index: int = 1,
    knot_index: int = 0,
    segment: int = 0,
    param: float = 0.5,
    thickness: float = 0.0,
    sides: int = 12,
    samples: int = 0,
    center_pivot: bool = False,
    convert: bool = False,
) -> Any:
    """Draw and edit spline shapes from explicit points (world-space).

    Actions:
    - create: new shape from `points` — each point is [x,y,z] or a dict
      {pos, type: corner|smooth|bezier|bezierCorner, seg: line|curve, in_vec, out_vec}
      (bezier handle vecs are absolute positions). closed=true closes the loop.
      knot_type sets the default; thickness>0 makes it renderable; center_pivot
      moves the pivot to the bbox center.
    - add_spline: add another spline (from `points`) to an existing shape — holes,
      multi-outline shapes. Converts parametric shapes to editable SplineShape.
    - get: read back splines/knots (positions, types, handle vecs) plus optional
      `samples` length-uniform points per spline for silhouette comparison.
    - set_knots: edit knots via `knots` list — {spline, knot, pos, in_vec, out_vec,
      type}; moving a bezier knot without explicit vecs drags its handles along.
    - insert_knot: refine `segment` of spline_index at `param` (0-1) — adds a knot.
    - delete_knot: remove knot_index from spline_index.
    - delete_spline: remove spline_index entirely.
    - set_render: toggle renderable mesh — thickness (0 disables), sides.

    Edits target the spline BASE, preserving Surface, CrossSection, Sweep, etc.
    A parametric base with modifiers requires convert=true to collapse explicitly;
    bare parametric shapes still convert automatically. Coordinates are world-space.
    Pair with modifiers for solids: Extrude, Lathe, Bevel, Sweep on the result.
    Use when: tracing reference profiles/silhouettes, drawing paths, building
    curved forms parametric primitives cannot express, refining spline curves.
    Not when: simple parametric shapes (create_object: Circle, Rectangle, ...).
    """
    action = action.strip().lower()
    if not name:
        return _err("name is required")
    safe = safe_string(name)
    default_type = knot_type.strip().lower() or "smooth"
    if default_type not in KNOT_TYPES:
        return _err(f"knot_type must be one of {sorted(set(KNOT_TYPES))}")
    editable_guard = _editable_guard(convert)

    if action == "create":
        pts, perr = _normalize_points(points, default_type)
        if perr:
            return _err(perr)
        if len(pts) < 2:
            return _err("create needs at least 2 points")
        lines = [
            f'local finalName = "{safe}"',
            f'if (getNodeByName finalName) != undefined do finalName = uniquename "{safe}"',
            "local ss = splineShape name:finalName pos:[0,0,0]",
            "addNewSpline ss",
        ]
        lines += _knot_lines("ss", "1", pts)
        if closed:
            lines.append("close ss 1")
        lines.append("updateShape ss")
        lines += _render_lines("ss", thickness, sides)
        if center_pivot:
            lines.append("CenterPivot ss")
        script = "(\nundo \"Draw Spline\" on (\n" + "\n".join(lines) + "\n" + _summary_tail("ss") + "\n)\n)"
        raw = _run(script)
        summary = _parse_summary(raw)
        if summary is None:
            return _err(f"unexpected bridge reply: {raw[:300]}")
        summary["closed"] = closed
        summary["renderable"] = thickness > 0
        if summary["name"] != name:
            summary["renamed_from"] = name
        return summary

    guard = f'local obj = getNodeByName "{safe}"\nif obj == undefined then "__ERROR__|shape not found: {safe}" else '

    if action == "add_spline":
        pts, perr = _normalize_points(points, default_type)
        if perr:
            return _err(perr)
        if len(pts) < 2:
            return _err("add_spline needs at least 2 points")
        body = [
            editable_guard,
            "addNewSpline obj",
            "local sidx = numSplines obj",
        ]
        for line in _knot_lines("obj", "sidx", pts):
            body.append(line)
        if closed:
            body.append("close obj sidx")
        body.append("updateShape obj")
        body.append(
            '"OK|" + (sidx as string) + "|" + (wasConverted as string) + "|" + ((numKnots obj sidx) as string)'
        )
        script = "(\n" + guard + "(\nundo \"Add Spline\" on (\n" + "\n".join(body) + "\n)\n)\n)"
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 4 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:300]}")
        return {
            "name": name,
            "spline_index": int(parts[1]) if parts[1].isdigit() else 0,
            "converted_to_splineshape": parts[2] == "true",
            "knots": int(parts[3]) if parts[3].isdigit() else 0,
            "closed": closed,
        }

    if action == "get":
        sample_block = ""
        if samples > 0:
            n = min(int(samples), 200)
            sample_block = f"""
    local nsp = 1
    try (nsp = numSplines obj) catch ()
    resetLengthInterp()
    for s = 1 to nsp do (
        for i = 0 to {n - 1} do (
            local t = i / {float(max(n - 1, 1))}
            try (format "SAMP|%|%|%\\n" s i ((lengthInterp obj s t) as string) to:out) catch ()
        )
    )"""
        script = f"""(
{guard}(
    local out = stringstream ""
    format "SHAPE|%|%|%|%\\n" ((classof obj) as string) ((isKindOf obj.baseObject SplineShape) as string) ((classof obj.baseObject) as string) obj.modifiers.count to:out
    local emitted = 0
    try (
        for s = 1 to (numSplines obj) do (
            format "SPL|%|%|%\\n" s ((isClosed obj s) as string) ((numKnots obj s) as string) to:out
            for k = 1 to (numKnots obj s) do (
                if emitted < {MAX_KNOTS_OUT} do (
                    format "KNOT|%|%|%|%|%|%\\n" s k ((getKnotType obj s k) as string) ((getKnotPoint obj s k) as string) ((getInVec obj s k) as string) ((getOutVec obj s k) as string) to:out
                    emitted += 1
                )
            )
        )
    ) catch (format "NOKNOTS|%\\n" (getCurrentException() as string) to:out){sample_block}
    out as string
)
)"""
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        result: dict[str, Any] = {"name": name, "splines": [], "cage": "base", "space": "world"}
        by_idx: dict[int, dict[str, Any]] = {}
        emitted = 0
        for line in raw.splitlines():
            kind, _, rest = line.partition("|")
            f = rest.split("|")
            if kind == "SHAPE" and len(f) >= 2:
                result["class"] = f[0]
                result["editable"] = f[1] == "true"
                if len(f) >= 4:
                    result["base_class"] = f[2]
                    result["modifiers_above"] = int(f[3])
            elif kind == "SPL" and len(f) >= 3:
                idx = int(f[0])
                by_idx[idx] = {
                    "index": idx,
                    "closed": f[1] == "true",
                    "knot_count": int(f[2]) if f[2].isdigit() else 0,
                    "knots": [],
                }
                result["splines"].append(by_idx[idx])
            elif kind == "KNOT" and len(f) >= 6:
                idx = int(f[0])
                if idx in by_idx:
                    by_idx[idx]["knots"].append(
                        {
                            "knot": int(f[1]),
                            "type": f[2].lstrip("#"),
                            "pos": _parse_p3(f[3]),
                            "in_vec": _parse_p3(f[4]),
                            "out_vec": _parse_p3(f[5]),
                        }
                    )
                    emitted += 1
            elif kind == "SAMP" and len(f) >= 3:
                idx = int(f[0])
                if idx not in by_idx:
                    by_idx[idx] = {"index": idx, "knots": []}
                    result["splines"].append(by_idx[idx])
                by_idx[idx].setdefault("samples", []).append(_parse_p3(f[2]))
            elif kind == "NOKNOTS":
                result["knots_unavailable"] = (
                    "knot readback failed — parametric shape? set_knots/add_spline "
                    "convert it to SplineShape first"
                )
        if emitted >= MAX_KNOTS_OUT:
            result["truncated"] = f"knot output capped at {MAX_KNOTS_OUT}"
        return result

    if action == "set_knots":
        edits = list(knots or [])
        if not edits:
            return _err("set_knots needs `knots`: [{spline, knot, pos?, in_vec?, out_vec?, type?}]")
        lines = [editable_guard, "local edited = 0"]
        for i, k in enumerate(edits):
            if not isinstance(k, dict):
                return _err(f"knots[{i}] must be a dict")
            s_idx = int(k.get("spline") or k.get("spline_index") or 1)
            k_idx = int(k.get("knot") or k.get("knot_index") or 0)
            if k_idx < 1:
                return _err(f"knots[{i}]: knot (1-based index) is required")
            ktype = str(k.get("type") or "").strip().lower()
            if ktype and ktype not in KNOT_TYPES:
                return _err(f"knots[{i}]: type must be one of {sorted(set(KNOT_TYPES))}")
            pos = _p3(k.get("pos"))
            in_vec = _p3(k.get("in_vec") if k.get("in_vec") is not None else k.get("in"))
            out_vec = _p3(k.get("out_vec") if k.get("out_vec") is not None else k.get("out"))
            if not (ktype or pos or in_vec or out_vec):
                return _err(f"knots[{i}]: nothing to change (pos, in_vec, out_vec, or type)")
            block = [f"if {s_idx} <= (numSplines obj) and {k_idx} <= (numKnots obj {s_idx}) do ("]
            if ktype:
                block.append(f"    setKnotType obj {s_idx} {k_idx} {KNOT_TYPES[ktype]}")
            if pos is not None:
                block.append(f"    local oldP = getKnotPoint obj {s_idx} {k_idx}")
                block.append(f"    local newP = {_p3_lit(pos)}")
                block.append(f"    setKnotPoint obj {s_idx} {k_idx} newP")
                if in_vec is None and out_vec is None:
                    # Drag bezier handles along with the knot so the curve shape survives.
                    block.append(f"    local kt = getKnotType obj {s_idx} {k_idx}")
                    block.append("    if kt == #bezier or kt == #bezierCorner do (")
                    block.append(f"        setInVec obj {s_idx} {k_idx} ((getInVec obj {s_idx} {k_idx}) + newP - oldP)")
                    block.append(f"        setOutVec obj {s_idx} {k_idx} ((getOutVec obj {s_idx} {k_idx}) + newP - oldP)")
                    block.append("    )")
            if in_vec is not None:
                block.append(f"    setInVec obj {s_idx} {k_idx} {_p3_lit(in_vec)}")
            if out_vec is not None:
                block.append(f"    setOutVec obj {s_idx} {k_idx} {_p3_lit(out_vec)}")
            block.append("    edited += 1")
            block.append(")")
            lines += block
        lines.append("updateShape obj")
        lines.append('"OK|" + (edited as string) + "|" + (wasConverted as string)')
        script = "(\n" + guard + "(\nundo \"Edit Knots\" on (\n" + "\n".join(lines) + "\n)\n)\n)"
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 3 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:300]}")
        edited = int(parts[1]) if parts[1].isdigit() else 0
        result = {"name": name, "edited": edited, "converted_to_splineshape": parts[2] == "true"}
        if edited < len(edits):
            result["warning"] = f"{len(edits) - edited} edit(s) skipped — spline/knot index out of range"
        return result

    if action == "insert_knot":
        if segment < 1:
            return _err("insert_knot needs `segment` (1-based) and `param` (0-1) on spline_index")
        p = min(max(float(param), 0.0), 1.0)
        body = [
            editable_guard,
            f"if {spline_index} > (numSplines obj) then \"__ERROR__|spline_index out of range\" else (",
            f"if {segment} > (numSegments obj {spline_index}) then \"__ERROR__|segment out of range\" else (",
            f"local newIdx = refineSegment obj {spline_index} {segment} {p}",
            "updateShape obj",
            f'"OK|" + (newIdx as string) + "|" + ((numKnots obj {spline_index}) as string)',
            ")",
            ")",
        ]
        script = "(\n" + guard + "(\nundo \"Insert Knot\" on (\n" + "\n".join(body) + "\n)\n)\n)"
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 3 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:300]}")
        return {
            "name": name,
            "spline_index": spline_index,
            "new_knot": int(parts[1]) if parts[1].isdigit() else 0,
            "knots": int(parts[2]) if parts[2].isdigit() else 0,
        }

    if action == "delete_knot":
        if knot_index < 1:
            return _err("delete_knot needs knot_index (1-based) on spline_index")
        body = [
            editable_guard,
            f"if {spline_index} > (numSplines obj) then \"__ERROR__|spline_index out of range\" else (",
            f"if {knot_index} > (numKnots obj {spline_index}) then \"__ERROR__|knot_index out of range\" else (",
            f"deleteKnot obj {spline_index} {knot_index}",
            "updateShape obj",
            f'"OK|" + ((numKnots obj {spline_index}) as string)',
            ")",
            ")",
        ]
        script = "(\n" + guard + "(\nundo \"Delete Knot\" on (\n" + "\n".join(body) + "\n)\n)\n)"
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 2 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:300]}")
        return {"name": name, "spline_index": spline_index, "knots": int(parts[1]) if parts[1].isdigit() else 0}

    if action == "delete_spline":
        body = [
            editable_guard,
            f"if {spline_index} > (numSplines obj) then \"__ERROR__|spline_index out of range\" else (",
            f"deleteSpline obj {spline_index}",
            "updateShape obj",
            '"OK|" + ((numSplines obj) as string)',
            ")",
        ]
        script = "(\n" + guard + "(\nundo \"Delete Spline\" on (\n" + "\n".join(body) + "\n)\n)\n)"
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 2 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:300]}")
        return {"name": name, "splines": int(parts[1]) if parts[1].isdigit() else 0}

    if action == "set_render":
        if thickness > 0:
            body = _render_lines("obj", thickness, sides) + ['"OK|on"']
        else:
            body = ["obj.render_renderable = false", "obj.render_displayRenderMesh = false", '"OK|off"']
        script = "(\n" + guard + "(\n" + "\n".join(body) + "\n)\n)"
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        return {"name": name, "renderable": thickness > 0, "thickness": thickness}

    return _err(
        f"unknown action: {action} (create | add_spline | get | set_knots | "
        "insert_knot | delete_knot | delete_spline | set_render)"
    )
