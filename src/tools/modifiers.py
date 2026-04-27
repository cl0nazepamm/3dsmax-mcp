from typing import Optional
import json as _json
from ..server import mcp, client
from ..coerce import StrList
from src.helpers.maxscript import safe_string


@mcp.tool()
def add_modifier(name: str, modifier: str, params: str = "") -> str:
    """Add a modifier to an object."""
    if client.native_available:
        try:
            payload = _json.dumps({"name": name, "modifier": modifier, "params": params})
            response = client.send_command(payload, cmd_type="native:add_modifier")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)
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
    """Remove a modifier from an object by name."""
    if client.native_available:
        try:
            payload = _json.dumps({"name": name, "modifier": modifier})
            response = client.send_command(payload, cmd_type="native:remove_modifier")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)
    safe_mod = safe_string(modifier)
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


@mcp.tool()
def set_modifier_state(
    name: str,
    modifier_name: str = "",
    modifier_index: int = 0,
    enabled: Optional[bool] = None,
    enabled_in_views: Optional[bool] = None,
    enabled_in_renders: Optional[bool] = None,
) -> str:
    """Set the enable state of a modifier with viewport/render granularity."""
    if client.native_available:
        try:
            payload = {"name": name, "modifier_name": modifier_name, "modifier_index": modifier_index}
            if enabled is not None:
                payload["enabled"] = enabled
            if enabled_in_views is not None:
                payload["enabled_in_views"] = enabled_in_views
            if enabled_in_renders is not None:
                payload["enabled_in_renders"] = enabled_in_renders
            response = client.send_command(_json.dumps(payload), cmd_type="native:set_modifier_state")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)

    if modifier_index > 0:
        find_mod = f"local mod = obj.modifiers[{modifier_index}]"
    else:
        safe_mod = safe_string(modifier_name)
        find_mod = f"""local mod = undefined
            for i = 1 to obj.modifiers.count do (
                if obj.modifiers[i].name == "{safe_mod}" do (mod = obj.modifiers[i]; exit)
            )"""

    ops = []
    if enabled is not None:
        ops.append(f"mod.enabled = {'true' if enabled else 'false'}")
    if enabled_in_views is not None:
        ops.append(f"mod.enabledInViews = {'true' if enabled_in_views else 'false'}")
    if enabled_in_renders is not None:
        ops.append(f"mod.enabledInRenders = {'true' if enabled_in_renders else 'false'}")

    if not ops:
        return "No state changes specified."

    ops_str = "\n                ".join(ops)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj != undefined then (
            {find_mod}
            if mod != undefined then (
                {ops_str}
                "Set state on " + mod.name + " (" + obj.name + "): " + \
                "enabled=" + (mod.enabled as string) + \
                " views=" + (mod.enabledInViews as string) + \
                " renders=" + (mod.enabledInRenders as string)
            ) else (
                "Modifier not found on " + obj.name
            )
        ) else (
            "Object not found: {safe}"
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def collapse_modifier_stack(
    name: str,
    to_index: int = 0,
) -> str:
    """Collapse the modifier stack on an object."""
    if client.native_available:
        try:
            payload = _json.dumps({"name": name, "to_index": to_index})
            response = client.send_command(payload, cmd_type="native:collapse_modifier_stack")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)
    if to_index > 0:
        maxscript = f"""(
            local obj = getNodeByName "{safe}"
            if obj != undefined then (
                if {to_index} <= obj.modifiers.count then (
                    maxOps.CollapseNodeTo obj {to_index} off
                    "Collapsed " + obj.name + " to modifier index {to_index}"
                ) else (
                    "Index {to_index} out of range (stack has " + (obj.modifiers.count as string) + " modifiers)"
                )
            ) else (
                "Object not found: {safe}"
            )
        )"""
    else:
        maxscript = f"""(
            local obj = getNodeByName "{safe}"
            if obj != undefined then (
                maxOps.CollapseNode obj off
                "Collapsed entire stack on " + obj.name + " — now: " + ((classof obj.baseobject) as string)
            ) else (
                "Object not found: {safe}"
            )
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def make_modifier_unique(name: str, modifier_index: int) -> str:
    """Make an instanced modifier unique (de-instance it)."""
    if client.native_available:
        try:
            payload = _json.dumps({"name": name, "modifier_index": modifier_index})
            response = client.send_command(payload, cmd_type="native:make_modifier_unique")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj != undefined then (
            if {modifier_index} <= obj.modifiers.count then (
                local mod = obj.modifiers[{modifier_index}]
                InstanceMgr.makemodifiersunique obj mod #individual
                "Made modifier " + mod.name + " unique on " + obj.name
            ) else (
                "Index {modifier_index} out of range"
            )
        ) else (
            "Object not found: {safe}"
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def batch_modify(
    modifier_class: str,
    property_name: str,
    property_value: str,
    names: Optional[StrList] = None,
    selection_only: bool = False,
) -> str:
    """Batch-set a property on all modifiers of a given class across multiple objects."""
    if client.native_available:
        try:
            payload = {
                "modifier_class": modifier_class,
                "property_name": property_name,
                "property_value": property_value,
                "selection_only": selection_only,
            }
            if names:
                payload["names"] = names
            response = client.send_command(_json.dumps(payload), cmd_type="native:batch_modify")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe_class = safe_string(modifier_class)
    safe_prop = safe_string(property_name)

    if names:
        name_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in names) + ")"
        collect_line = f"local objsel = for n in {name_arr} where (getNodeByName n) != undefined collect (getNodeByName n)"
    elif selection_only:
        collect_line = "local objsel = selection as array"
    else:
        collect_line = "local objsel = objects as array"

    maxscript = f"""(
        disableSceneRedraw()
        undo "Batch Modify {safe_class}.{safe_prop}" on (
            {collect_line}
            local modCount = 0
            local targetClass = {safe_class}
            for obj in objsel do (
                for m = 1 to obj.modifiers.count do (
                    if (classof obj.modifiers[m]) == targetClass do (
                        try (
                            obj.modifiers[m].{safe_prop} = {property_value}
                            modCount += 1
                        ) catch ()
                    )
                )
            )
        )
        enableSceneRedraw()
        redrawViews()
        "Modified " + (modCount as string) + " {safe_class} modifiers: {safe_prop} = {property_value}"
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
