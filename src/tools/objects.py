import json as _json

from ..server import mcp, client
from ..coerce import StrList
from src.helpers.maxscript import safe_string


@mcp.tool()
def get_object_properties(name: str) -> str:
    """Get detailed properties of a named object in the 3ds Max scene.

    Args:
        name: The object name (e.g. "Box001")

    Returns properties including transform, material, and modifier stack.
    """
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
    """Set a property on a named object in the 3ds Max scene.

    If unsure of exact property names, call get_object_properties or
    inspect_object first. Property names vary by class (e.g. Cylinder has
    "radius" not "radius1", Cone has "radius1"/"radius2").

    Args:
        name: The object name (e.g. "Box001")
        property: The property to set (e.g. "pos", "wirecolor", "height")
        value: The value as a MAXScript expression (e.g. "[10,20,30]", "red", "50")

    Returns confirmation or error message.
    """
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


@mcp.tool()
def create_object(type: str, name: str = "", params: str = "") -> str:
    """Create a new object in the 3ds Max scene.

    Args:
        type: The object type (e.g. "Box", "Sphere", "Cylinder", "Plane", "Teapot")
        name: Optional name for the object
        params: Optional MAXScript parameters (e.g. "radius:25 pos:[0,0,50]")

    Returns the name of the created object.
    """
    # Apply sensible defaults when no params given — SDK defaults are all zeros
    if not params:
        params = _TYPE_DEFAULTS.get(type.lower(), "")

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
    """Delete objects from the 3ds Max scene by name.

    Args:
        names: List of object names to delete.

    Returns summary of deleted and not found objects.
    """
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
