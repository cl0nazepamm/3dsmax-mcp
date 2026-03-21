import json as _json

from ..server import mcp, client
from ..coerce import StrList
from src.helpers.maxscript import safe_string


@mcp.tool()
def set_parent(children: StrList, parent: str = "") -> str:
    """Parent or unparent objects in the 3ds Max scene.

    Args:
        children: List of object names to parent.
        parent: Parent object name. Empty string = unparent (set to scene root).

    Returns confirmation summary.
    """
    if client.native_available:
        payload = _json.dumps({"children": children, "parent": parent})
        response = client.send_command(payload, cmd_type="native:set_parent")
        return response.get("result", "")

    child_names = "#(" + ", ".join(f'"{safe_string(n)}"' for n in children) + ")"

    if parent:
        safe_parent = safe_string(parent)
        maxscript = f"""(
            local parentObj = getNodeByName "{safe_parent}"
            if parentObj == undefined then (
                "Parent not found: {safe_parent}"
            ) else (
                local childNames = {child_names}
                local done = #()
                local notFound = #()
                for n in childNames do (
                    local c = getNodeByName n
                    if c != undefined then (
                        c.parent = parentObj
                        append done n
                    ) else (
                        append notFound n
                    )
                )
                local result = "Parented " + (done.count as string) + " objects under " + parentObj.name
                if notFound.count > 0 do result += " | Not found: " + (notFound as string)
                result
            )
        )"""
    else:
        maxscript = f"""(
            local childNames = {child_names}
            local done = #()
            local notFound = #()
            for n in childNames do (
                local c = getNodeByName n
                if c != undefined then (
                    c.parent = undefined
                    append done n
                ) else (
                    append notFound n
                )
            )
            local result = "Unparented " + (done.count as string) + " objects"
            if notFound.count > 0 do result += " | Not found: " + (notFound as string)
            result
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def get_hierarchy(name: str) -> str:
    """Get the hierarchy tree of an object (recursive children).

    Args:
        name: The root object name.

    Returns JSON tree with name, class, and children for each node.
    """
    if client.native_available:
        try:
            params = _json.dumps({"name": name})
            response = client.send_command(params, cmd_type="native:get_hierarchy")
            return response.get("result", "")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    safe = safe_string(name)
    maxscript = f"""(
        fn buildTree obj = (
            local childArr = #()
            for c in obj.children do (
                append childArr (buildTree c)
            )
            local childStr = "["
            for i = 1 to childArr.count do (
                if i > 1 do childStr += ","
                childStr += childArr[i]
            )
            childStr += "]"
            "{{" + \
                "\\\"name\\\":\\\"" + obj.name + "\\\"," + \
                "\\\"class\\\":\\\"" + ((classOf obj) as string) + "\\\"," + \
                "\\\"children\\\":" + childStr + \
            "}}"
        )
        local rootObj = getNodeByName "{safe}"
        if rootObj != undefined then (
            buildTree rootObj
        ) else (
            "Object not found: {safe}"
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
