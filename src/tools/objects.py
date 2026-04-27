import json as _json
import re

from ..server import mcp, client
from ..coerce import FloatList, StrList
from src.helpers.maxscript import safe_string


@mcp.tool()
def get_object_properties(name: str) -> str:
    """Get detailed properties of a named object in the 3ds Max scene."""
    if client.native_available:
        try:
            params = _json.dumps({"name": name})
            response = client.send_command(params, cmd_type="native:get_object_properties")
            return response.get("result", "{}")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    safe = safe_string(name)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
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
def set_object_property(name: str, property: str, value: str) -> str:
    """Set a property on a named object in the 3ds Max scene."""
    if client.native_available:
        try:
            params = _json.dumps({"name": name, "property": property, "value": value})
            response = client.send_command(params, cmd_type="native:set_object_property")
            return response.get("result", "")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    safe = safe_string(name)
    safe_prop = safe_string(property)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj != undefined then (
            try (
                execute ("$'" + obj.name + "'." + "{safe_prop}" + " = " + "{value}")
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


@mcp.tool()
def create_object(
    type: str,
    name: str = "",
    params: str = "",
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
    """Create a new object in the 3ds Max scene and auto-fill common primitive sizes when omitted."""
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

    if client.native_available:
        try:
            p = {"type": type}
            if name:
                p["name"] = name
            if params:
                p["params"] = params
            response = client.send_command(_json.dumps(p), cmd_type="native:create_object")
            return response.get("result", "")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    safe = safe_string(name)
    name_param = f' name:"{safe}"' if name else ""
    maxscript = f"""(
        local obj = {type}{name_param} {params}
        obj.name
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def delete_objects(names: StrList) -> str:
    """Delete objects from the 3ds Max scene by name."""
    if client.native_available:
        try:
            params = _json.dumps({"names": names})
            response = client.send_command(params, cmd_type="native:delete_objects")
            return response.get("result", "")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    name_checks = [f'"{safe_string(n)}"' for n in names]
    names_array = "#(" + ", ".join(name_checks) + ")"

    maxscript = f"""(
        local nameList = {names_array}
        local deleted = #()
        local notFound = #()
        for n in nameList do (
            local obj = getNodeByName n
            if obj != undefined then (
                delete obj
                append deleted n
            ) else (
                append notFound n
            )
        )
        local result = "Deleted: " + (deleted as string)
        if notFound.count > 0 then
            result += " | Not found: " + (notFound as string)
        result
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
