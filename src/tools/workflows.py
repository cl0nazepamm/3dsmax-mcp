"""Higher-level context and action+verify workflow tools."""

from __future__ import annotations

import json

from ..server import mcp, client
from ..coerce import StrList, FloatList


def _load_json(raw: str, fallback):
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _inspect_objects(names: list[str]) -> list[dict]:
    from .inspect import inspect_object

    inspected = []
    for name in names:
        raw = inspect_object(name)
        inspected.append(_load_json(raw, {"name": name, "raw": raw}))
    return inspected


def _slot_map(slot_payload: dict, keys: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in keys:
        values = slot_payload.get(key, [])
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict) and "name" in item:
                    result[str(item["name"])] = str(item.get("value"))
    return result


def _find_modifier_index(modifiers: list[dict], modifier_hint: str) -> int:
    hint = (modifier_hint or "").strip().lower()
    if not hint:
        return 1 if modifiers else 0

    for index, mod in enumerate(modifiers, start=1):
        mod_class = str(mod.get("class", "")).strip().lower()
        mod_name = str(mod.get("name", "")).strip().lower()
        if mod_class == hint or mod_name == hint or hint in mod_name:
            return index

    return 1 if modifiers else 0


def _modifier_summary(modifiers: list[dict], modifier_hint: str) -> dict | None:
    index = _find_modifier_index(modifiers, modifier_hint)
    if index <= 0 or len(modifiers) < index:
        return None
    mod = modifiers[index - 1]
    return {
        "name": mod.get("name"),
        "class": mod.get("class"),
        "enabled": mod.get("enabled"),
        "enabledInViews": mod.get("enabledInViews"),
        "enabledInRenders": mod.get("enabledInRenders"),
        "index": index,
    }


def _inspect_modifier_from_object(inspected_object: dict, object_name: str, modifier_hint: str):
    from .inspect import inspect_modifier_properties

    modifiers = inspected_object.get("modifiers", [])
    if not modifiers:
        return None

    modifier_index = _find_modifier_index(modifiers, modifier_hint)
    raw_modifier = inspect_modifier_properties(object_name, modifier_index=modifier_index)
    return _load_json(raw_modifier, {"raw": raw_modifier, "modifierIndex": modifier_index})


@mcp.tool()
def inspect_active_target(
    detail: str = "normal",
    max_selection: int = 10,
    max_roots: int = 10,
) -> str:
    """Inspect the most relevant current target in 3ds Max.

    This is the context-aware readback entry point:
    - if one object is selected, inspect that object deeply
    - if multiple objects are selected, summarize the selection
    - if nothing is selected and the scene has one root, inspect that root
    - otherwise return compact scene context

    Args:
        detail: "summary", "normal", or "full".
        max_selection: Max selected objects to include in summaries.
        max_roots: Max root names to include in scene summaries.
    """
    from .bridge import get_bridge_status
    from .inspect import inspect_object
    from .session_context import get_session_context
    from .snapshots import get_scene_snapshot, get_selection_snapshot

    detail = (detail or "normal").strip().lower()
    if detail not in {"summary", "normal", "full"}:
        detail = "normal"

    selection = _load_json(get_selection_snapshot(max_items=max_selection), {"selected": 0, "objects": []})
    selected = int(selection.get("selected", 0))

    if selected == 1 and selection.get("objects"):
        target = selection["objects"][0]
        result = {
            "mode": "single_selection",
            "selection": selection,
            "targetName": target.get("name"),
            "target": _load_json(inspect_object(target.get("name", "")), {"raw": inspect_object(target.get("name", ""))}),
        }
        if detail in {"normal", "full"}:
            result["bridge"] = _load_json(get_bridge_status(), {})
        return json.dumps(result)

    if selected > 1:
        result = {
            "mode": "multi_selection",
            "selection": selection,
        }
        if detail in {"normal", "full"}:
            result["scene"] = _load_json(get_scene_snapshot(max_roots=max_roots), {})
            result["bridge"] = _load_json(get_bridge_status(), {})
        return json.dumps(result)

    scene = _load_json(get_scene_snapshot(max_roots=max_roots), {})
    root_names = scene.get("roots", [])
    if scene.get("objectCount") == 1 and root_names:
        root_name = root_names[0]
        result = {
            "mode": "single_root",
            "scene": scene,
            "targetName": root_name,
            "target": _load_json(inspect_object(root_name), {"raw": inspect_object(root_name)}),
        }
        if detail in {"normal", "full"}:
            result["bridge"] = _load_json(get_bridge_status(), {})
        return json.dumps(result)

    if detail == "summary":
        return json.dumps({
            "mode": "scene_summary",
            "scene": scene,
        })

    return json.dumps({
        "mode": "session_context",
        "context": _load_json(
            get_session_context(max_roots=max_roots, max_selection=max_selection),
            {},
        ),
    })


@mcp.tool()
def create_object_verified(
    type: str,
    name: str = "",
    params: str = "",
    select_created: bool = True,
) -> str:
    """Create an object, then verify it via delta and inspection."""
    from .inspect import inspect_object
    from .objects import create_object
    from .selection import select_objects
    from .snapshots import get_scene_delta

    _load_json(get_scene_delta(capture=True), {})
    created_name = create_object(type=type, name=name, params=params)
    delta = _load_json(get_scene_delta(), {})

    if select_created and created_name:
        select_result = select_objects(names=[created_name])
    else:
        select_result = ""

    return json.dumps({
        "created": created_name,
        "selectResult": select_result,
        "delta": delta,
        "object": _load_json(inspect_object(created_name), {"raw": inspect_object(created_name)}),
    })


@mcp.tool()
def assign_material_verified(
    names: StrList,
    material_class: str,
    material_name: str = "",
    params: str = "",
) -> str:
    """Assign a material, then verify the assignment on the target objects."""
    from .inspect import inspect_object
    from .material_ops import assign_material, get_material_slots
    from .snapshots import get_scene_delta

    _load_json(get_scene_delta(capture=True), {})
    assign_result = assign_material(
        names=names,
        material_class=material_class,
        material_name=material_name,
        params=params,
    )
    delta = _load_json(get_scene_delta(), {})

    slot_summary = None
    if names:
        raw_slots = get_material_slots(names[0], slot_scope="summary", include_values=True)
        slot_summary = _load_json(raw_slots, {"raw": raw_slots})

    return json.dumps({
        "assignResult": assign_result,
        "delta": delta,
        "objects": _inspect_objects(names),
        "materialSlots": slot_summary,
    })


@mcp.tool()
def set_material_verified(
    name: str,
    properties: dict[str, str],
    sub_material_index: int = 0,
) -> str:
    """Set material properties, then verify via delta, object readback, and slot summary."""
    if client.native_available:
        response = client.send_command(
            json.dumps({
                "name": name,
                "properties": properties,
                "sub_material_index": sub_material_index,
            }),
            cmd_type="native:set_material_verified",
        )
        return response.get("result", "")

    from .material_ops import get_material_slots, set_material_properties
    from .snapshots import get_scene_delta

    raw_before_slots = get_material_slots(
        name,
        sub_material_index=sub_material_index,
        slot_scope="all",
        include_values=True,
        max_per_group=50,
    )
    before_slots = _load_json(raw_before_slots, {"raw": raw_before_slots})

    _load_json(get_scene_delta(capture=True), {})
    set_result = set_material_properties(
        name=name,
        properties=properties,
        sub_material_index=sub_material_index,
    )
    delta = _load_json(get_scene_delta(), {})
    raw_slots = get_material_slots(
        name,
        sub_material_index=sub_material_index,
        slot_scope="all",
        include_values=True,
        max_per_group=50,
    )
    after_slots = _load_json(raw_slots, {"raw": raw_slots})

    before_map = _slot_map(before_slots, ["mapSlots", "colorSlots", "numericSlots", "boolSlots", "otherSlots"])
    after_map = _slot_map(after_slots, ["mapSlots", "colorSlots", "numericSlots", "boolSlots", "otherSlots"])
    slot_changes = {}
    for prop in properties.keys():
        prop_name = str(prop)
        slot_changes[prop_name] = {
            "before": before_map.get(prop_name),
            "after": after_map.get(prop_name),
        }

    return json.dumps({
        "setResult": set_result,
        "delta": delta,
        "object": _inspect_objects([name])[0],
        "slotChanges": slot_changes,
        "materialSlotsBefore": before_slots,
        "materialSlots": after_slots,
    })


@mcp.tool()
def add_modifier_verified(
    name: str,
    modifier: str,
    params: str = "",
) -> str:
    """Add a modifier, then verify via delta and object readback."""
    if client.native_available:
        response = client.send_command(
            json.dumps({
                "name": name,
                "modifier": modifier,
                "params": params,
            }),
            cmd_type="native:add_modifier_verified",
        )
        return response.get("result", "")

    from .inspect import inspect_modifier_properties
    from .modifiers import add_modifier
    from .snapshots import get_scene_delta

    _load_json(get_scene_delta(capture=True), {})
    add_result = add_modifier(name=name, modifier=modifier, params=params)
    delta = _load_json(get_scene_delta(), {})
    inspected_object = _inspect_objects([name])[0]

    return json.dumps({
        "addResult": add_result,
        "delta": delta,
        "object": inspected_object,
        "modifier": _inspect_modifier_from_object(inspected_object, name, modifier),
    })


@mcp.tool()
def transform_object_verified(
    name: str,
    move: FloatList | None = None,
    rotate: FloatList | None = None,
    scale: FloatList | None = None,
    coordinate_system: str = "world",
) -> str:
    """Transform an object, then verify via delta and object readback."""
    from .snapshots import get_scene_delta
    from .transform import transform_object

    _load_json(get_scene_delta(capture=True), {})
    transform_result = transform_object(
        name=name,
        move=move,
        rotate=rotate,
        scale=scale,
        coordinate_system=coordinate_system,
    )
    delta = _load_json(get_scene_delta(), {})

    return json.dumps({
        "transformResult": transform_result,
        "delta": delta,
        "object": _inspect_objects([name])[0],
    })


@mcp.tool()
def set_modifier_state_verified(
    name: str,
    modifier_name: str = "",
    modifier_index: int = 0,
    enabled: bool | None = None,
    enabled_in_views: bool | None = None,
    enabled_in_renders: bool | None = None,
) -> str:
    """Set modifier state, then verify via object readback and modifier inspection."""
    from .modifiers import set_modifier_state
    from .snapshots import get_scene_delta

    before_object = _inspect_objects([name])[0]
    before_modifier = _modifier_summary(before_object.get("modifiers", []), modifier_name if modifier_name else "")

    _load_json(get_scene_delta(capture=True), {})
    state_result = set_modifier_state(
        name=name,
        modifier_name=modifier_name,
        modifier_index=modifier_index,
        enabled=enabled,
        enabled_in_views=enabled_in_views,
        enabled_in_renders=enabled_in_renders,
    )
    delta = _load_json(get_scene_delta(), {})
    inspected_object = _inspect_objects([name])[0]

    modifier_hint = modifier_name
    if not modifier_hint and modifier_index > 0:
        modifiers = inspected_object.get("modifiers", [])
        if len(modifiers) >= modifier_index:
            modifier_hint = str(modifiers[modifier_index - 1].get("name", ""))
    after_modifier = _modifier_summary(inspected_object.get("modifiers", []), modifier_hint)

    state_changes = {}
    if before_modifier or after_modifier:
        for key in ("enabled", "enabledInViews", "enabledInRenders"):
            state_changes[key] = {
                "before": None if before_modifier is None else before_modifier.get(key),
                "after": None if after_modifier is None else after_modifier.get(key),
            }

    return json.dumps({
        "stateResult": state_result,
        "delta": delta,
        "object": inspected_object,
        "modifierStateBefore": before_modifier,
        "modifierStateChanges": state_changes,
        "modifier": _inspect_modifier_from_object(inspected_object, name, modifier_hint),
    })


@mcp.tool()
def set_object_property_verified(
    name: str,
    property: str,
    value: str,
) -> str:
    """Set an object property, then verify via delta and object readback."""
    from .objects import set_object_property
    from .snapshots import get_scene_delta

    before_object = _inspect_objects([name])[0]

    _load_json(get_scene_delta(capture=True), {})
    set_result = set_object_property(name=name, property=property, value=value)
    delta = _load_json(get_scene_delta(), {})
    after_object = _inspect_objects([name])[0]

    return json.dumps({
        "setResult": set_result,
        "property": property,
        "valueExpression": value,
        "delta": delta,
        "objectBefore": before_object,
        "object": after_object,
    })
