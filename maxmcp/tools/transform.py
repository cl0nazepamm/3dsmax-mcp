import json as _json
from typing import Optional
from ..server import mcp, client
from ..coerce import FloatList
from ..helpers.maxscript import safe_string


@mcp.tool()
def transform_object(
    name: str = "",
    handle: int = 0,
    move: Optional[FloatList] = None,
    rotate: Optional[FloatList] = None,
    scale: Optional[FloatList] = None,
    coordinate_system: str = "world",
) -> str:
    """Move, rotate, and/or scale an object by world/local offsets.

    Use when: posing unkeyed (or intentionally current-frame) objects.
    Not when: editing animation on keyed tracks — prefer keyframe_tracks value/move at
    explicit times; this tool can rewrite keys at the current slider frame.
    """
    if client.native_available:
        try:
            params: dict = {"name": name}
            if handle:
                params["handle"] = handle
            if move:
                params["move"] = move
            if rotate:
                params["rotate"] = rotate
            if scale:
                params["scale"] = scale
            if coordinate_system != "world":
                params["coordinate_system"] = coordinate_system
            response = client.send_command(_json.dumps(params), cmd_type="native:transform_object")
            return response.get("result", "")
        except RuntimeError:
            pass

    if not name:
        return "Object name is required for TCP fallback"

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    safe = safe_string(name)

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
