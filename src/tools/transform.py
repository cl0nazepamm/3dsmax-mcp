from typing import Optional
from ..server import mcp, client


def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


@mcp.tool()
def transform_object(
    name: str,
    move: Optional[list[float]] = None,
    rotate: Optional[list[float]] = None,
    scale: Optional[list[float]] = None,
    coordinate_system: str = "world",
) -> str:
    """Move, rotate, and/or scale an object by the given offsets.

    Args:
        name: The object name (e.g. "Box001")
        move: [x,y,z] translation offset (relative)
        rotate: [x,y,z] rotation in degrees
        scale: [x,y,z] scale factors, or a single-element list for uniform scale
        coordinate_system: "world" (default) or "local"

    Returns confirmation of applied transforms.
    """
    safe = _safe_name(name)

    ops = []
    if move:
        ops.append(f"move obj [{move[0]},{move[1]},{move[2]}]")
    if rotate:
        if coordinate_system == "local":
            ops.append(f"in coordsys local rotate obj (eulerAngles {rotate[0]} {rotate[1]} {rotate[2]})")
        else:
            ops.append(f"rotate obj (eulerAngles {rotate[0]} {rotate[1]} {rotate[2]})")
    if scale:
        if len(scale) == 1:
            s = scale[0]
            scale = [s, s, s]
        ops.append(f"scale obj [{scale[0]},{scale[1]},{scale[2]}]")

    if not ops:
        return "No transform parameters provided."

    ops_str = "\n            ".join(ops)

    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj != undefined then (
            {ops_str}
            local posStr = "[" + (obj.pos.x as string) + "," + (obj.pos.y as string) + "," + (obj.pos.z as string) + "]"
            "Transformed " + obj.name + " — pos: " + posStr
        ) else (
            "Object not found: {safe}"
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
