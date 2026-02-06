from ..server import mcp, client


def _safe_name(name: str) -> str:
    """Escape a string for use in a MAXScript string literal."""
    return name.replace("\\", "\\\\").replace('"', '\\"')


@mcp.tool()
def get_object_properties(name: str) -> str:
    """Get detailed properties of a named object in the 3ds Max scene.

    Args:
        name: The object name (e.g. "Box001")

    Returns properties including transform, material, and modifier stack.
    """
    safe = _safe_name(name)
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
            "{{" + \
                "\\\"name\\\":\\\"" + obj.name + "\\\"," + \
                "\\\"class\\\":\\\"" + ((classOf obj) as string) + "\\\"," + \
                "\\\"superclass\\\":\\\"" + ((superClassOf obj) as string) + "\\\"," + \
                "\\\"position\\\":" + posStr + "," + \
                "\\\"rotation\\\":" + rotStr + "," + \
                "\\\"scale\\\":" + scaleStr + "," + \
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

    Args:
        name: The object name (e.g. "Box001")
        property: The property to set (e.g. "pos", "wirecolor", "height")
        value: The value as a MAXScript expression (e.g. "[10,20,30]", "red", "50")

    Returns confirmation or error message.
    """
    safe = _safe_name(name)
    safe_prop = _safe_name(property)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj != undefined then (
            try (
                execute ("obj." + "{safe_prop}" + " = " + "{value}")
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


@mcp.tool()
def create_object(type: str, name: str = "", params: str = "") -> str:
    """Create a new object in the 3ds Max scene.

    Args:
        type: The object type (e.g. "Box", "Sphere", "Cylinder", "Plane", "Teapot")
        name: Optional name for the object
        params: Optional MAXScript parameters (e.g. "radius:25 pos:[0,0,50]")

    Returns the name of the created object.
    """
    safe = _safe_name(name)
    name_param = f' name:"{safe}"' if name else ""
    maxscript = f"""(
        local obj = {type}{name_param} {params}
        obj.name
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def delete_objects(names: list[str]) -> str:
    """Delete objects from the 3ds Max scene by name.

    Args:
        names: List of object names to delete.

    Returns summary of deleted and not found objects.
    """
    name_checks = [f'"{_safe_name(n)}"' for n in names]
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
