from typing import Optional
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


@mcp.tool()
def set_modifier_state(
    name: str,
    modifier_name: str = "",
    modifier_index: int = 0,
    enabled: Optional[bool] = None,
    enabled_in_views: Optional[bool] = None,
    enabled_in_renders: Optional[bool] = None,
) -> str:
    """Set the enable state of a modifier with viewport/render granularity.

    Use this instead of execute_maxscript when you need to toggle modifiers
    on/off — e.g. disable TurboSmooth in viewport for performance but keep
    it for render, or temporarily disable a Bend to see the base shape.

    3ds Max modifiers have three independent enable flags:
    - enabled: master on/off
    - enabledInViews: active in viewport only
    - enabledInRenders: active in renders only

    Args:
        name: The object name.
        modifier_name: Modifier name to find (e.g. "TurboSmooth 1"). Ignored if modifier_index is set.
        modifier_index: 1-based modifier stack index. Takes priority over modifier_name.
        enabled: Set master enabled state.
        enabled_in_views: Set viewport-only enabled state.
        enabled_in_renders: Set render-only enabled state.

    Returns confirmation.
    """
    safe = _safe_name(name)

    if modifier_index > 0:
        find_mod = f"local mod = obj.modifiers[{modifier_index}]"
    else:
        safe_mod = _safe_name(modifier_name)
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
    """Collapse the modifier stack on an object.

    Use this to bake modifiers into the mesh — e.g. before boolean operations,
    exporting, or when the parametric stack is no longer needed. Converts to
    Editable Mesh/Poly.

    Args:
        name: The object name.
        to_index: If 0, collapses the entire stack. Otherwise, collapses
                  down to the specified 1-based modifier index.

    Returns confirmation.
    """
    safe = _safe_name(name)
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
    """Make an instanced modifier unique (de-instance it).

    Use this when multiple objects share the same modifier instance and you
    need to change one without affecting the others.

    Args:
        name: The object name.
        modifier_index: 1-based modifier stack index.

    Returns confirmation.
    """
    safe = _safe_name(name)
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
    names: Optional[list[str]] = None,
    selection_only: bool = False,
) -> str:
    """Batch-set a property on all modifiers of a given class across multiple objects.

    Use this for scene-wide modifier changes — e.g. "set all TurboSmooth
    iterations to 0" or "disable all Bend modifiers". Much faster and safer
    than looping via execute_maxscript. Wraps in disableSceneRedraw and undo.

    Args:
        modifier_class: Class name to match (e.g. "TurboSmooth", "Bend").
        property_name: Property to set (e.g. "iterations", "angle").
        property_value: Value as MAXScript expression (e.g. "3", "45.0", "true").
        names: Optional list of specific object names. If empty, uses all or selection.
        selection_only: If True and names is empty, only process selected objects.

    Returns count of modified modifiers.
    """
    safe_class = _safe_name(modifier_class)
    safe_prop = _safe_name(property_name)

    if names:
        name_arr = "#(" + ", ".join(f'"{_safe_name(n)}"' for n in names) + ")"
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
