from __future__ import annotations

import json as _json
import re

from ...server import mcp, client
from ...core.coerce import FloatList, IntList, ParamValues, StrList
from ...core.max_client import MaxBridgeError
from ...core.maxscript import maxscript_params, safe_string, node_by_name, node_by_name_expr, node_script_ref
from ._spatial import (
    apply_pos_mode_fix_maxscript,
    build_create_object_maxscript,
    build_create_tripback_from_orientation,
    enrich_spatial_payload,
    normalize_pos_mode,
    parse_spatial_json,
)


def _orientation_payload_for_name(name: str) -> dict | None:
    try:
        response = client.send_command(
            _json.dumps({"names": [name]}),
            cmd_type="native:analyze_node_orientation",
        )
        raw = response.get("result", "")
        if not raw:
            return None
        payload = _json.loads(raw) if isinstance(raw, str) else raw
        return payload if isinstance(payload, dict) else None
    except (MaxBridgeError, RuntimeError, _json.JSONDecodeError, TypeError):
        # A name several objects share is refused natively: report no
        # snapshot rather than failing a creation that already happened.
        return None


def _finalize_create_result(
    raw: str,
    *,
    type: str,
    pos: FloatList | None,
    pos_mode: str,
) -> str:
    if not raw:
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            payload = parse_spatial_json(raw)
            return _json.dumps(payload)
        except (_json.JSONDecodeError, TypeError):
            pass

    name = raw.strip().strip('"')
    if not name or name.lower().startswith("error"):
        return raw

    orientation = _orientation_payload_for_name(name)
    if orientation:
        try:
            payload = build_create_tripback_from_orientation(
                orientation,
                type_name=type,
                pos=list(pos) if pos is not None else None,
                pos_mode=pos_mode,
            )
            return _json.dumps(payload)
        except ValueError:
            pass

    return _json.dumps({"name": name, "type": type, "warning": "spatial snapshot unavailable"})


@mcp.tool()
def get_object_properties(name: str = "", handle: int = 0) -> str:
    """Get compact properties of a named object (transform, class, material name).

    Use when: you need a small property snapshot for one known object.
    Not when: searching the scene (query_scene) or a deep exploratory dump (inspect_object).
    """
    if client.native_available:
        try:
            params = {"name": name}
            if handle:
                params["handle"] = handle
            response = client.send_command(_json.dumps(params), cmd_type="native:get_object_properties")
            return response.get("result", "{}")
        except RuntimeError:
            pass

    if not name:
        return "Object name is required for TCP fallback"

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    safe = safe_string(name)
    maxscript = f"""(
        local obj = {node_by_name(name, missing_ok=True)}
        if obj != undefined then (
            local posStr = "[" + (obj.pos.x as string) + "," + \
                           (obj.pos.y as string) + "," + \
                           (obj.pos.z as string) + "]"
            local rotStr = "[" + (obj.rotation.x as string) + "," + \
                           (obj.rotation.y as string) + "," + \
                           (obj.rotation.z as string) + "]"
            local scaleStr = "[" + (obj.scale.x as string) + "," + \
                             (obj.scale.y as string) + "," + \
                             (obj.scale.z as string) + "]"
            local matName = if obj.material != undefined then obj.material.name else "none"
            local modArr = for m in obj.modifiers collect ("\\\"" + m.name + "\\\"")
            local modStr = "["
            for i = 1 to modArr.count do (
                if i > 1 do modStr += ","
                modStr += modArr[i]
            )
            modStr += "]"
            local parentName = if obj.parent != undefined then obj.parent.name else ""
            local parentField = if parentName == "" then "null" else ("\\\"" + parentName + "\\\"")
            local childArr = for c in obj.children collect ("\\\"" + c.name + "\\\"")
            local childStr = "["
            for i = 1 to childArr.count do (
                if i > 1 do childStr += ","
                childStr += childArr[i]
            )
            childStr += "]"
            local numVStr = "null"
            local numFStr = "null"
            try (
                local snapMesh = snapshotAsMesh obj
                numVStr = snapMesh.numVerts as string
                numFStr = snapMesh.numFaces as string
                delete snapMesh
            ) catch ()
            local wcStr = "[" + (obj.wirecolor.r as string) + "," + (obj.wirecolor.g as string) + "," + (obj.wirecolor.b as string) + "]"
            local bbMin = obj.min
            local bbMax = obj.max
            local dims = bbMax - bbMin
            local dimsStr = "[" + (dims.x as string) + "," + (dims.y as string) + "," + (dims.z as string) + "]"
            "{{" + \
                "\\\"name\\\":\\\"" + obj.name + "\\\"," + \
                "\\\"class\\\":\\\"" + ((classOf obj) as string) + "\\\"," + \
                "\\\"superclass\\\":\\\"" + ((superClassOf obj) as string) + "\\\"," + \
                "\\\"position\\\":" + posStr + "," + \
                "\\\"rotation\\\":" + rotStr + "," + \
                "\\\"scale\\\":" + scaleStr + "," + \
                "\\\"parent\\\":" + parentField + "," + \
                "\\\"children\\\":" + childStr + "," + \
                "\\\"numVerts\\\":" + numVStr + "," + \
                "\\\"numFaces\\\":" + numFStr + "," + \
                "\\\"wirecolor\\\":" + wcStr + "," + \
                "\\\"layer\\\":\\\"" + obj.layer.name + "\\\"," + \
                "\\\"dimensions\\\":" + dimsStr + "," + \
                "\\\"material\\\":\\\"" + matName + "\\\"," + \
                "\\\"modifiers\\\":" + modStr + \
            "}}"
        ) else (
            "Object not found: {safe}"
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_object_property(name: str = "", property: str = "", value: str = "", handle: int = 0) -> str:
    """Set a property on a named object in the 3ds Max scene."""
    if client.native_available:
        try:
            params = {"name": name, "property": property, "value": value}
            if handle:
                params["handle"] = handle
            params = _json.dumps(params)
            response = client.send_command(params, cmd_type="native:set_object_property")
            return response.get("result", "")
        except RuntimeError:
            pass

    if not name:
        return "Object name is required for TCP fallback"

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    safe = safe_string(name)
    safe_prop = safe_string(property)
    maxscript = f"""(
        local obj = {node_by_name(name, missing_ok=True)}
        if obj != undefined then (
            try (
                execute ({node_script_ref("obj")} + "." + "{safe_prop}" + " = " + "{value}")
                "Set {safe_prop} = " + ({value} as string) + " on " + obj.name
            ) catch (
                "Error: " + (getCurrentException())
            )
        ) else (
            "Object not found: {safe}"
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


# Sensible defaults for common geometry types — SDK defaults are all zeros,
# which creates invisible objects.  Only applied when params is empty.
_TYPE_DEFAULTS = {
    "box":      "length:25 width:25 height:25",
    "sphere":   "radius:25",
    "cylinder": "radius:10 height:25",
    "cone":     "radius1:15 radius2:0 height:25",
    "torus":    "radius:20 radius2:5",
    "plane":    "length:50 width:50",
    "teapot":   "radius:15",
    "tube":     "radius1:15 radius2:10 height:25",
    "pyramid":  "width:25 depth:25 height:25",
    "geosphere": "radius:25",
    "hedra":    "radius:15",
    "torusknot": "radius:20 radius2:4",
    "chamferbox": "length:25 width:25 height:25 fillet:2",
    "chamfercyl": "radius:10 height:25 fillet:2",
    "oiltank":  "radius:15 height:25 capheight:5",
    "spindle":  "radius:15 height:25 capheight:5",
    "capsule":  "radius:10 height:25",
}


def _has_param(params: str, key: str) -> bool:
    return bool(re.search(rf"(?i)(?<!\S){re.escape(key)}\s*:", params))


def _format_param_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return format(value, "g")
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(format(v, "g") for v in value) + "]"
    return str(value)


def _merge_create_object_params(
    type: str,
    params: str = "",
    *,
    pos: FloatList | None = None,
    length: float | None = None,
    width: float | None = None,
    height: float | None = None,
    depth: float | None = None,
    radius: float | None = None,
    radius1: float | None = None,
    radius2: float | None = None,
    fillet: float | None = None,
    capheight: float | None = None,
) -> str:
    merged = params.strip()

    def append_param(key: str, value: object) -> None:
        nonlocal merged
        if value is None or _has_param(merged, key):
            return
        fragment = f"{key}:{_format_param_value(value)}"
        merged = f"{merged} {fragment}".strip() if merged else fragment

    append_param("pos", pos)
    append_param("length", length)
    append_param("width", width)
    append_param("height", height)
    append_param("depth", depth)
    append_param("radius", radius)
    append_param("radius1", radius1)
    append_param("radius2", radius2)
    append_param("fillet", fillet)
    append_param("capheight", capheight)

    defaults = _TYPE_DEFAULTS.get(type.lower(), "")
    for token in defaults.split():
        key, value = token.split(":", 1)
        append_param(key, value)

    return merged


def _typed_create_params(values: dict, **conveniences: object) -> dict:
    """The params dict with the convenience arguments filling keys it lacks (any case)."""
    merged = dict(values)
    present = {key.lower() for key in merged}
    for key, value in conveniences.items():
        if value is not None and key not in present and not (key == "pos" and "position" in present):
            merged[key] = list(value) if isinstance(value, (list, tuple)) else value
    return merged


@mcp.tool()
def create_object(
    type: str,
    name: str = "",
    params: ParamValues = "",
    pos: FloatList | None = None,
    pos_mode: str = "ground",
    length: float | None = None,
    width: float | None = None,
    height: float | None = None,
    depth: float | None = None,
    radius: float | None = None,
    radius1: float | None = None,
    radius2: float | None = None,
    fillet: float | None = None,
    capheight: float | None = None,
) -> str:
    """Create a geometry object with spatial placement feedback.

    pos_mode controls how pos is interpreted:
    - ground (default): pos targets the bottom-center of the world bbox (floor contact).
    - center: pos targets the bbox geometric center.
    - pivot: pos sets the node pivot directly (legacy/advanced).

    params: {name: value} for any class's parameters in MAXScript units (degrees,
    0-1 colors, choice names); the reply reads each one back under "params".
    parameters(class_name=type) lists what a class takes. Legacy "key:value"
    text is still accepted.

    Returns JSON with name, class, placement, bbox, axes, pivot, and localAxesWorld.
    """
    if isinstance(params, dict):
        typed = _typed_create_params(
            params, pos=pos, length=length, width=width, height=height, depth=depth, radius=radius,
            radius1=radius1, radius2=radius2, fillet=fillet, capheight=capheight)
        mode = normalize_pos_mode(pos_mode)
        if client.native_available:
            p = {"type": type, "pos_mode": mode, "params": typed}
            if name:
                p["name"] = name
            response = client.send_command(_json.dumps(p, allow_nan=False), cmd_type="native:create_object")
            return _finalize_create_result(response.get("result", ""), type=type, pos=pos, pos_mode=mode)
        # MAXScript fallback: the same values as constructor text.
        params = maxscript_params({k: v for k, v in typed.items() if k.lower() not in ("pos", "position")})
        if pos is None:
            pos = typed.get("pos", typed.get("position"))
    params = _merge_create_object_params(
        type,
        params,
        pos=pos,
        length=length,
        width=width,
        height=height,
        depth=depth,
        radius=radius,
        radius1=radius1,
        radius2=radius2,
        fillet=fillet,
        capheight=capheight,
    )
    mode = normalize_pos_mode(pos_mode)

    # A failed native request may have committed; never replay a constructor.
    if client.native_available:
        p = {"type": type, "pos_mode": mode}
        if name:
            p["name"] = name
        if params:
            p["params"] = params
        response = client.send_command(_json.dumps(p), cmd_type="native:create_object")
        raw = response.get("result", "")
        if isinstance(raw, str) and raw and not raw.strip().startswith("{"):
            name = raw.strip().strip('"')
            if name:
                client.send_command(
                    apply_pos_mode_fix_maxscript(
                        name,
                        list(pos) if pos is not None else None,
                        mode,
                    )
                )
        return _finalize_create_result(
            raw,
            type=type,
            pos=pos,
            pos_mode=mode,
        )

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    maxscript = build_create_object_maxscript(
        type=type,
        name=name,
        params=params,
        pos=list(pos) if pos is not None else None,
        pos_mode=mode,
    )
    response = client.send_command(maxscript)
    return _finalize_create_result(
        response.get("result", ""),
        type=type,
        pos=pos,
        pos_mode=mode,
    )


@mcp.tool()
def delete_objects(names: StrList | None = None, handles: IntList | None = None, dry_run: bool = False) -> str:
    """Delete objects from the 3ds Max scene by name."""
    names = names or []
    handles = handles or []
    if client.native_available:
        try:
            params = _json.dumps({"names": names, "handles": handles, "dry_run": dry_run})
            response = client.send_command(params, cmd_type="native:delete_objects")
            return response.get("result", "")
        except RuntimeError:
            pass

    if not names:
        return "Object names are required for TCP fallback"

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    name_checks = [f'"{safe_string(n)}"' for n in names]
    names_array = "#(" + ", ".join(name_checks) + ")"

    maxscript = f"""(
        local nameList = {names_array}
        local targets = #()
        local deleted = #()
        local notFound = #()
        -- Every name resolves before anything is deleted, so a shared name
        -- fails the call without leaving a partially deleted scene.
        for n in nameList do (
            local obj = {node_by_name_expr("n", missing_ok=True)}
            if obj != undefined then (
                if appendIfUnique targets obj do append deleted n
            ) else (
                append notFound n
            )
        )
        if not {str(dry_run).lower()} do delete targets
        local result = (if {str(dry_run).lower()} then "Would delete: " else "Deleted: ") + (deleted as string)
        if notFound.count > 0 then
            result += " | Not found: " + (notFound as string)
        result
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
