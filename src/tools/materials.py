from ..server import mcp, client


@mcp.tool()
def get_materials() -> str:
    """List all materials assigned to objects in the current 3ds Max scene.

    Returns a formatted list with each material's name, class, and which
    objects it is assigned to.
    """
    maxscript = r"""(
        local arr = #()
        local matSet = #()
        for obj in objects where obj.material != undefined do (
            local mat = obj.material
            local idx = findItem matSet mat.name
            if idx == 0 then (
                append matSet mat.name
                local objNames = for o in objects where o.material != undefined \
                    and o.material.name == mat.name collect o.name
                local objNameArr = for n in objNames collect ("\"" + n + "\"")
                local objStr = "["
                for i = 1 to objNameArr.count do (
                    if i > 1 do objStr += ","
                    objStr += objNameArr[i]
                )
                objStr += "]"
                local entry = "{" + \
                    "\"name\":\"" + mat.name + "\"," + \
                    "\"class\":\"" + ((classOf mat) as string) + "\"," + \
                    "\"assignedTo\":" + objStr + \
                "}"
                append arr entry
            )
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
