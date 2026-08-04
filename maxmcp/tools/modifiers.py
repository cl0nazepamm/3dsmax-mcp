from typing import Optional
import json as _json
from ..server import mcp, client
from ..coerce import StrList
from ..helpers.maxscript import safe_string


def _modifier_property_payload(
    *,
    property_name: str,
    property_value: str,
    name: str = "",
    names: Optional[StrList] = None,
    selection_only: bool = False,
    modifier_class: str = "",
    modifier_name: str = "",
    modifier_index: int = 0,
) -> dict:
    payload: dict = {
        "property_name": property_name,
        "property_value": property_value,
        "selection_only": selection_only,
    }
    if name:
        payload["name"] = name
    if names:
        payload["names"] = names
    if modifier_class:
        payload["modifier_class"] = modifier_class
    if modifier_name:
        payload["modifier_name"] = modifier_name
    if modifier_index > 0:
        payload["modifier_index"] = modifier_index
    return payload


def _maxscript_property_value(raw: str) -> str:
    text = raw.strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered
    try:
        float(text)
        return text
    except ValueError:
        return f'"{safe_string(text)}"'


def _format_modifier_property_result(
    property_name: str,
    property_value: str,
    modified: int,
    hits: list[dict] | None = None,
) -> str:
    body: dict = {
        "modified": modified,
        "property": property_name,
        "value": property_value,
    }
    if hits:
        body["hits"] = hits
    return _json.dumps(body)


@mcp.tool()
def add_modifier(name: str = "", modifier: str = "", params: str = "", handle: int = 0) -> str:
    """Add a modifier to an object."""
    if client.native_available:
        try:
            body = {"name": name, "modifier": modifier, "params": params}
            if handle:
                body["handle"] = handle
            payload = _json.dumps(body)
            response = client.send_command(payload, cmd_type="native:add_modifier")
            return response.get("result", "")
        except RuntimeError:
            pass

    if not name:
        return "Object name is required for TCP fallback"

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
def remove_modifier(name: str = "", modifier: str = "", handle: int = 0) -> str:
    """Remove a modifier from an object by name."""
    if client.native_available:
        try:
            body = {"name": name, "modifier": modifier}
            if handle:
                body["handle"] = handle
            payload = _json.dumps(body)
            response = client.send_command(payload, cmd_type="native:remove_modifier")
            return response.get("result", "")
        except RuntimeError:
            pass

    if not name:
        return "Object name is required for TCP fallback"

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
    name: str = "",
    handle: int = 0,
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
            if handle:
                payload["handle"] = handle
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

    if not name:
        return "Object name is required for TCP fallback"

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
    name: str = "",
    handle: int = 0,
    to_index: int = 0,
    dry_run: bool = False,
) -> str:
    """Collapse the modifier stack on an object."""
    if client.native_available:
        try:
            body = {"name": name, "to_index": to_index, "dry_run": dry_run}
            if handle:
                body["handle"] = handle
            payload = _json.dumps(body)
            response = client.send_command(payload, cmd_type="native:collapse_modifier_stack")
            return response.get("result", "")
        except RuntimeError:
            pass

    if not name:
        return "Object name is required for TCP fallback"

    safe = safe_string(name)
    if to_index > 0:
        maxscript = f"""(
            local obj = getNodeByName "{safe}"
            if obj != undefined then (
                if {to_index} <= obj.modifiers.count then (
                    if not {str(dry_run).lower()} do maxOps.CollapseNodeTo obj {to_index} off
                    (if {str(dry_run).lower()} then "Would collapse " else "Collapsed ") + obj.name + " to modifier index {to_index}"
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
                if not {str(dry_run).lower()} do maxOps.CollapseNode obj off
                (if {str(dry_run).lower()} then "Would collapse entire stack on " else "Collapsed entire stack on ") + obj.name + " — now: " + ((classof obj.baseobject) as string)
            ) else (
                "Object not found: {safe}"
            )
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def make_modifier_unique(name: str = "", modifier_index: int = 0, handle: int = 0) -> str:
    """Make an instanced modifier unique (de-instance it)."""
    if client.native_available:
        try:
            body = {"name": name, "modifier_index": modifier_index}
            if handle:
                body["handle"] = handle
            payload = _json.dumps(body)
            response = client.send_command(payload, cmd_type="native:make_modifier_unique")
            return response.get("result", "")
        except RuntimeError:
            pass

    if not name:
        return "Object name is required for TCP fallback"

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
def set_modifier_property(
    property_name: str,
    property_value: str,
    name: str = "",
    names: Optional[StrList] = None,
    selection_only: bool = False,
    modifier_class: str = "",
    modifier_name: str = "",
    modifier_index: int = 0,
) -> str:
    """Set a modifier parameter on one object or many.

    Object scope: pass `name` for one node, `names` for several, or `selection_only=true`.
    Leave all three empty only when using `modifier_class` to update every matching modifier in the scene.

    Modifier target (pick one):
    - `modifier_index` (1-based stack index) — best for a single known modifier
    - `modifier_name` — match by modifier stack entry name
    - `modifier_class` — every modifier of that class on each scoped object (batch)

    Examples:
    - One TurboSmooth on Box001: name="Box001", modifier_index=1, property_name="iterations", property_value="2"
    - All Smooth modifiers on selection: selection_only=true, modifier_class="Smooth", property_name="autosmooth", property_value="true"
    """
    payload = _modifier_property_payload(
        property_name=property_name,
        property_value=property_value,
        name=name,
        names=names,
        selection_only=selection_only,
        modifier_class=modifier_class,
        modifier_name=modifier_name,
        modifier_index=modifier_index,
    )

    if client.native_available:
        try:
            response = client.send_command(
                _json.dumps(payload),
                cmd_type="native:set_modifier_property",
            )
            return response.get("result", "")
        except RuntimeError:
            pass

    if modifier_index <= 0 and not modifier_name and not modifier_class:
        return _json.dumps({
            "error": "Specify modifier_index, modifier_name, or modifier_class to choose a modifier",
        })

    target_names: list[str] = []
    if name:
        target_names.append(name)
    if names:
        target_names.extend(names)

    if not target_names and not selection_only and modifier_index <= 0 and not modifier_name:
        if not modifier_class:
            return _json.dumps({
                "error": "Specify name, names, or selection_only to choose objects",
            })

    safe_prop = safe_string(property_name)
    ms_value = _maxscript_property_value(str(property_value))

    if target_names:
        name_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in target_names) + ")"
        collect_line = (
            f"local objsel = for n in {name_arr} "
            "where (getNodeByName n) != undefined collect (getNodeByName n)"
        )
    elif selection_only:
        collect_line = "local objsel = selection as array"
    else:
        collect_line = "local objsel = objects as array"

    if modifier_index > 0:
        mod_filter = f"if m == {modifier_index} do ("
        mod_filter_close = ")"
    elif modifier_name:
        safe_mod = safe_string(modifier_name)
        mod_filter = f'if obj.modifiers[m].name == "{safe_mod}" do ('
        mod_filter_close = ")"
    elif modifier_class:
        safe_class = safe_string(modifier_class)
        mod_filter = f"if (classof obj.modifiers[m]) == {safe_class} do ("
        mod_filter_close = ")"
    else:
        mod_filter = ""
        mod_filter_close = ""

    maxscript = f"""(
        disableSceneRedraw()
        undo "Set Modifier Property {safe_prop}" on (
            {collect_line}
            local modCount = 0
            for obj in objsel do (
                for m = 1 to obj.modifiers.count do (
                    {mod_filter}
                        try (
                            obj.modifiers[m].{safe_prop} = {ms_value}
                            modCount += 1
                        ) catch ()
                    {mod_filter_close}
                )
            )
        )
        enableSceneRedraw()
        redrawViews()
        modCount as string
    )"""
    response = client.send_command(maxscript)
    raw = response.get("result", "0")
    try:
        modified = int(str(raw).strip())
    except ValueError:
        return _json.dumps({"error": str(raw)})

    if modified == 0:
        return _json.dumps({
            "error": "No modifiers updated — check object names, modifier target, and property name",
        })

    return _format_modifier_property_result(property_name, str(property_value), modified)
