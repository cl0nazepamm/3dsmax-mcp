from __future__ import annotations

import json
from typing import Any

from ..helpers.maxscript import (
    NODE_LOOKUP_FNS,
    ambiguous_names_warning,
    name_in_use,
    name_literal,
    node_by_name,
    safe_string,
)

from ..server import client, mcp
from ..coerce import StrList, FloatList, IntList, DictList
from ..max_client import CALL_STATE_ERRORS
from ._tyflow_core import EDITOR_HELPERS, MAXSCRIPT_ERROR, ledger_update, merge_ledger, operator_call


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


HELPERS = EDITOR_HELPERS + """
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

fn mcpIsTyFlow node =
(
    (classof node.baseobject as string) == "tyFlow" or (classof node as string) == "tyFlow"
)

fn mcpTyChildByName parent childName =
(
    -- tyFlow dispatch exposes spaced names underscored (Send Out -> .Send_Out):
    -- try the name as given, then the underscored form.
    if parent == undefined then return undefined
    local sub = undefined
    local sym = undefined
    try (sym = execute ("#'" + childName + "'")) catch ()
    if sym != undefined do (try (sub = parent[sym]) catch ())
    if sub == undefined do (
        local alt = substituteString childName " " "_"
        if alt != childName do (
            local sym2 = undefined
            try (sym2 = execute ("#'" + alt + "'")) catch ()
            if sym2 != undefined do (try (sub = parent[sym2]) catch ())
        )
    )
    sub
)

fn findEventSubAnim flowNode eventName =
(
    if flowNode == undefined then return undefined
    local bo = flowNode.baseobject
    if bo == undefined then return undefined
    mcpTyChildByName bo eventName
)

fn findOperatorSubAnim eventSub operatorName =
(
    mcpTyChildByName eventSub operatorName
)

fn mcpTyFlowOfEvent realEv =
(
    -- The tyFlow node whose event sub-anim holds realEv.
    local owner = undefined
    for n in objects where owner == undefined and (classof n.baseobject as string) == "tyFlow" do (
        local bo = n.baseobject
        for i = 1 to bo.numsubs while owner == undefined do (
            local o = undefined
            try (o = bo[i].object) catch ()
            if o == realEv do owner = n
        )
    )
    owner
)

fn mcpTyAddWithEditor realEv opType position =
(
    -- tyFlow 2.101 (live, Max 2023 and 2027) adds operators only to the flow whose
    -- editor is open; otherwise addOperator answers undefined for every type. Open
    -- the editor on the event's flow for the add, and close it only if it was closed.
    local flow = mcpTyFlowOfEvent realEv
    if flow == undefined then return undefined
    local op = undefined
    local wasOpen = mcpTyEditorOpen flow
    try (op = realEv.Event.addOperator opType position) catch ()
    mcpTyEditorRestore flow wasOpen
    op
)

fn mcpTyAddOperator eventTarget opType position =
(
    -- An event holds operators of one family, set by its first operator. tyFlow
    -- answers addOperator for another family with an object that never joins
    -- the event, so the operator count is the proof.
    local realEv = eventTarget
    try (realEv = eventTarget.object) catch ()
    local before = realEv.numsubs
    local op = undefined
    try (op = realEv.Event.addOperator opType position) catch ()
    if op == undefined do op = mcpTyAddWithEditor realEv opType position
    if op == undefined do throw ("tyFlow has no operator type " + opType + " (list_tyflow_operators shows the installed types)")
    if realEv.numsubs <= before do throw ("tyFlow did not add " + opType + " to " + (realEv.Event.getName()) + ": an event holds operators of one family (particle, terrain, VDB or Inferno), set by its first operator. Add it to an event of its own family.")
    op
)

fn mcpTyThrown =
(
    -- The message of the exception being handled, without MAXScript's prefix.
    local text = (getCurrentException()) as string
    local marker = findString text "Runtime error: "
    if marker != undefined do text = substring text (marker + 15) -1
    trimRight text
)
"""


def _load_json(raw: str, fallback: Any) -> Any:
    if isinstance(raw, str) and raw.startswith(MAXSCRIPT_ERROR):
        return {"error": "MAXScript error: " + raw[len(MAXSCRIPT_ERROR):].strip()}
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _send_json(maxscript: str, fallback: Any) -> Any:
    # Transport and call-state exceptions propagate: the envelope keeps their
    # code (BLOCKED_BY_DIALOG, MAX_BUSY, TARGET_UNAVAILABLE) and hint.
    response = client.send_command(maxscript)
    return _load_json(response.get("result", ""), fallback)


def _batch_outcome(payload: Any, issues: list[str], *, applied: bool, nothing: str) -> Any:
    """Mark a result whose request applied only in part, or not at all."""
    if not isinstance(payload, dict) or "error" in payload or not issues:
        return payload
    if not applied:
        payload["error"] = f"{nothing}: {'; '.join(issues)}"
        payload["details"] = {key: value for key, value in payload.items() if key != "error"}
        return payload
    payload["status"] = "partial"
    payload["warnings"] = list(payload.get("warnings") or []) + issues
    return payload


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


TYFLOW_PRESETS: tuple[str, ...] = ("rain", "snow", "fountain", "burst", "debris")


def _preset_operators(key: str, amount: int, speed: float) -> list[dict[str, Any]]:
    """Operator list for one of TYFLOW_PRESETS."""
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

    return [
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
    ]


@mcp.tool()
def create_tyflow(
    name: str = "",
    position: FloatList | None = None,
    event_name: str = "",
    event_position: IntList | None = None,
    operators: DictList | None = None,
    select_created: bool = True,
    preset: str = "",
    amount: int = 100,
    speed: float = 120.0,
) -> str:
    """Create tyFlow with one event and a configurable operator list or a preset.

    Without operators or preset, the event gets Birth, Shape (sphere) and Display.
    preset: rain | snow | fountain | burst | debris adds Birth (amount particles),
    Speed (scaled from speed), Force, Shape and Display; the flow defaults to
    ty_<preset> and the event to the capitalized preset name. Pass operators or
    preset, not both. event_name defaults to Emit otherwise.
    """
    from .selection import select_objects

    key = preset.strip().lower()
    if key and key not in TYFLOW_PRESETS:
        return json.dumps({"error": "Unsupported preset. Use rain|snow|fountain|burst|debris"})
    if key and operators:
        return json.dumps({"error": "Pass either operators or preset, not both"})
    if key:
        name = name or f"ty_{key}"
        event_name = event_name or key.capitalize()
        operators = _preset_operators(key, amount, speed)
    event_name = event_name or "Emit"

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
        # A new operator is named after its type. Shape and a few others expose
        # no Operator interface to MAXScript; the bridge names those afterwards.
        if op_name == op_type:
            naming = ""
        else:
            naming = (
                f'iface = undefined\n    try (iface = {var}.Operator) catch ()\n    '
                f'if iface == undefined then append bridgeNames #((formattedPrint ((getHandleByAnim {var}) as integer64) format:"d"), "{op_name}", "{op_type}") '
                f'else (try (iface.setName "{op_name}") catch (append opErrors "Could not name {op_type} operator {op_name}"))'
            )
        props = op.get("properties", {})
        if not isinstance(props, dict):
            props = {}
        assign_lines: list[str] = []
        for prop_name, prop_value in props.items():
            prop = safe_string(prop_name)
            expr = _mxs_value(prop_value)
            assign_lines.append(
                f'try ({var}.{prop} = {expr}) catch (append opErrors "Could not set {op_name}.{prop}")'
            )
        assign_script = "\n    ".join(assign_lines)
        op_blocks.append(
            f"""
local {var} = undefined
addError = ""
try ({var} = mcpTyAddOperator eventHandle "{op_type}" {op_pos}) catch (addError = mcpTyThrown())
if {var} == undefined then (
    append failedOps "{op_name}"
    append opErrors ("Could not add {op_type} operator {op_name}: " + addError)
) else (
    {naming}
    {assign_script}
    operatorCount += 1
)
"""
        )

    maxscript = f"""(
{HELPERS}
if tyFlow == undefined then (
    "{{\\"error\\":\\"tyFlow plugin is not available\\"}}"
) else (
    if "{safe_string(name)}" != "" and {name_in_use(name_literal(name))} then (
        "{{\\"error\\":\\"Object already exists: {safe_string(name)}\\"}}"
    ) else (
        local flow = if "{safe_string(name)}" == "" then tyFlow pos:[{float(pos[0])},{float(pos[1])},{float(pos[2])}] else tyFlow name:"{safe_string(name)}" pos:[{float(pos[0])},{float(pos[1])},{float(pos[2])}]
        local eventHandle = flow.tyFlow.addEvent()
        local ev = eventHandle.Event
        ev.setName "{safe_string(event_name)}"
        ev.setPosition [{int(ev_pos[0])},{int(ev_pos[1])}]
        local operatorCount = 0
        local opErrors = #()
        local failedOps = #()
        local bridgeNames = #()
        local addError = ""
        local iface = undefined
        {"".join(op_blocks)}
        local renames = "["
        for i = 1 to bridgeNames.count do (
            if i > 1 do renames += ","
            renames += jsonStringArray bridgeNames[i]
        )
        renames += "]"
        "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"event\\":\\"" + (esc (ev.getName())) + "\\",\\"operatorCount\\":" + (operatorCount as string) + ",\\"errorCount\\":" + (opErrors.count as string) + ",\\"errors\\":" + (jsonStringArray opErrors) + ",\\"failedOperators\\":" + (jsonStringArray failedOps) + ",\\"bridgeNames\\":" + renames + "}}"
    )
)
)"""
    payload = _send_json(maxscript, {"error": "Could not parse create_tyflow response."})
    if isinstance(payload, dict) and "name" in payload and "error" not in payload:
        errors = [str(e) for e in payload.get("errors") or []]
        for handle, op_name, op_type in payload.pop("bridgeNames", None) or []:
            try:
                operator_call(str(payload["name"]), int(handle), "setName", value=op_name)
            except CALL_STATE_ERRORS:
                raise
            except Exception as exc:
                errors.append(f"Could not name {op_type} operator {op_name}: {exc}")
        payload["errors"] = errors
        payload["errorCount"] = len(errors)
        # The flow exists even when operators or values were refused, so this
        # is a partial result, never an error.
        _batch_outcome(payload, errors, applied=True, nothing="")
        failed_ops = {str(n) for n in payload.get("failedOperators") or []}
        record_ops: dict[str, str] = {}
        for idx, op in enumerate(op_defs, start=1):
            op_type = str(op.get("type", "Birth"))
            op_name = str(op.get("name", op.get("type", f"Operator{idx}")))
            if op_name not in failed_ops:
                record_ops[f"{event_name}/{op_name}"] = op_type
        merge_ledger(payload, ledger_update(str(payload["name"]), record_ops=record_ops))
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

local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else if not (mcpIsTyFlow flow) then (
    "__ERROR__|Not a tyFlow object: {safe_string(name)}"
) else (
    local bo = flow.baseobject
    local particleCount = "unreadable"
    local particleError = ""
    try (particleCount = flow.numParticles()) catch (particleError = getCurrentException())

    local out = "FLOW|" + (clean flow.name) + "|" + (clean ((classof bo) as string)) + "|" + (particleCount as string) + "\\n"
    if particleError != "" do out += "WARN|PARTICLES_UNREADABLE|" + (clean particleError) + "\\n"
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
    response = client.send_command(maxscript)
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return json.dumps({"error": raw.split("|", 1)[1]})
    if raw.startswith(MAXSCRIPT_ERROR):
        return raw

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
            except ValueError:
                # numParticles failed; a WARN line carries the reason.
                result["particleCount"] = None
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
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local evRef = flow.tyFlow.addEvent()
    local ev = evRef.Event
    ev.setName "{safe_string(event_name)}"
    ev.setPosition [{int(pos[0])},{int(pos[1])}]
    "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"event\\":\\"" + (esc (ev.getName())) +"\\"}}"
)
)"""
    payload = _send_json(maxscript, {"error": "Could not parse add_tyflow_event response."})
    if isinstance(payload, dict) and "error" not in payload:
        merge_ledger(payload, ledger_update(name))
    return json.dumps(payload)


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
local flow = {node_by_name(name, missing_ok=True)}
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
    payload = _send_json(maxscript, {"error": "Could not parse modify_tyflow_operator response."})
    if isinstance(payload, dict) and "error" not in payload:
        _batch_outcome(payload, [str(e) for e in payload.get("errors") or []],
                       applied=bool(payload.get("applied")),
                       nothing=f"No property was set on {operator_name}")
    if isinstance(payload, dict) and "error" not in payload:
        merge_ledger(payload, ledger_update(name))
    return json.dumps(payload)


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
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local ev = findEventSubAnim flow "{safe_string(event_name)}"
    if ev == undefined then (
        "{{\\"error\\":\\"Event not found: {safe_string(event_name)}\\"}}"
    ) else (
        local shapeOp = findOperatorSubAnim ev "{safe_string(operator_name)}"
        local created = false
        local renameHandle = ""
        local addError = ""
        if shapeOp == undefined and {str(bool(create_if_missing)).lower()} then (
            local evI = undefined
            try (evI = ev.Event) catch ()
            if evI != undefined then (
                try (shapeOp = mcpTyAddOperator ev "Shape" -1) catch (addError = mcpTyThrown())
                if shapeOp != undefined do created = true
                -- Shape has no Operator interface in MAXScript; the bridge names it.
                if created and "{safe_string(operator_name)}" != "Shape" do (
                    local iface = undefined
                    try (iface = shapeOp.Operator) catch ()
                    if iface == undefined then renameHandle = formattedPrint ((getHandleByAnim shapeOp) as integer64) format:"d"
                    else try (iface.setName "{safe_string(operator_name)}") catch ()
                )
            )
        )
        if shapeOp == undefined then (
            if addError != "" then "{{\\"error\\":\\"" + (esc addError) + "\\"}}" else "{{\\"error\\":\\"Shape operator not found\\"}}"
        ) else (
            local applied = #()
            local errors = #()
            try (shapeOp.shape_type_tab = #(1); append applied "shape_type_tab") catch (append errors "shape_type_tab")
            try (shapeOp.type_3d_ID_tab = #({shape_id}); append applied "type_3d_ID_tab") catch (append errors "type_3d_ID_tab")
            try (shapeOp.frequency_tab = #({float(frequency)}); append applied "frequency_tab") catch (append errors "frequency_tab")
            try (shapeOp.scaleVal_tab = #({float(scale)}); append applied "scaleVal_tab") catch (append errors "scaleVal_tab")
            "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"shape\\":\\"{safe_string(key)}\\",\\"shapeId\\":{shape_id},\\"created\\":" + (created as string) + ",\\"applied\\":" + (jsonStringArray applied) + ",\\"errors\\":" + (jsonStringArray errors) + ",\\"renameHandle\\":\\"" + renameHandle + "\\"}}"
        )
    )
)
)"""
    payload = _send_json(maxscript, {"error": "Could not parse set_tyflow_shape response."})
    rename_handle = payload.pop("renameHandle", "") if isinstance(payload, dict) else ""
    if rename_handle:
        try:
            operator_call(name, int(rename_handle), "setName", value=operator_name)
        except CALL_STATE_ERRORS:
            raise
        except Exception as exc:
            payload.setdefault("errors", []).append(f"name {operator_name} ({exc})")
    if isinstance(payload, dict) and "error" not in payload:
        created = bool(payload.get("created"))
        _batch_outcome(payload, [f"Could not set {e}" for e in payload.get("errors") or []],
                       applied=bool(payload.get("applied")),
                       nothing=(f"Created Shape operator {operator_name}, but no shape value was set"
                                if created else f"No shape value was set on {operator_name}"))
        # A created operator changed the flow even when its values were refused.
        if created or "error" not in payload:
            merge_ledger(
                payload, ledger_update(name, record_ops={f"{event_name}/{operator_name}": "Shape"})
            )
    return json.dumps(payload)


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
{NODE_LOOKUP_FNS}
local flow = {node_by_name(name, missing_ok=True)}
local names = {requested}
local nodes = #()
local missing = #()
local ambiguous = #()
-- Collider names that match no object or several objects are skipped.
for n in names do (
    local node = mcpBatchNode n missing ambiguous
    if node != undefined do append nodes node
)
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else if names.count > 0 and nodes.count == 0 then (
    -- Writing an empty list would clear the operator's existing colliders.
    if ambiguous.count > 0 do mcpThrowUnresolved missing ambiguous "nothing was changed"
    "{{\\"error\\":\\"No collider name matched an object; nothing was changed\\",\\"details\\":{{\\"missing\\":" + (jsonStringArray missing) + "}}}}"
) else (
    local ev = findEventSubAnim flow "{safe_string(event_name)}"
    if ev == undefined then (
        "{{\\"error\\":\\"Event not found: {safe_string(event_name)}\\"}}"
    ) else (
        local collOp = findOperatorSubAnim ev "{safe_string(operator_name)}"
        local created = false
        local addError = ""
        if collOp == undefined and {str(bool(create_if_missing)).lower()} then (
            local evI = undefined
            try (evI = ev.Event) catch ()
            if evI != undefined then (
                try (collOp = mcpTyAddOperator ev "Collision" -1) catch (addError = mcpTyThrown())
                if collOp != undefined do created = true
                try (collOp.Operator.setName "{safe_string(operator_name)}") catch ()
            )
        )
        if collOp == undefined then (
            if addError != "" then "{{\\"error\\":\\"" + (esc addError) + "\\"}}" else "{{\\"error\\":\\"Collision operator not found\\"}}"
        ) else (
            local applied = #()
            local errors = #()
            local props = #("colliderList", "objectList", "objects", "nodes")
            for pName in props do (
                local pSym = execute ("#" + pName)
                if isProperty collOp pSym then (
                    try (setProperty collOp pSym nodes; append applied pName) catch (append errors ("Could not set " + pName))
                )
            )
            "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"operator\\":\\"{safe_string(operator_name)}\\",\\"created\\":" + (created as string) + ",\\"requested\\":" + (jsonStringArray names) + ",\\"missing\\":" + (jsonStringArray missing) + ",\\"ambiguous\\":" + (mcpAmbiguousJson ambiguous) + ",\\"applied\\":" + (jsonStringArray applied) + ",\\"errors\\":" + (jsonStringArray errors) + "}}"
        )
    )
)
    )"""
    payload = _send_json(maxscript, {"error": "Could not parse add_tyflow_collision response."})
    if isinstance(payload, dict) and "error" not in payload:
        created = bool(payload.get("created"))
        missing = [str(n) for n in payload.get("missing") or []]
        ambiguous = payload.get("ambiguous") or []
        errors = [str(e) for e in payload.get("errors") or []]
        issues = list(errors)
        if not payload.get("applied") and not errors:
            issues.append("the Collision operator has none of colliderList, objectList, objects or nodes")
        if missing:
            issues.append(f"{len(missing)} collider name(s) matched no object and were skipped: {', '.join(missing)}")
        if ambiguous:
            issues.append(ambiguous_names_warning(ambiguous))
        else:
            payload.pop("ambiguous", None)
        _batch_outcome(payload, issues, applied=bool(payload.get("applied")),
                       nothing=(f"Created Collision operator {operator_name}, but no collider was set"
                                if created else f"No collider was set on {operator_name}"))
        # A created operator changed the flow even when no collider was set.
        if created or "error" not in payload:
            merge_ledger(
                payload, ledger_update(name, record_ops={f"{event_name}/{operator_name}": "Collision"})
            )
    return json.dumps(payload)


@mcp.tool()
def set_tyflow_physx(
    name: str,
    enabled: bool | None = None,
    gravity: float | None = None,
    substeps: int | None = None,
    pos_iterations: int | None = None,
    vel_iterations: int | None = None,
) -> str:
    """Set object-level PhysX settings on a tyFlow; only the settings passed change.

    enabled: gravity on/off; gravity: gravity value (scene units/s^2, e.g. -980);
    substeps; pos_iterations / vel_iterations: solver iterations.
    """
    requested = {
        "physXGravityEnabled": None if enabled is None else str(bool(enabled)).lower(),
        "physXGravityValue": None if gravity is None else repr(float(gravity)),
        "physXSubsteps": None if substeps is None else str(int(substeps)),
        "physXPosIterations": None if pos_iterations is None else str(int(pos_iterations)),
        "physXVelIterations": None if vel_iterations is None else str(int(vel_iterations)),
    }
    # Parenthesized: `setIf "x" -500.0` would parse as a subtraction.
    setters = "\n    ".join(f'setIf "{prop}" ({value})' for prop, value in requested.items() if value is not None)
    if not setters:
        return json.dumps({"error": "Pass at least one of enabled, gravity, substeps, pos_iterations or "
                                    "vel_iterations; nothing was changed"})
    maxscript = f"""(
{HELPERS}
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else (
    local bo = flow.baseobject
    local applied = #()
    local errors = #()
    local unsupported = #()
    fn setIf propName propValue = (
        local pSym = execute ("#" + propName)
        if isProperty bo pSym then (
            try (setProperty bo pSym propValue; append applied propName) catch (append errors ("Could not set " + propName))
        ) else append unsupported propName
    )
    {setters}
    "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"applied\\":" + (jsonStringArray applied) + ",\\"errors\\":" + (jsonStringArray errors) + ",\\"unsupported\\":" + (jsonStringArray unsupported) + "}}"
)
)"""
    payload = _send_json(maxscript, {"error": "Could not parse set_tyflow_physx response."})
    if isinstance(payload, dict) and "error" not in payload:
        issues = [str(e) for e in payload.get("errors") or []]
        unsupported = [str(p) for p in payload.get("unsupported") or []]
        if unsupported:
            issues.append(f"{name} has no {', '.join(unsupported)} property, so they were skipped")
        _batch_outcome(payload, issues, applied=bool(payload.get("applied")),
                       nothing=f"No PhysX setting was changed on {name}")
    return json.dumps(payload)


@mcp.tool()
def remove_tyflow_element(name: str, event_name: str, operator_name: str = "") -> str:
    """Remove operator from an event, or remove event when operator_name is empty."""
    maxscript = f"""(
{HELPERS}
local flow = {node_by_name(name, missing_ok=True)}
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
                local realOp = undefined
                try (realOp = op.object) catch ()
                if realOp == undefined do realOp = op
                local iface = undefined
                try (iface = realOp.Operator) catch ()
                if iface == undefined then (
                    -- No Operator interface in MAXScript (Shape and a few others): the bridge removes it.
                    "{{\\"native\\":\\"remove\\",\\"handle\\":" + (formattedPrint ((getHandleByAnim realOp) as integer64) format:"d") + "}}"
                ) else (
                    try (iface.remove(); ok = true) catch ()
                    if ok then "{{\\"removed\\":\\"operator\\",\\"event\\":\\"{safe_string(event_name)}\\",\\"operator\\":\\"{safe_string(operator_name)}\\"}}" else "{{\\"error\\":\\"Could not remove operator\\"}}"
                )
            )
        ) else (
            local ok = false
            try (ev.remove(); ok = true) catch ()
            if ok then "{{\\"removed\\":\\"event\\",\\"event\\":\\"{safe_string(event_name)}\\"}}" else "{{\\"error\\":\\"Could not remove event\\"}}"
        )
    )
)
)"""
    payload = _send_json(maxscript, {"error": "Could not parse remove_tyflow_element response."})
    if isinstance(payload, dict) and payload.get("native") == "remove":
        try:
            operator_call(name, int(payload["handle"]), "remove")
        except CALL_STATE_ERRORS:
            raise
        except Exception as exc:
            payload = {"error": f"Could not remove operator: {exc}"}
        else:
            payload = {"removed": "operator", "event": event_name, "operator": operator_name}
    if isinstance(payload, dict) and "error" not in payload:
        if payload.get("removed") == "event":
            merge_ledger(payload, ledger_update(name, remove_event=event_name))
        elif payload.get("removed") == "operator":
            merge_ledger(payload, ledger_update(name, remove_operator=(event_name, operator_name)))
    return json.dumps(payload)


@mcp.tool()
def get_tyflow_particle_count(name: str, frame: int | None = None, update: bool = True) -> str:
    """Return tyFlow particle count at current frame or supplied frame."""
    frame_expr = "currentTime" if frame is None else f"{int(frame)}f"
    maxscript = f"""(
{HELPERS}
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else if not (mcpIsTyFlow flow) then (
    "{{\\"error\\":\\"Not a tyFlow object: {safe_string(name)}\\"}}"
) else (
    local updateError = ""
    if {str(bool(update)).lower()} then (
        sliderTime = {frame_expr}
        try (flow.updateParticles {frame_expr}) catch (updateError = getCurrentException())
    )
    local n = undefined
    local readError = ""
    try (n = flow.numParticles()) catch (readError = getCurrentException())
    if n == undefined then (
        "{{\\"error\\":\\"Could not read particles from " + (esc flow.name) + ": " + (esc readError) + "\\"}}"
    ) else (
        local warn = if updateError == "" then "" else ",\\"warnings\\":[\\"Particles were not updated to the frame; the count may be stale: " + (esc updateError) + "\\"]"
        "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"particleCount\\":" + (n as string) + warn + "}}"
    )
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
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined then (
    "{{\\"error\\":\\"Object not found: {safe_string(name)}\\"}}"
) else if not (mcpIsTyFlow flow) then (
    "{{\\"error\\":\\"Not a tyFlow object: {safe_string(name)}\\"}}"
) else (
    local warnings = #()
    sliderTime = {frame_expr}
    try (flow.updateParticles {frame_expr}) catch (append warnings ("Particles were not updated to the frame; the data may be stale: " + getCurrentException()))
    local total = undefined
    local readError = ""
    try (total = flow.numParticles()) catch (readError = getCurrentException())
    if total == undefined then (
        "{{\\"error\\":\\"Could not read particles from " + (esc flow.name) + ": " + (esc readError) + "\\"}}"
    ) else (
    local takeCount = total
    if takeCount > {int(max_particles)} do takeCount = {int(max_particles)}
    local pos = #()
    local vel = #()
    local age = #()
    if {str(bool(include_position)).lower()} then (try (pos = flow.getAllParticlePositions()) catch (append warnings ("Positions could not be read: " + getCurrentException())))
    if {str(bool(include_velocity)).lower()} then (try (vel = flow.getAllParticleVelocities()) catch (append warnings ("Velocities could not be read: " + getCurrentException())))
    if {str(bool(include_age)).lower()} then (try (age = flow.getAllParticleAges()) catch (append warnings ("Ages could not be read: " + getCurrentException())))

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
    local warn = if warnings.count == 0 then "" else ",\\"warnings\\":" + (jsonStringArray warnings)
    "{{\\"name\\":\\"" + (esc flow.name) + "\\",\\"total\\":" + (total as string) + ",\\"returned\\":" + (takeCount as string) + ",\\"particles\\":" + payload + warn + "}}"
    )
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
    local node = {node_by_name(name, missing_ok=True)}
    if node != undefined then append targets node
) else (
    for o in objects where ((classof o.baseobject as string) == "tyFlow" or (classof o as string) == "tyFlow") do append targets o
)
if targets.count == 0 then (
    "{{\\"error\\":\\"No tyFlow objects found\\"}}"
) else (
    local resetNames = #()
    local failures = #()
    for n in targets do (
        local done = false
        local failure = ""
        try (n.reset_simulation(); done = true) catch (failure = getCurrentException())
        -- Fall back to the tyFlow interface form that the patch tools use.
        if not done do (try (n.tyFlow.reset_simulation(); done = true) catch ())
        if done then append resetNames n.name else append failures (n.name + ": " + (failure as string))
    )
    "{{\\"count\\":" + (resetNames.count as string) + ",\\"names\\":" + (jsonStringArray resetNames) + ",\\"failed\\":" + (jsonStringArray failures) + "}}"
)
)"""
    payload = _send_json(maxscript, {"error": "Could not parse reset_tyflow_simulation response."})
    if isinstance(payload, dict) and "error" not in payload:
        failures = [str(f) for f in payload.get("failed") or []]
        _batch_outcome(payload, [f"Could not reset {f}" for f in failures],
                       applied=bool(payload.get("count")),
                       nothing="No tyFlow simulation was reset")
    return json.dumps(payload)
