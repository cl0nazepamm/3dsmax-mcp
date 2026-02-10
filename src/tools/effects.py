"""Atmospheric and render effects management tools for 3ds Max.

Provides access to scene-level atmospherics (fog, volume light, fire, etc.)
and render effects (lens effects, blur, etc.) which are global to the scene,
not per-object.
"""

from ..server import mcp, client


@mcp.tool()
def get_effects() -> str:
    """List all atmospheric effects and render effects in the scene.

    Use this when asked about fog, volume effects, lens effects, or any
    scene-level post-processing. These are NOT per-object — they're global
    scene effects. Atmospherics include Fog, Volume Fog, Volume Light,
    Fire Effect, etc. Render effects include Lens Effects, Blur, etc.

    Returns:
        JSON with atmospheric and render effect lists, including class,
        active state, and which scene objects reference each effect.
    """
    maxscript = r"""(
        local result = "{\"atmospherics\": ["
        for i = 1 to numAtmospherics do (
            if i > 1 do result += ","
            local atm = getAtmospheric i
            local isActive = true
            try (isActive = (isActive atm)) catch ()
            local depNodes = refs.dependentnodes atm
            local nodeStr = "["
            for j = 1 to depNodes.count do (
                if j > 1 do nodeStr += ","
                nodeStr += "\"" + depNodes[j].name + "\""
            )
            nodeStr += "]"
            local atmName = ""
            try (atmName = atm.name) catch (atmName = (classof atm) as string)
            result += "{\"index\": " + (i as string) + ", \"name\": \"" + atmName + "\", \"class\": \"" + ((classof atm) as string) + "\", \"active\": " + (if isActive then "true" else "false") + ", \"usedByNodes\": " + nodeStr + "}"
        )
        result += "], \"renderEffects\": ["
        for i = 1 to numEffects do (
            if i > 1 do result += ","
            local eff = getEffect i
            local isActive = true
            try (isActive = (isActive eff)) catch ()
            local effName = ""
            try (effName = eff.name) catch (effName = (classof eff) as string)
            result += "{\"index\": " + (i as string) + ", \"name\": \"" + effName + "\", \"class\": \"" + ((classof eff) as string) + "\", \"active\": " + (if isActive then "true" else "false") + "}"
        )
        result += "]}"
        result
    )"""
    response = client.send_command(maxscript)
    return response.get("result", '{"atmospherics":[],"renderEffects":[]}')


@mcp.tool()
def toggle_effect(
    index: int,
    effect_type: str = "atmospheric",
    active: bool = True,
) -> str:
    """Enable or disable an atmospheric or render effect by index.

    Args:
        index: 1-based index of the effect (from get_effects).
        effect_type: "atmospheric" or "render_effect".
        active: True to enable, False to disable.

    Returns confirmation.
    """
    active_str = "true" if active else "false"

    if effect_type == "atmospheric":
        maxscript = f"""(
            if {index} > numAtmospherics then (
                "Index {index} out of range (total: " + (numAtmospherics as string) + ")"
            ) else (
                local atm = getAtmospheric {index}
                setActive atm {active_str}
                "Set atmospheric " + ((classof atm) as string) + " active = " + ((isActive atm) as string)
            )
        )"""
    else:
        maxscript = f"""(
            if {index} > numEffects then (
                "Index {index} out of range (total: " + (numEffects as string) + ")"
            ) else (
                local eff = getEffect {index}
                setActive eff {active_str}
                "Set render effect " + ((classof eff) as string) + " active = " + ((isActive eff) as string)
            )
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def delete_effect(
    index: int,
    effect_type: str = "atmospheric",
) -> str:
    """Delete an atmospheric or render effect by index.

    Args:
        index: 1-based index of the effect (from get_effects).
        effect_type: "atmospheric" or "render_effect".

    Returns confirmation.
    """
    if effect_type == "atmospheric":
        maxscript = f"""(
            if {index} > numAtmospherics then (
                "Index {index} out of range (total: " + (numAtmospherics as string) + ")"
            ) else (
                local atm = getAtmospheric {index}
                local cn = (classof atm) as string
                deleteAtmospheric {index}
                "Deleted atmospheric " + cn + " at index {index}"
            )
        )"""
    else:
        maxscript = f"""(
            if {index} > numEffects then (
                "Index {index} out of range (total: " + (numEffects as string) + ")"
            ) else (
                local eff = getEffect {index}
                local cn = (classof eff) as string
                deleteEffect {index}
                "Deleted render effect " + cn + " at index {index}"
            )
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
