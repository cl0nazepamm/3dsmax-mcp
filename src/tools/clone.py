from typing import Optional
from ..server import mcp, client


def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


@mcp.tool()
def clone_objects(
    names: list[str],
    mode: str = "copy",
    offset: Optional[list[float]] = None,
) -> str:
    """Clone (copy/instance/reference) objects in the scene.

    Args:
        names: List of object names to clone.
        mode: "copy" (default), "instance", or "reference".
        offset: [x,y,z] position offset for clones. Default [0,0,0].

    Returns list of new clone names.
    """
    if offset is None:
        offset = [0.0, 0.0, 0.0]

    mode_map = {"copy": "#copy", "instance": "#instance", "reference": "#reference"}
    ms_mode = mode_map.get(mode, "#copy")
    name_arr = "#(" + ", ".join(f'"{_safe_name(n)}"' for n in names) + ")"

    maxscript = f"""(
        local nameList = {name_arr}
        local srcNodes = #()
        local notFound = #()
        for n in nameList do (
            local obj = getNodeByName n
            if obj != undefined then
                append srcNodes obj
            else
                append notFound n
        )
        if srcNodes.count == 0 then (
            "No valid objects found to clone"
        ) else (
            local newNodes = #()
            maxOps.cloneNodes srcNodes cloneType:{ms_mode} newNodes:&newNodes
            local offsetVec = [{offset[0]},{offset[1]},{offset[2]}]
            for n in newNodes do move n offsetVec
            local resultNames = for n in newNodes collect ("\\\"" + n.name + "\\\"")
            local resultStr = "["
            for i = 1 to resultNames.count do (
                if i > 1 do resultStr += ","
                resultStr += resultNames[i]
            )
            resultStr += "]"
            if notFound.count > 0 then
                resultStr += " | Not found: " + (notFound as string)
            resultStr
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
