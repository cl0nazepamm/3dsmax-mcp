"""Atmospheric and render effects management tools for 3ds Max.

Provides access to scene-level atmospherics (fog, volume light, fire, etc.)
and render effects (lens effects, blur, etc.) which are global to the scene,
not per-object.
"""

import json
from ..server import mcp, client


@mcp.tool()
def get_effects() -> str:
    """List all atmospheric effects and render effects in the scene."""
    if client.native_available:
        response = client.send_command("{}", cmd_type="native:get_effects")
        return response.get("result", '{"atmospherics":[],"renderEffects":[]}')

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
    """Enable or disable an atmospheric or render effect by index."""
    if client.native_available:
        payload = json.dumps({"index": index, "effect_type": effect_type, "active": active})
        response = client.send_command(payload, cmd_type="native:toggle_effect")
        return response.get("result", "")

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
    """Delete an atmospheric or render effect by index."""
    if client.native_available:
        payload = json.dumps({"index": index, "effect_type": effect_type})
        response = client.send_command(payload, cmd_type="native:delete_effect")
        return response.get("result", "")

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
