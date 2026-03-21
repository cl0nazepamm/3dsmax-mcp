from __future__ import annotations

import json
from typing import Any

from src.helpers.maxscript import safe_string

from ..server import client, mcp
from ..coerce import StrList, FloatList, IntList, DictList


SHAPE_3D_IDS: dict[str, int] = {
    "triangle": 0,
    "cone": 1,
    "quad": 2,
    "plane": 2,
    "cylinder": 3,
    "sphere": 4,
    "pyramid": 5,
    "box": 6,
    "cube": 6,
    "octahedron": 7,
    "geosphere_low": 8,
    "geosphere": 9,
    "geosphere_high": 10,
    "icosahedron": 11,
}


KNOWN_OPERATORS: tuple[str, ...] = (
    "Birth",
    "Birth Surface",
    "Birth Objects",
    "Birth Spline",
    "Speed",
    "Spin",
    "Rotation",
    "Scale",
    "Mass",
    "Force",
    "Shape",
    "Display",
    "PhysX Shape",
    "PhysX Collision",
    "Collision",
    "Delete",
    "Spawn",
    "Select",
    "Send Out",
    "Split",
    "Time Test",
    "Object Test",
    "Surface Test",
    "Property Test",
    "Voronoi Fracture",
    "Element Fracture",
    "Face Fracture",
    "Bounds Fracture",
    "Brick Fracture",
    "Multifracture",
    "Convex Hull",
    "Export Particles",
    "Display Data",
    "Position Object",
)


HELPERS = """
local esc = MCP_Server.escapeJsonString

fn jsonStringArray arr =
(
    local s = "["
    for i = 1 to arr.count do (
        if i > 1 do s += ","
        s += "\\"" + (esc arr[i]) + "\\""
    )
    s += "]"
    s
)

fn findEventSubAnim flowNode eventName =
(
    if flowNode == undefined then return undefined
    local bo = flowNode.baseobject
    if bo == undefined then return undefined
    local evSym = undefined
    try (evSym = execute ("#'" + eventName + "'")) catch ()
    if evSym == undefined then return undefined
    local ev = undefined
    try (ev = bo[evSym]) catch ()
    ev
)

fn findOperatorSubAnim eventSub operatorName =
(
    if eventSub == undefined then return undefined
    local opSym = undefined
    try (opSym = execute ("#'" + operatorName + "'")) catch ()
    if opSym == undefined then return undefined
    local op = undefined
    try (op = eventSub[opSym]) catch ()
    op
)
"""


def _load_json(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _send_json(maxscript: str, fallback: Any) -> Any:
    try:
        response = client.send_command(maxscript)
    except Exception as exc:
        return {"error": str(exc)}
    return _load_json(response.get("result", ""), fallback)


def _mxs_string_array(items: list[str]) -> str:
    return "#(" + ", ".join(f'"{safe_string(item)}"' for item in items) + ")"


def _mxs_value(value: Any, raw_strings: bool = False) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(float(value))
    if isinstance(value, str):
        return value if raw_strings else f'"{safe_string(value)}"'
    if isinstance(value, list):
        if not value:
            return "#()"
        if all(isinstance(v, str) for v in value):
            return _mxs_string_array(value)
        if all(isinstance(v, bool) for v in value):
            return "#(" + ", ".join("true" if v else "false" for v in value) + ")"
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return "#(" + ", ".join(str(float(v)) if isinstance(v, float) else str(int(v)) for v in value) + ")"
    raise ValueError(f"Unsupported value type: {type(value).__name__}")


def _assignment_lines(values: dict[str, Any], var_name: str, raw_strings: bool = False) -> tuple[str, list[str]]:
    lines: list[str] = []
    names: list[str] = []
    for prop_name, prop_value in values.items():
        prop = safe_string(prop_name)
        expr = _mxs_value(prop_value, raw_strings=raw_strings)
        lines.append(
            f'try ({var_name}.{prop} = {expr}; append applied "{prop}") '
            f'catch (append errors "Could not set {prop}")'
        )
        names.append(prop_name)
    return "\n".join(lines), names


@mcp.tool()
def list_tyflow_operator_types() -> str:
    """Return available and unavailable tyFlow operator names for this installation."""
    candidates = _mxs_string_array(list(KNOWN_OPERATORS))
    maxscript = f"""(
{HELPERS}
if tyFlow == undefined then (
    "{{\\"error\\":\\"tyFlow plugin is not available\\"}}"
) else (
    local opNames = {candidates}
    local flow = tyFlow name:"zzz_tyflow_op_probe"
    local eventHandle = flow.tyFlow.addEvent()
    local ev = eventHandle.Event
    ev.setName "Probe"
    local ok = #()
    local fail = #()
    for n in opNames do (
        local op = undefined
        try (op = ev.addOperator n -1) catch ()
        if op == undefined then append fail n else (
            append ok n
            try (op.remove()) catch ()
        )
    )
    delete flow
    "{{\\"available\\":" + (jsonStringArray ok) + ",\\"unavailable\\":" + (jsonStringArray fail) + "}}"
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse operator probe response."}))


@mcp.tool()
def create_tyflow(
    name: str = "",
    position: FloatList | None = None,
    event_name: str = "Emit",
    event_position: IntList | None = None,
    operators: DictList | None = None,
    select_created: bool = True,
) -> str:
    """Create tyFlow with one event and a configurable operator list."""
    from .selection import select_objects

    pos = position or [0.0, 0.0, 0.0]
    ev_pos = event_position or [0, 0]
    if len(pos) != 3:
        raise ValueError("position must be [x, y, z]")
    if len(ev_pos) != 2:
        raise ValueError("event_position must be [x, y]")

    op_defs = operators or [
        {"type": "Birth", "name": "Birth", "position": 0, "properties": {"birthMode": 0, "birthTotal": 100}},
        {
            "type": "Shape",
            "name": "Shape",
            "position": 1,
            "properties": {
                "shape_type_tab": [1],
                "type_3d_ID_tab": [SHAPE_3D_IDS["sphere"]],
                "frequency_tab": [100.0],
                "scaleVal_tab": [100.0],
            },
        },
        {"type": "Display", "name": "Display", "position": 2, "properties": {"displayMode": 2}},
    ]

    op_blocks: list[str] = []
    for idx, op in enumerate(op_defs, start=1):
        op_type = safe_string(str(op.get("type", "Birth")))
        op_name = safe_string(str(op.get("name", op.get("type", f"Operator{idx}"))))
        op_pos = int(op.get("position", idx - 1))
        var = f"op{idx}"
        props = op.get("properties", {})
        if not isinstance(props, dict):
            props = {}
        assign_lines: list[str] = []
        for prop_name, prop_value in props.items():
            prop = safe_string(prop_name)
            expr = _mxs_value(prop_value)
            assign_lines.append(f'try ({var}.{prop} = {expr}) catch (totalErrors += 1)')
        assign_script = "\n".join(assign_lines)
        op_blocks.append(
            f"""
local {var} = ev.addOperator "{op_type}" {op_pos}
try ({var}.Operator.setName "{op_name}") catch ()
{assign_script}
operatorCount += 1
"""
        )

    maxscript = f"""(
{HELPERS}
if tyFlow == undefined then (
    "{{\\"error\\":\\"tyFlow plugin is not available\\"}}"
) else (
    if "{safe_string(name)}" != "" and (getNodeByName "{safe_string(name)}") != undefined then (
        "{{\\"error\\":\\"Object already exists: {safe_string(name)}\\"}}"
    ) else (
        local flow = if "{safe_string(name)}" == "" then tyFlow pos:[{float(pos[0])},{float(pos[1])},{float(pos[2])}] else tyFlow name:"{safe_string(name)}" pos:[{float(pos[0])},{float(pos[1])},{float(pos[2])}]
        local eventHandle = flow.tyFlow.addEvent()
        local ev = eventHandle.Event
        ev.setName "{safe_string(event_name)}"
        ev.setPosition [{int(ev_pos[0])},{int(ev_pos[1])}]
        local operatorCount = 0
        local totalErrors = 0
        {"".join(op_blocks)}
        "{{\\"name\\":\\"" + flow.name + "\\",\\"event\\":\\"" + ev.getName() + "\\",\\"operatorCount\\":" + (operatorCount as string) + ",\\"errorCount\\":" + (totalErrors as string) + "}}"
    )
)
)"""
    payload = _send_json(maxscript, {"error": "Could not parse create_tyflow response."})
    if select_created and isinstance(payload, dict) and "name" in payload and "error" not in payload:
        payload["selectResult"] = select_objects(names=[str(payload["name"])])
    return json.dumps(payload)


@mcp.tool()
def get_tyflow_info(
    name: str,
    include_events: bool = True,
    include_operator_properties: bool = False,
    max_operators_per_event: int = 200,
    include_flow_properties: bool = False,
    include_event_properties: bool = False,
    max_properties_per_operator: int = 200,
    max_properties_per_event: int = 200,
    max_properties_on_flow: int = 200,
) -> str:
    """Inspect a tyFlow object with deep flow/event/operator/property readback."""
    max_ops = max(1, int(max_operators_per_event))
    max_op_props = max(1, int(max_properties_per_operator))
    max_ev_props = max(1, int(max_properties_per_event))
    max_flow_props = max(1, int(max_properties_on_flow))
    maxscript = f"""(
{HELPERS}
fn parseSubAnimNamesByShowProps targetObj =
(
    local names = #()
    local ss = stringstream ""
    try (showProperties targetObj to:ss) catch ()
    seek ss 0
    while not eof ss do (
        local line = trimRight (trimLeft (readline ss))
        if line.count > 1 and (substring line 1 1) == "." then (
            local rawName = trimRight (trimLeft (substring line 2 (line.count - 1)))
            if (findString rawName ":") == undefined and rawName != "" do append names rawName
        )
    )
    names
)

fn clean s =
(
    local t = s as string
    t = substituteString t "|" "<pipe>"
    t = substituteString t "\\n" " "
    t = substituteString t "\\r" ""
    t
)

fn propLinesFor targetObj lineTag maxProps maxChars =
(
    local out = ""
    local pNames = #()
    try (pNames = getPropNames targetObj) catch ()
    local total = pNames.count
    local take = total
    if take > maxProps do take = maxProps
    for i = 1 to take do (
        local p = pNames[i]
        local pName = p as string
        local pVal = ""
        try (pVal = (getProperty targetObj p) as string) catch (pVal = "<unreadable>")
        if pVal.count > maxChars do pVal = (substring pVal 1 maxChars) + "..."
        out += lineTag + "|" + (clean pName) + "|" + (clean pVal) + "\\n"
    )
    if total > take do out += "WARN|" + lineTag + "_TRUNCATED|" + (total as string) + "|" + (take as string) + "\\n"
    out
)

local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    local bo = flow.baseobject
    local particleCount = 0
    try (particleCount = flow.numParticles()) catch ()

    local out = "FLOW|" + (clean flow.name) + "|" + (clean ((classof bo) as string)) + "|" + (particleCount as string) + "\\n"
    if {str(bool(include_flow_properties)).lower()} then (
        local fpLines = propLinesFor bo "FP" {max_flow_props} 300
        out += fpLines
    )
    if {str(bool(include_events)).lower()} then (
        local eventNames = parseSubAnimNamesByShowProps bo
        out += "META|eventSubAnimCount|" + (eventNames.count as string) + "\\n"
        for eventName in eventNames do (
            out += "EV|" + (clean eventName) + "\\n"
            local evSym = undefined
            local ev = undefined
            try (evSym = execute ("#'" + eventName + "'")) catch ()
            if evSym != undefined then (
                try (ev = bo[evSym]) catch ()
            )
            if ev != undefined then (
                if {str(bool(include_event_properties)).lower()} then (
                    local epNames = #()
                    try (epNames = getPropNames ev) catch ()
                    local epTotal = epNames.count
                    local epTake = epTotal
                    if epTake > {max_ev_props} do epTake = {max_ev_props}
                    for epi = 1 to epTake do (
                        local ep = epNames[epi]
                        local epName = ep as string
                        local epVal = ""
                        try (epVal = (getProperty ev ep) as string) catch (epVal = "<unreadable>")
                        if epVal.count > 300 do epVal = (substring epVal 1 300) + "..."
                        out += "EP|" + (clean eventName) + "|" + (clean epName) + "|" + (clean epVal) + "\\n"
                    )
                    if epTotal > epTake do out += "WARN|EP_TRUNCATED|" + (clean eventName) + "|" + (epTotal as string) + "|" + (epTake as string) + "\\n"
                )
                local opNames = parseSubAnimNamesByShowProps ev
                local opCount = opNames.count
                if opCount > {max_ops} then (
                    out += "WARN|OP_TRUNCATED|" + (clean eventName) + "|" + (opNames.count as string) + "|" + ({max_ops} as string) + "\\n"
                    opCount = {max_ops}
                )
                for oi = 1 to opCount do (
                    local opName = opNames[oi]
                    local opSym = undefined
                    local op = undefined
                    try (opSym = execute ("#'" + opName + "'")) catch ()
                    if opSym != undefined then (
                        try (op = ev[opSym]) catch ()
                    )
                    local opClass = "<unknown>"
                    local propCount = 0
                    if op != undefined then (
                        try (opClass = (classof op.Operator) as string) catch (try (opClass = (classof op) as string) catch ())
                        local pNames = #()
                        try (pNames = getPropNames op) catch ()
                        propCount = pNames.count
                        out += "OP|" + (clean eventName) + "|" + (clean opName) + "|" + (clean opClass) + "|" + (propCount as string) + "\\n"
                        if {str(bool(include_operator_properties)).lower()} then (
                            local pTake = pNames.count
                            if pTake > {max_op_props} do pTake = {max_op_props}
                            for pi = 1 to pTake do (
                                local p = pNames[pi]
                                local pName = p as string
                                local pVal = ""
                                try (pVal = (getProperty op p) as string) catch (pVal = "<unreadable>")
                                if pVal.count > 300 do pVal = (substring pVal 1 300) + "..."
                                out += "PR|" + (clean eventName) + "|" + (clean opName) + "|" + (clean pName) + "|" + (clean pVal) + "\\n"
                            )
                            if pNames.count > pTake do out += "WARN|PR_TRUNCATED|" + (clean eventName) + "|" + (clean opName) + "|" + (pNames.count as string) + "|" + (pTake as string) + "\\n"
                        )
                    )
                    if op == undefined then out += "OP|" + (clean eventName) + "|" + (clean opName) + "|<unresolved>|0\\n"
                )
            )
        )
    )
    out
)
)"""
    try:
        response = client.send_command(maxscript)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return json.dumps({"error": raw.split("|", 1)[1]})

    def _decode_token(value: str) -> str:
        return value.replace("<pipe>", "|")

    result: dict[str, Any] = {
        "name": name,
        "class": "",
        "particleCount": 0,
        "flowPropertyCount": 0,
        "flowProperties": [],
        "eventSubAnimCount": 0,
        "eventCount": 0,
        "events": [],
        "warnings": [],
    }
    events: dict[str, dict[str, Any]] = {}

    for line in raw.splitlines():
        parts = line.split("|")
        if not parts:
            continue
        tag = parts[0]
        if tag == "FLOW" and len(parts) >= 4:
            result["name"] = _decode_token(parts[1])
            result["class"] = _decode_token(parts[2])
            try:
                result["particleCount"] = int(parts[3])
            except Exception:
                result["particleCount"] = 0
        elif tag == "META" and len(parts) >= 3:
            if parts[1] == "eventSubAnimCount":
                try:
                    result["eventSubAnimCount"] = int(parts[2])
                except Exception:
                    result["eventSubAnimCount"] = 0
        elif tag == "FP" and len(parts) >= 3:
            result["flowProperties"].append({"name": _decode_token(parts[1]), "value": _decode_token(parts[2])})
        elif tag == "EV" and len(parts) >= 2:
            ev_name = _decode_token(parts[1])
            if ev_name not in events:
                events[ev_name] = {
                    "name": ev_name,
                    "propertyCount": 0,
                    "properties": [],
                    "operatorCount": 0,
                    "operators": [],
                }
        elif tag == "EP" and len(parts) >= 4:
            ev_name = _decode_token(parts[1])
            p_name = _decode_token(parts[2])
            p_val = _decode_token(parts[3])
            if ev_name not in events:
                events[ev_name] = {
                    "name": ev_name,
                    "propertyCount": 0,
                    "properties": [],
                    "operatorCount": 0,
                    "operators": [],
                }
            events[ev_name]["properties"].append({"name": p_name, "value": p_val})
        elif tag == "OP" and len(parts) >= 5:
            ev_name = _decode_token(parts[1])
            op_name = _decode_token(parts[2])
            op_class = _decode_token(parts[3])
            try:
                prop_count = int(parts[4])
            except Exception:
                prop_count = 0
            if ev_name not in events:
                events[ev_name] = {
                    "name": ev_name,
                    "propertyCount": 0,
                    "properties": [],
                    "operatorCount": 0,
                    "operators": [],
                }
            events[ev_name]["operators"].append({
                "name": op_name,
                "class": op_class,
                "propertyCount": prop_count,
                "properties": [],
            })
        elif tag == "PR" and len(parts) >= 5:
            ev_name = _decode_token(parts[1])
            op_name = _decode_token(parts[2])
            prop_name = _decode_token(parts[3])
            prop_value = _decode_token(parts[4])
            ev = events.get(ev_name)
            if not ev:
                continue
            op = next((item for item in ev["operators"] if item["name"] == op_name), None)
            if op is None:
                continue
            op["properties"].append({"name": prop_name, "value": prop_value})
        elif tag == "WARN":
            decoded = [_decode_token(p) for p in parts[1:]]
            if decoded:
                result["warnings"].append(decoded)

    event_list = list(events.values())
    for ev in event_list:
        ev["propertyCount"] = len(ev["properties"])
        ev["operatorCount"] = len(ev["operators"])
    result["flowPropertyCount"] = len(result["flowProperties"])
    result["eventCount"] = len(event_list)
    result["events"] = event_list
    return json.dumps(result)


@mcp.tool()
def add_tyflow_event(name: str, event_name: str, event_position: IntList | None = None) -> str:
    """Add one event to an existing tyFlow object."""
    pos = event_position or [0, 0]
    if len(pos) != 2:
        raise ValueError("event_position must be [x, y]")

    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local evRef = flow.tyFlow.addEvent()
    local ev = evRef.Event
    ev.setName "{safe_string(event_name)}"
    ev.setPosition [{int(pos[0])},{int(pos[1])}]
    "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"event\\":\\"" + (esc ev.getName()) + "\\"}}"
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse add_tyflow_event response."}))


@mcp.tool()
def modify_tyflow_operator(
    name: str,
    event_name: str,
    operator_name: str,
    properties: dict[str, Any],
    raw_values: bool = False,
) -> str:
    """Set operator properties on an existing tyFlow event/operator pair."""
    if not properties:
        return json.dumps({"error": "properties cannot be empty"})

    assignments, names = _assignment_lines(properties, "op", raw_strings=raw_values)
    req = _mxs_string_array(names)
    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local ev = findEventSubAnim flow "{safe_string(event_name)}"
    if ev == undefined then (
        "{{\\"error\\":\\"Event not found: {safe_string(event_name)}\\"}}"
    ) else (
        local op = findOperatorSubAnim ev "{safe_string(operator_name)}"
        if op == undefined then (
            "{{\\"error\\":\\"Operator not found: {safe_string(operator_name)}\\"}}"
        ) else (
            local applied = #()
            local errors = #()
            {assignments}
            "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"event\\":\\"" + (esc "{safe_string(event_name)}") + "\\",\\"operator\\":\\"" + (esc "{safe_string(operator_name)}") + "\\",\\"requested\\":" + (jsonStringArray {req}) + ",\\"applied\\":" + (jsonStringArray applied) + ",\\"errors\\":" + (jsonStringArray errors) + "}}"
        )
    )
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse modify_tyflow_operator response."}))


@mcp.tool()
def set_tyflow_shape(
    name: str,
    event_name: str = "Emit",
    operator_name: str = "Shape",
    shape: str = "sphere",
    scale: float = 100.0,
    frequency: float = 100.0,
    create_if_missing: bool = True,
) -> str:
    """Set Shape operator with validated 3D shape IDs."""
    key = shape.strip().lower()
    if key not in SHAPE_3D_IDS:
        return json.dumps({"error": f"Unknown shape '{shape}'"})

    shape_id = SHAPE_3D_IDS[key]
    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local ev = findEventSubAnim flow "{safe_string(event_name)}"
    if ev == undefined then (
        "{{\\"error\\":\\"Event not found: {safe_string(event_name)}\\"}}"
    ) else (
        local shapeOp = findOperatorSubAnim ev "{safe_string(operator_name)}"
        if shapeOp == undefined and {str(bool(create_if_missing)).lower()} then (
            local evI = undefined
            try (evI = ev.Event) catch ()
            if evI != undefined then (
                shapeOp = evI.addOperator "Shape" -1
                try (shapeOp.Operator.setName "{safe_string(operator_name)}") catch ()
            )
        )
        if shapeOp == undefined then (
            "{{\\"error\\":\\"Shape operator not found\\"}}"
        ) else (
            local applied = #()
            local errors = #()
            try (shapeOp.shape_type_tab = #(1); append applied "shape_type_tab") catch (append errors "shape_type_tab")
            try (shapeOp.type_3d_ID_tab = #({shape_id}); append applied "type_3d_ID_tab") catch (append errors "type_3d_ID_tab")
            try (shapeOp.frequency_tab = #({float(frequency)}); append applied "frequency_tab") catch (append errors "frequency_tab")
            try (shapeOp.scaleVal_tab = #({float(scale)}); append applied "scaleVal_tab") catch (append errors "scaleVal_tab")
            "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"shape\\":\\"{safe_string(key)}\\",\\"shapeId\\":{shape_id},\\"applied\\":" + (jsonStringArray applied) + ",\\"errors\\":" + (jsonStringArray errors) + "}}"
        )
    )
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse set_tyflow_shape response."}))


@mcp.tool()
def connect_tyflow_events(
    name: str,
    from_event: str,
    to_event: str,
    send_out_operator_name: str = "Send Out",
    create_if_missing: bool = True,
) -> str:
    """Connect events with Send Out by applying common destination property candidates."""
    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local src = findEventSubAnim flow "{safe_string(from_event)}"
    local dst = findEventSubAnim flow "{safe_string(to_event)}"
    if src == undefined then (
        "{{\\"error\\":\\"Source event not found: {safe_string(from_event)}\\"}}"
    ) else if dst == undefined then (
        "{{\\"error\\":\\"Destination event not found: {safe_string(to_event)}\\"}}"
    ) else (
        local sendOp = findOperatorSubAnim src "{safe_string(send_out_operator_name)}"
        if sendOp == undefined and {str(bool(create_if_missing)).lower()} then (
            local srcI = undefined
            try (srcI = src.Event) catch ()
            if srcI != undefined then (
                sendOp = srcI.addOperator "Send Out" -1
                try (sendOp.Operator.setName "{safe_string(send_out_operator_name)}") catch ()
            )
        )
        if sendOp == undefined then (
            "{{\\"error\\":\\"Send Out operator not found\\"}}"
        ) else (
            local applied = #()
            local errors = #()
            local props = #("eventName", "targetEvent", "nextEvent", "destinationEvent")
            for pName in props do (
                local pSym = execute ("#" + pName)
                if isProperty sendOp pSym then (
                    try (setProperty sendOp pSym "{safe_string(to_event)}"; append applied pName) catch (append errors ("Could not set " + pName))
                )
            )
            "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"fromEvent\\":\\"{safe_string(from_event)}\\",\\"toEvent\\":\\"{safe_string(to_event)}\\",\\"operator\\":\\"{safe_string(send_out_operator_name)}\\",\\"applied\\":" + (jsonStringArray applied) + ",\\"errors\\":" + (jsonStringArray errors) + "}}"
        )
    )
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse connect_tyflow_events response."}))


@mcp.tool()
def add_tyflow_collision(
    name: str,
    event_name: str,
    collider_names: StrList,
    operator_name: str = "Collision",
    create_if_missing: bool = True,
) -> str:
    """Add/configure Collision operator and wire collider node list."""
    requested = _mxs_string_array(collider_names)
    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
local names = {requested}
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local ev = findEventSubAnim flow "{safe_string(event_name)}"
    if ev == undefined then (
        "{{\\"error\\":\\"Event not found: {safe_string(event_name)}\\"}}"
    ) else (
        local collOp = findOperatorSubAnim ev "{safe_string(operator_name)}"
        if collOp == undefined and {str(bool(create_if_missing)).lower()} then (
            local evI = undefined
            try (evI = ev.Event) catch ()
            if evI != undefined then (
                collOp = evI.addOperator "Collision" -1
                try (collOp.Operator.setName "{safe_string(operator_name)}") catch ()
            )
        )
        if collOp == undefined then (
            "{{\\"error\\":\\"Collision operator not found\\"}}"
        ) else (
            local nodes = #()
            local missing = #()
            for n in names do (
                local node = getNodeByName n
                if node == undefined then append missing n else append nodes node
            )
            local applied = #()
            local errors = #()
            local props = #("colliderList", "objectList", "objects", "nodes")
            for pName in props do (
                local pSym = execute ("#" + pName)
                if isProperty collOp pSym then (
                    try (setProperty collOp pSym nodes; append applied pName) catch (append errors ("Could not set " + pName))
                )
            )
            "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"operator\\":\\"{safe_string(operator_name)}\\",\\"requested\\":" + (jsonStringArray names) + ",\\"missing\\":" + (jsonStringArray missing) + ",\\"applied\\":" + (jsonStringArray applied) + ",\\"errors\\":" + (jsonStringArray errors) + "}}"
        )
    )
)
    )"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse add_tyflow_collision response."}))


@mcp.tool()
def set_tyflow_physx(
    name: str,
    enabled: bool = True,
    gravity: float = -980.0,
    substeps: int = 8,
    pos_iterations: int = 4,
    vel_iterations: int = 1,
) -> str:
    """Set object-level PhysX settings from tyFlow object properties."""
    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local bo = flow.baseobject
    local applied = #()
    local errors = #()
    fn setIf propName propValue = (
        local pSym = execute ("#" + propName)
        if isProperty bo pSym then (
            try (setProperty bo pSym propValue; append applied propName) catch (append errors ("Could not set " + propName))
        )
    )
    setIf "physXGravityEnabled" {str(bool(enabled)).lower()}
    setIf "physXGravityValue" {float(gravity)}
    setIf "physXSubsteps" {int(substeps)}
    setIf "physXPosIterations" {int(pos_iterations)}
    setIf "physXVelIterations" {int(vel_iterations)}
    "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"applied\\":" + (jsonStringArray applied) + ",\\"errors\\":" + (jsonStringArray errors) + "}}"
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse set_tyflow_physx response."}))


@mcp.tool()
def remove_tyflow_element(name: str, event_name: str, operator_name: str = "") -> str:
    """Remove operator from an event, or remove event when operator_name is empty."""
    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local ev = findEventSubAnim flow "{safe_string(event_name)}"
    if ev == undefined then (
        "{{\\"error\\":\\"Event not found: {safe_string(event_name)}\\"}}"
    ) else (
        if "{safe_string(operator_name)}" != "" then (
            local op = findOperatorSubAnim ev "{safe_string(operator_name)}"
            if op == undefined then (
                "{{\\"error\\":\\"Operator not found: {safe_string(operator_name)}\\"}}"
            ) else (
                local ok = false
                try (op.remove(); ok = true) catch ()
                if ok then "{{\\"removed\\":\\"operator\\",\\"event\\":\\"{safe_string(event_name)}\\",\\"operator\\":\\"{safe_string(operator_name)}\\"}}" else "{{\\"error\\":\\"Could not remove operator\\"}}"
            )
        ) else (
            local ok = false
            try (ev.remove(); ok = true) catch ()
            if ok then "{{\\"removed\\":\\"event\\",\\"event\\":\\"{safe_string(event_name)}\\"}}" else "{{\\"error\\":\\"Could not remove event\\"}}"
        )
    )
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse remove_tyflow_element response."}))


@mcp.tool()
def get_tyflow_particle_count(name: str, frame: int | None = None, update: bool = True) -> str:
    """Return tyFlow particle count at current frame or supplied frame."""
    frame_expr = "currentTime" if frame is None else f"{int(frame)}f"
    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    if {str(bool(update)).lower()} then (
        sliderTime = {frame_expr}
        try (flow.updateParticles {frame_expr}) catch ()
    )
    local n = 0
    try (n = flow.numParticles()) catch ()
    "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"particleCount\\":" + (n as string) + "}}"
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse get_tyflow_particle_count response."}))


@mcp.tool()
def get_tyflow_particles(
    name: str,
    frame: int | None = None,
    max_particles: int = 1000,
    include_position: bool = True,
    include_velocity: bool = True,
    include_age: bool = True,
) -> str:
    """Return particle data rows from tyFlow read-only APIs."""
    if max_particles <= 0:
        raise ValueError("max_particles must be > 0")
    frame_expr = "currentTime" if frame is None else f"{int(frame)}f"
    maxscript = f"""(
{HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    sliderTime = {frame_expr}
    try (flow.updateParticles {frame_expr}) catch ()
    local total = 0
    try (total = flow.numParticles()) catch ()
    local takeCount = total
    if takeCount > {int(max_particles)} do takeCount = {int(max_particles)}
    local pos = #()
    local vel = #()
    local age = #()
    if {str(bool(include_position)).lower()} then (try (pos = flow.getAllParticlePositions()) catch ())
    if {str(bool(include_velocity)).lower()} then (try (vel = flow.getAllParticleVelocities()) catch ())
    if {str(bool(include_age)).lower()} then (try (age = flow.getAllParticleAges()) catch ())

    local rows = #()
    for i = 1 to takeCount do (
        local row = "{{\\"id\\":" + (i as string)
        if {str(bool(include_position)).lower()} and pos.count >= i then (
            local p = pos[i]
            row += ",\\"position\\":[" + ((p.x) as string) + "," + ((p.y) as string) + "," + ((p.z) as string) + "]"
        )
        if {str(bool(include_velocity)).lower()} and vel.count >= i then (
            local v = vel[i]
            row += ",\\"velocity\\":[" + ((v.x) as string) + "," + ((v.y) as string) + "," + ((v.z) as string) + "]"
        )
        if {str(bool(include_age)).lower()} and age.count >= i then row += ",\\"age\\":" + ((age[i]) as string)
        row += "}}"
        append rows row
    )
    local payload = "["
    for i = 1 to rows.count do (
        if i > 1 do payload += ","
        payload += rows[i]
    )
    payload += "]"
    "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"total\\":" + (total as string) + ",\\"returned\\":" + (takeCount as string) + ",\\"particles\\":" + payload + "}}"
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse get_tyflow_particles response."}))


@mcp.tool()
def reset_tyflow_simulation(name: str = "") -> str:
    """Reset simulation for one tyFlow object or for all tyFlow objects."""
    maxscript = f"""(
{HELPERS}
local targets = #()
if "{safe_string(name)}" != "" then (
    local node = getNodeByName "{safe_string(name)}"
    if node != undefined then append targets node
) else (
    for o in objects where ((classof o.baseobject as string) == "tyFlow" or (classof o as string) == "tyFlow") do append targets o
)
if targets.count == 0 then (
    "{{\\"error\\":\\"No tyFlow objects found\\"}}"
) else (
    local resetNames = #()
    for n in targets do (
        try (n.reset_simulation(); append resetNames n.name) catch ()
    )
    "{{\\"count\\":" + (resetNames.count as string) + ",\\"names\\":" + (jsonStringArray resetNames) + "}}"
)
)"""
    return json.dumps(_send_json(maxscript, {"error": "Could not parse reset_tyflow_simulation response."}))


@mcp.tool()
def create_tyflow_preset(
    preset: str,
    name: str = "",
    position: FloatList | None = None,
    amount: int = 100,
    speed: float = 120.0,
) -> str:
    """Create common tyFlow presets: rain, snow, fountain, burst, debris."""
    key = preset.strip().lower()
    if key not in {"rain", "snow", "fountain", "burst", "debris"}:
        return json.dumps({"error": "Unsupported preset. Use rain|snow|fountain|burst|debris"})

    if key == "snow":
        shape, force_z, speed_v = "quad", -50.0, max(5.0, speed * 0.2)
    elif key == "rain":
        shape, force_z, speed_v = "sphere", -300.0, max(50.0, speed * 1.5)
    elif key == "fountain":
        shape, force_z, speed_v = "sphere", -150.0, max(80.0, speed * 1.2)
    elif key == "burst":
        shape, force_z, speed_v = "sphere", -30.0, max(100.0, speed * 2.0)
    else:
        shape, force_z, speed_v = "box", -980.0, max(30.0, speed)

    flow_name = name or f"ty_{key}"
    return create_tyflow(
        name=flow_name,
        position=position or [0.0, 0.0, 0.0],
        event_name=key.capitalize(),
        event_position=[0, 0],
        operators=[
            {"type": "Birth", "name": "Birth", "position": 0, "properties": {"birthMode": 0, "birthTotal": int(amount)}},
            {"type": "Speed", "name": "Speed", "position": 1, "properties": {"magnitude": float(speed_v), "directionMode": 3}},
            {"type": "Force", "name": "Force", "position": 2, "properties": {"gravityStrength": float(force_z)}},
            {
                "type": "Shape",
                "name": "Shape",
                "position": 3,
                "properties": {
                    "shape_type_tab": [1],
                    "type_3d_ID_tab": [SHAPE_3D_IDS[shape]],
                    "frequency_tab": [100.0],
                    "scaleVal_tab": [100.0],
                },
            },
            {"type": "Display", "name": "Display", "position": 4, "properties": {"displayMode": 2}},
        ],
        select_created=True,
    )
