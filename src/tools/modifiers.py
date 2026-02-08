from ..server import mcp, client


def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


@mcp.tool()
def add_modifier(name: str, modifier: str, params: str = "") -> str:
    """Add a modifier to an object.

    Args:
        name: The object name (e.g. "Box001")
        modifier: Modifier class name (e.g. "TurboSmooth", "Bend", "Shell", "Edit_Poly")
        params: Optional MAXScript parameters (e.g. "iterations:2")

    Returns confirmation or error.
    """
    safe = _safe_name(name)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj != undefined then (
            try (
                local m = {modifier} {params}
                addModifier obj m
                "Added " + (classOf m as string) + " to " + obj.name
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
def remove_modifier(name: str, modifier: str) -> str:
    """Remove a modifier from an object by name.

    Args:
        name: The object name (e.g. "Box001")
        modifier: The modifier name to remove (e.g. "TurboSmooth 1", "Bend 1")

    Returns confirmation or error.
    """
    safe = _safe_name(name)
    safe_mod = _safe_name(modifier)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj != undefined then (
            local found = false
            for i = 1 to obj.modifiers.count do (
                if obj.modifiers[i].name == "{safe_mod}" then (
                    deleteModifier obj i
                    found = true
                    exit
                )
            )
            if found then
                "Removed modifier \\\"" + "{safe_mod}" + "\\\" from " + obj.name
            else
                "Modifier \\\"" + "{safe_mod}" + "\\\" not found on " + obj.name
        ) else (
            "Object not found: {safe}"
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
