from ..server import mcp, client


@mcp.tool()
def get_scene_info() -> str:
    """Get a list of all objects in the current 3ds Max scene.

    Returns a formatted list with each object's name, class, position, and visibility.
    """
    maxscript = r"""(
        local arr = #()
        for obj in objects do (
            local posStr = "[" + (obj.pos.x as string) + "," + \
                           (obj.pos.y as string) + "," + \
                           (obj.pos.z as string) + "]"
            local entry = "{" + \
                "\"name\":\"" + obj.name + "\"," + \
                "\"class\":\"" + ((classOf obj) as string) + "\"," + \
                "\"position\":" + posStr + "," + \
                "\"isHidden\":" + (if obj.isHidden then "true" else "false") + \
            "}"
            append arr entry
        )
        local result = "["
        for i = 1 to arr.count do (
            if i > 1 do result += ","
            result += arr[i]
        )
        result += "]"
        result
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "[]")


@mcp.tool()
def get_selection() -> str:
    """Get information about the currently selected objects in 3ds Max.

    Returns a formatted list with each selected object's name, class,
    position, and wireframe color.
    """
    maxscript = r"""(
        local arr = #()
        for obj in selection do (
            local posStr = "[" + (obj.pos.x as string) + "," + \
                           (obj.pos.y as string) + "," + \
                           (obj.pos.z as string) + "]"
            local colorStr = "[" + (obj.wirecolor.r as string) + "," + \
                             (obj.wirecolor.g as string) + "," + \
                             (obj.wirecolor.b as string) + "]"
            local entry = "{" + \
                "\"name\":\"" + obj.name + "\"," + \
                "\"class\":\"" + ((classOf obj) as string) + "\"," + \
                "\"position\":" + posStr + "," + \
                "\"wirecolor\":" + colorStr + \
            "}"
            append arr entry
        )
        local result = "["
        for i = 1 to arr.count do (
            if i > 1 do result += ","
            result += arr[i]
        )
        result += "]"
        result
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "[]")
