"""Plugin-specific verified workflows built on top of generic discovery tools."""

from __future__ import annotations

import json

from src.helpers.maxscript import safe_string

from ..server import client, mcp
from ..coerce import StrList, FloatList, IntList


def _load_json(raw: str, fallback):
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _json_array(items: list[str]) -> str:
    return "#(" + ", ".join(f'"{safe_string(item)}"' for item in items) + ")"


def _point3_expr(values: list[float] | None, default: list[float]) -> str:
    point = values or default
    return "[{0},{1},{2}]".format(float(point[0]), float(point[1]), float(point[2]))


def _point2_expr(values: list[int] | None, default: list[int]) -> str:
    point = values or default
    return "[{0},{1}]".format(int(point[0]), int(point[1]))


def _birth_settings_expr(birth_mode: str, birth_amount: int) -> str:
    mode = (birth_mode or "total").strip().lower()
    if mode == "per_frame":
        return (
            "birthOp.birthMode = 1\n"
            f"            birthOp.birthPerFrame = {int(birth_amount)}"
        )
    return (
        "birthOp.birthMode = 0\n"
        f"            birthOp.birthTotal = {int(birth_amount)}"
    )


def _inspected_plugin_payload(name: str) -> tuple[dict | None, dict | None]:
    from .inspect import inspect_object
    from .plugins import inspect_plugin_instance

    if not name:
        return None, None

    raw_object = inspect_object(name)
    raw_plugin = inspect_plugin_instance(name, detail="normal")
    return (
        _load_json(raw_object, {"raw": raw_object}),
        _load_json(raw_plugin, {"raw": raw_plugin}),
    )


def _execute_tyflow_recipe(
    flow_name: str,
    position_expr: str,
    event_name: str,
    event_position_expr: str,
    birth_settings_expr: str,
    source_names: list[str],
    recipe_name: str,
) -> dict:
    safe_flow_name = safe_string(flow_name)
    safe_event_name = safe_string(event_name)
    source_names_expr = _json_array(source_names)

    maxscript = f"""(
        local esc = MCP_Server.escapeJsonString
        if tyFlow == undefined then (
            "{{\\"error\\":\\"tyFlow plugin is not available\\"}}"
        ) else (
            if "{safe_flow_name}" != "" and (getNodeByName "{safe_flow_name}") != undefined then (
                "{{\\"error\\":\\"Object already exists: {safe_flow_name}\\"}}"
            ) else (
                local flow = if "{safe_flow_name}" == "" then tyFlow pos:{position_expr} else tyFlow name:"{safe_flow_name}" pos:{position_expr}
                local eventHandle = flow.tyFlow.addEvent()
                local eventInterface = eventHandle.Event
                eventInterface.setName "{safe_event_name}"
                eventInterface.setPosition {event_position_expr}

                local birthOp = eventInterface.addOperator "Birth" 0
                birthOp.Operator.setName "Birth"
                {birth_settings_expr}

                local sourceNames = {source_names_expr}
                local sourceObjects = #()
                local missingNames = #()
                for sourceName in sourceNames do (
                    local src = getNodeByName sourceName
                    if src == undefined then append missingNames sourceName else append sourceObjects src
                )

                local operatorsJson = "[{{\\"type\\":\\"Birth\\",\\"name\\":\\"" + (esc (birthOp.Operator.getName())) + "\\"}}"
                if sourceObjects.count > 0 then (
                    local posOp = eventInterface.addOperator "Position Object" 1
                    posOp.Operator.setName "Position Object"
                    posOp.objectList = sourceObjects
                    operatorsJson += ",{{\\"type\\":\\"Position Object\\",\\"name\\":\\"" + (esc (posOp.Operator.getName())) + "\\",\\"objectCount\\":" + (sourceObjects.count as string) + "}}"
                )
                operatorsJson += "]"

                local missingJson = "["
                for i = 1 to missingNames.count do (
                    if i > 1 do missingJson += ","
                    missingJson += "\\"" + (esc missingNames[i]) + "\\""
                )
                missingJson += "]"
                local eventPos = eventInterface.getPosition()
                local eventPosX = ((eventPos.x as integer) as string)
                local eventPosY = ((eventPos.y as integer) as string)

                "{{\\"recipe\\":\\"" + (esc "{recipe_name}") + "\\"," + \
                  "\\"flow\\":\\"" + (esc flow.name) + "\\"," + \
                  "\\"event\\":{{\\"name\\":\\"" + (esc (eventInterface.getName())) + "\\",\\"position\\":[" + eventPosX + "," + eventPosY + "]}}," + \
                  "\\"operators\\":" + operatorsJson + "," + \
                  "\\"missingSourceNames\\":" + missingJson + \
                "}}"
            )
        )
    )"""
    response = client.send_command(maxscript)
    return _load_json(response.get("result", "{}"), {"raw": response.get("result", "")})


@mcp.tool()
def create_tyflow_basic_verified(
    name: str = "",
    position: FloatList | None = None,
    event_name: str = "Emit",
    event_position: IntList | None = None,
    birth_mode: str = "total",
    birth_amount: int = 100,
    select_created: bool = True,
) -> str:
    """Create a basic tyFlow object with one event and a Birth operator."""
    from .selection import select_objects
    from .snapshots import get_scene_delta

    before_delta = _load_json(get_scene_delta(capture=True), {})
    recipe_result = _execute_tyflow_recipe(
        flow_name=name,
        position_expr=_point3_expr(position, [0.0, 0.0, 0.0]),
        event_name=event_name,
        event_position_expr=_point2_expr(event_position, [0, 0]),
        birth_settings_expr=_birth_settings_expr(birth_mode, birth_amount),
        source_names=[],
        recipe_name="basic_birth",
    )
    delta = _load_json(get_scene_delta(), {})

    created_name = recipe_result.get("flow")
    select_result = ""
    if select_created and created_name:
        select_result = select_objects(names=[created_name])
    object_payload, plugin_payload = _inspected_plugin_payload(created_name)

    return json.dumps({
        "recipe": "tyflow_basic_birth",
        "baseline": before_delta,
        "createResult": recipe_result,
        "selectResult": select_result,
        "delta": delta,
        "object": object_payload,
        "plugin": plugin_payload,
    })


@mcp.tool()
def create_tyflow_scatter_from_objects_verified(
    source_names: StrList,
    flow_name: str = "",
    position: FloatList | None = None,
    event_name: str = "Scatter",
    event_position: IntList | None = None,
    birth_mode: str = "total",
    birth_amount: int = 100,
    select_created: bool = True,
) -> str:
    """Create a tyFlow object with Birth and Position Object operators wired to scene objects."""
    from .selection import select_objects
    from .snapshots import get_scene_delta

    before_delta = _load_json(get_scene_delta(capture=True), {})
    recipe_result = _execute_tyflow_recipe(
        flow_name=flow_name,
        position_expr=_point3_expr(position, [0.0, 0.0, 0.0]),
        event_name=event_name,
        event_position_expr=_point2_expr(event_position, [0, 0]),
        birth_settings_expr=_birth_settings_expr(birth_mode, birth_amount),
        source_names=source_names,
        recipe_name="scatter_from_objects",
    )
    delta = _load_json(get_scene_delta(), {})

    created_name = recipe_result.get("flow")
    select_result = ""
    if select_created and created_name:
        select_result = select_objects(names=[created_name])
    object_payload, plugin_payload = _inspected_plugin_payload(created_name)

    return json.dumps({
        "recipe": "tyflow_scatter_from_objects",
        "createResult": recipe_result,
        "selectResult": select_result,
        "delta": delta,
        "object": object_payload,
        "plugin": plugin_payload,
    })
