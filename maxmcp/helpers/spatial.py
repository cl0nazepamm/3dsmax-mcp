"""Shared spatial placement and snapshot helpers for object creation tools."""

from __future__ import annotations

import json
from typing import Any

SPATIAL_SPACE: dict[str, Any] = {
    "coordinateSystem": "3ds Max world",
    "upAxis": "Z",
    "groundPlane": "XY",
    "rightHanded": True,
}

_TYPE_AXIS_HINTS: dict[str, dict[str, str]] = {
    "box": {
        "width": "X",
        "length": "Y",
        "height": "Z",
        "note": "Box width=X, length=Y, height=Z. Default pivot is bottom-center.",
    },
    "chamferbox": {
        "width": "X",
        "length": "Y",
        "height": "Z",
        "note": "Box width=X, length=Y, height=Z. Default pivot is bottom-center.",
    },
    "plane": {
        "length": "Y",
        "width": "X",
        "note": "Plane length=Y, width=X. Default pivot is center.",
    },
    "cylinder": {
        "radius": "XY",
        "height": "Z",
        "note": "Radial primitives extend in XY; height is Z.",
    },
    "cone": {
        "radius": "XY",
        "height": "Z",
        "note": "Radial primitives extend in XY; height is Z.",
    },
    "tube": {
        "radius": "XY",
        "height": "Z",
        "note": "Radial primitives extend in XY; height is Z.",
    },
    "chamfercyl": {
        "radius": "XY",
        "height": "Z",
        "note": "Radial primitives extend in XY; height is Z.",
    },
    "sphere": {
        "radius": "XYZ",
        "note": "Radial primitives are centered on the pivot.",
    },
    "geosphere": {
        "radius": "XYZ",
        "note": "Radial primitives are centered on the pivot.",
    },
    "hedra": {
        "radius": "XYZ",
        "note": "Radial primitives are centered on the pivot.",
    },
    "teapot": {
        "radius": "XYZ",
        "note": "Radial primitives are centered on the pivot.",
    },
    "pyramid": {
        "width": "X",
        "depth": "Y",
        "height": "Z",
        "note": "Pyramid width=X, depth=Y, height=Z.",
    },
}


def normalize_pos_mode(pos_mode: str | None) -> str:
    mode = (pos_mode or "ground").strip().lower()
    if mode in {"pivot"}:
        return "pivot"
    if mode in {"center", "bbox_center"}:
        return "center"
    return "ground"


def type_axis_hints(type_name: str) -> dict[str, Any]:
    hints: dict[str, Any] = {"primitive": type_name}
    lower = type_name.strip().lower()
    known = _TYPE_AXIS_HINTS.get(lower)
    if known:
        hints.update(known)
        return hints
    hints["note"] = "Use analyze_node_orientation for bbox/pivot after creation."
    return hints


def _format_point(values: list[float] | tuple[float, ...] | None) -> str:
    if not values:
        return "undefined"
    return "[" + ",".join(format(float(v), "g") for v in values[:3]) + "]"


def strip_pos_from_params(params: str) -> str:
    if not params.strip():
        return ""
    tokens: list[str] = []
    for token in params.strip().split():
        key = token.split(":", 1)[0].strip().lower()
        if key in {"pos", "position"}:
            continue
        tokens.append(token)
    return " ".join(tokens)


def build_create_object_maxscript(
    *,
    type: str,
    name: str,
    params: str,
    pos: list[float] | None,
    pos_mode: str,
) -> str:
    """Create an object and return the same spatial JSON shape as the native path."""
    safe_name = name.replace('"', '\\"')
    name_param = f' name:"{safe_name}"' if name else ""
    clean_params = strip_pos_from_params(params)
    param_fragment = f" {clean_params}" if clean_params else ""
    has_pos = pos is not None
    target_pos = _format_point(pos) if has_pos else "undefined"
    # Interpolated inside a JSON string literal, so it must be final array text "[x,y,z]", not an mcpVec3 call.
    pos_json = "null" if not has_pos else target_pos
    mode = normalize_pos_mode(pos_mode)
    type_literal = type
    type_lower = type.strip().lower()

    return f"""(
        fn mcpNum v = (
            if v == undefined then "null" else (formattedPrint (v as float) format:".6f")
        )
        fn mcpVec3 p = (
            "[" + mcpNum p.x + "," + mcpNum p.y + "," + mcpNum p.z + "]"
        )
        fn mcpBottomCenter obj = (
            local bbMin = obj.min
            local bbMax = obj.max
            [(bbMin.x + bbMax.x) * 0.5, (bbMin.y + bbMax.y) * 0.5, bbMin.z]
        )
        fn mcpBBoxCenter obj = (
            local bbMin = obj.min
            local bbMax = obj.max
            (bbMin + bbMax) * 0.5
        )
        fn mcpApplyPosMode obj targetPos posMode = (
            local mode = toLower posMode
            local anchor = case mode of (
                "pivot": (obj.pos)
                "center": (mcpBBoxCenter obj)
                "bbox_center": (mcpBBoxCenter obj)
                default: (mcpBottomCenter obj)
            )
            if mode == "pivot" and targetPos == undefined then (
                false
            ) else (
                local target = if targetPos == undefined then [0,0,0] else targetPos
                move obj (target - anchor)
                true
            )
        )
        fn mcpSafeNormalize p = (
            local len = length p
            if len < 0.000001 then [0,0,0] else (p / len)
        )
        fn mcpSpatialSnapshot obj typeHint = (
            local tm = obj.transform
            local pivot = tm.row4
            local bbMin = obj.min
            local bbMax = obj.max
            local center = (bbMin + bbMax) * 0.5
            local dims = bbMax - bbMin
            local pivotToCenter = center - pivot
            local groundContact = mcpBottomCenter obj
            "{{" +
                "\\\"name\\\":\\\"" + obj.name + "\\\"," +
                "\\\"class\\\":\\\"" + ((classOf obj) as string) + "\\\"," +
                "\\\"type\\\":\\\"" + typeHint + "\\\"," +
                "\\\"pivot\\\":" + mcpVec3 pivot + "," +
                "\\\"bbox\\\":{{" +
                    "\\\"min\\\":" + mcpVec3 bbMin + "," +
                    "\\\"max\\\":" + mcpVec3 bbMax + "," +
                    "\\\"center\\\":" + mcpVec3 center + "," +
                    "\\\"dimensions\\\":" + mcpVec3 dims +
                "}}," +
                "\\\"groundContact\\\":" + mcpVec3 groundContact + "," +
                "\\\"pivotToBBoxCenter\\\":" + mcpVec3 pivotToCenter + "," +
                "\\\"localAxesWorld\\\":{{" +
                    "\\\"x\\\":" + mcpVec3 (mcpSafeNormalize tm.row1) + "," +
                    "\\\"y\\\":" + mcpVec3 (mcpSafeNormalize tm.row2) + "," +
                    "\\\"z\\\":" + mcpVec3 (mcpSafeNormalize tm.row3) +
                "}}," +
                "\\\"placement\\\":{{" +
                    "\\\"pos\\\":{pos_json}," +
                    "\\\"pos_mode\\\":\\\"{mode}\\\"," +
                    "\\\"pivot\\\":" + mcpVec3 pivot + "," +
                    "\\\"ground_contact\\\":" + mcpVec3 groundContact +
                "}}," +
                "\\\"space\\\":{{" +
                    "\\\"coordinateSystem\\\":\\\"3ds Max world\\\"," +
                    "\\\"upAxis\\\":\\\"Z\\\"," +
                    "\\\"groundPlane\\\":\\\"XY\\\"," +
                    "\\\"rightHanded\\\":true" +
                "}}" +
            "}}"
        )

        local obj = {type_literal}{name_param}{param_fragment}
        mcpApplyPosMode obj {target_pos} "{mode}"
        mcpSpatialSnapshot obj "{type_lower}"
    )"""


def build_clone_spatial_maxscript(node_names: list[str]) -> str:
    names_array = "#(" + ", ".join(f'"{name.replace(chr(34), "")}"' for name in node_names) + ")"
    return f"""(
        fn mcpNum v = (
            if v == undefined then "null" else (formattedPrint (v as float) format:".6f")
        )
        fn mcpVec3 p = (
            "[" + mcpNum p.x + "," + mcpNum p.y + "," + mcpNum p.z + "]"
        )
        fn mcpBottomCenter obj = (
            local bbMin = obj.min
            local bbMax = obj.max
            [(bbMin.x + bbMax.x) * 0.5, (bbMin.y + bbMax.y) * 0.5, bbMin.z]
        )
        fn mcpSafeNormalize p = (
            local len = length p
            if len < 0.000001 then [0,0,0] else (p / len)
        )
        fn mcpNodeSnapshot obj = (
            local tm = obj.transform
            local pivot = tm.row4
            local bbMin = obj.min
            local bbMax = obj.max
            local center = (bbMin + bbMax) * 0.5
            local dims = bbMax - bbMin
            "{{" +
                "\\\"name\\\":\\\"" + obj.name + "\\\"," +
                "\\\"class\\\":\\\"" + ((classOf obj) as string) + "\\\"," +
                "\\\"pivot\\\":" + mcpVec3 pivot + "," +
                "\\\"bbox\\\":{{" +
                    "\\\"min\\\":" + mcpVec3 bbMin + "," +
                    "\\\"max\\\":" + mcpVec3 bbMax + "," +
                    "\\\"center\\\":" + mcpVec3 center + "," +
                    "\\\"dimensions\\\":" + mcpVec3 dims +
                "}}," +
                "\\\"groundContact\\\":" + mcpVec3 (mcpBottomCenter obj) + "," +
                "\\\"pivotToBBoxCenter\\\":" + mcpVec3 (center - pivot) + "," +
                "\\\"localAxesWorld\\\":{{" +
                    "\\\"x\\\":" + mcpVec3 (mcpSafeNormalize tm.row1) + "," +
                    "\\\"y\\\":" + mcpVec3 (mcpSafeNormalize tm.row2) + "," +
                    "\\\"z\\\":" + mcpVec3 (mcpSafeNormalize tm.row3) +
                "}}" +
            "}}"
        )

        local names = {names_array}
        local nodes = "["
        local first = true
        for n in names do (
            local obj = getNodeByName n
            if obj != undefined then (
                if not first do nodes += ","
                nodes += mcpNodeSnapshot obj
                first = false
            )
        )
        nodes += "]"
        "{{" +
            "\\\"nodes\\\":" + nodes + "," +
            "\\\"space\\\":{{" +
                "\\\"coordinateSystem\\\":\\\"3ds Max world\\\"," +
                "\\\"upAxis\\\":\\\"Z\\\"," +
                "\\\"groundPlane\\\":\\\"XY\\\"," +
                "\\\"rightHanded\\\":true" +
            "}}" +
        "}}"
    )"""


def enrich_spatial_payload(payload: dict[str, Any], type_name: str) -> dict[str, Any]:
    payload.setdefault("space", SPATIAL_SPACE)
    payload.setdefault("axes", type_axis_hints(type_name))
    return payload


def apply_pos_mode_fix_maxscript(name: str, pos: list[float] | None, pos_mode: str) -> str:
    safe_name = name.replace('"', '\\"')
    has_pos = pos is not None
    target_pos = _format_point(pos) if has_pos else "undefined"
    mode = normalize_pos_mode(pos_mode)
    return f"""(
        fn mcpBottomCenter obj = (
            local bbMin = obj.min
            local bbMax = obj.max
            [(bbMin.x + bbMax.x) * 0.5, (bbMin.y + bbMax.y) * 0.5, bbMin.z]
        )
        fn mcpBBoxCenter obj = (
            local bbMin = obj.min
            local bbMax = obj.max
            (bbMin + bbMax) * 0.5
        )
        local obj = getNodeByName "{safe_name}"
        if obj == undefined then (
            "missing"
        ) else (
            local mode = "{mode}"
            local anchor = case mode of (
                "pivot": (obj.pos)
                "center": (mcpBBoxCenter obj)
                default: (mcpBottomCenter obj)
            )
            if mode == "pivot" and {str(has_pos).lower()} == false then (
                "ok"
            ) else (
                local target = if {str(has_pos).lower()} then {target_pos} else [0,0,0]
                move obj (target - anchor)
                "ok"
            )
        )
    )"""


def parse_spatial_json(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if isinstance(data, dict) and "type" in data:
        enrich_spatial_payload(data, str(data.get("type", "")))
    return data


def build_create_tripback_from_orientation(
    orientation_payload: dict[str, Any],
    *,
    type_name: str,
    pos: list[float] | None,
    pos_mode: str,
) -> dict[str, Any]:
    nodes = orientation_payload.get("nodes") or []
    if not nodes:
        raise ValueError("orientation query returned no nodes")
    node = dict(nodes[0])
    mode = normalize_pos_mode(pos_mode)
    bbox = node.get("bbox") or {}
    ground = node.get("groundContact")
    if ground is None and isinstance(bbox, dict):
        bb_min = bbox.get("min")
        if isinstance(bb_min, list) and len(bb_min) >= 3 and isinstance(bbox.get("max"), list):
            bb_max = bbox["max"]
            ground = [(bb_min[0] + bb_max[0]) * 0.5, (bb_min[1] + bb_max[1]) * 0.5, bb_min[2]]

    result = {
        "name": node.get("name"),
        "class": node.get("class"),
        "type": type_name,
        "pivot": node.get("pivot"),
        "bbox": bbox,
        "groundContact": ground,
        "pivotToBBoxCenter": node.get("pivotToBBoxCenter"),
        "localAxesWorld": node.get("localAxesWorld"),
        "placement": {
            "pos": list(pos) if pos is not None else None,
            "pos_mode": mode,
            "pivot": node.get("pivot"),
            "ground_contact": ground,
        },
        "space": orientation_payload.get("space") or SPATIAL_SPACE,
    }
    enrich_spatial_payload(result, type_name)
    return result
