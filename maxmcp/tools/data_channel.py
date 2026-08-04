"""Data Channel modifier tools — procedural per-vertex/face data processing.

The Data Channel modifier is 3ds Max's node-based data processing system
(similar to Houdini VOPs) that lives in the modifier stack. It chains
operators that read mesh data, process it, and output to channels like
position, selection, vertex color, UVs, normals, etc.

One object should have at most one Data Channel modifier in normal use.
Operators are appended to that modifier's internal substack — not by adding
another Data Channel modifier on the object stack.
"""

from __future__ import annotations

import json as _json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from ..server import mcp, client
from ..coerce import DictList, IntList
from ..helpers.maxscript import safe_string


# Friendly aliases resolve to the live catalog display name. AddOperator class
# IDs are deliberately discovered in Max instead of being baked into Python;
# Autodesk has added operators over time and the live modifier is authoritative.
_OPERATOR_NAMES = {
    "vertex_input": "Vertex Input",
    "face_input": "Face Input",
    "edge_input": "Edge Input",
    "xyz_space": "XYZ Space",
    "component_space": "Component Space",
    "curvature": "Curvature",
    "velocity": "Velocity",
    "node_influence": "Node Influence",
    "tension_deform": "Tension Deform",
    "distort": "Distort",
    "maxscript": "Maxscript",
    "maxscript_process": "Maxscript Process",
    "expression_float": "Expression Float",
    "expression_point3": "Expression Point3",
    "vector": "Vector",
    "scale": "Scale",
    "clamp": "Clamp",
    "invert": "Invert",
    "normalize": "Normalize",
    "curve": "Curve",
    "smooth": "Smooth",
    "decay": "Decay",
    "point3_to_float": "Point3 To Float",
    "convert_subobject": "Convert To SubObject Type",
    "geo_quantize": "GeoQuantize",
    "color_space": "Color Space Conversion",
    "vertex_output": "Vertex Output",
    "face_output": "Face Output",
    "edge_output": "Edge Output",
    "transform_elements": "Transform Elements",
    "color_elements": "Color Elements",
    "delta_mush": "Delta Mush",
}

_OPERATOR_ROLES = {
    "Vertex Input": "input",
    "Face Input": "input",
    "Edge Input": "input",
    "XYZ Space": "input",
    "Component Space": "input",
    "Curvature": "input",
    "Velocity": "input",
    "Node Influence": "input",
    "Tension Deform": "input",
    "Distort": "input",
    "Maxscript": "input",
    "Vector": "input",
    "Transform Elements": "input",
    "Color Elements": "input",
    "Delta Mush": "input",
    "Maxscript Process": "process",
    "Expression Float": "process",
    "Expression Point3": "process",
    "Scale": "process",
    "Clamp": "process",
    "Invert": "process",
    "Normalize": "process",
    "Curve": "process",
    "Smooth": "process",
    "Decay": "process",
    "Point3 To Float": "process",
    "Convert To SubObject Type": "process",
    "GeoQuantize": "process",
    "Color Space Conversion": "process",
    "Vertex Output": "output",
    "Face Output": "output",
    "Edge Output": "output",
}

_EXECUTABLE_OPERATORS = {
    "Maxscript",
    "Maxscript Process",
    "Expression Float",
    "Expression Point3",
}

_NODE_PROPERTIES = {"node", "pointnode", "stretchtarget", "squashtarget"}
_PROPERTY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLEND_MODES = {
    # Max 2027 stores 0 as no checked operation. Older MAXScript help still
    # documents Replace..Cross as 0..6, but the live modifier UI writes 1..7.
    "none": 0,
    "replace": 1,
    "add": 2,
    "subtract": 3,
    "multiply": 4,
    "divide": 5,
    "dot": 6,
    "dot_product": 6,
    "cross": 7,
    "cross_product": 7,
}


def _mxs_str(value: str) -> str:
    return safe_string(value)


def _mxs_object_block(name: str, body: str, *, not_found: str = "") -> str:
    safe_name = _mxs_str(name)
    msg = not_found or f"Object not found: {name}"
    safe_msg = msg.replace("\\", "\\\\").replace('"', '\\"')
    indented = "\n        ".join(line for line in body.splitlines() if line.strip())
    return f"""(
    local obj = getNodeByName "{safe_name}"
    if obj == undefined then "{safe_msg}"
    else
    (
        {indented}
    )
)"""


def _escape_script_literal(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _operator_display_name(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("operator type must be a non-empty string")
    normalized = re.sub(r"[\s-]+", "_", token).lower()
    display_name = _OPERATOR_NAMES.get(normalized, token)
    if len(display_name) > 128 or any(ord(char) < 32 for char in display_name):
        raise ValueError(f"Invalid Data Channel operator name: {token!r}")
    return display_name


def _operator_role(display_name: str) -> str:
    return _OPERATOR_ROLES.get(display_name, "unknown")


def _tagged_sequence(value: object, *, tag: str, length: int) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{tag} value must be a {length}-number sequence")
    items = list(value)
    if len(items) != length or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in items):
        raise ValueError(f"{tag} value must contain exactly {length} numbers")
    result = [float(item) for item in items]
    if any(not math.isfinite(item) for item in result):
        raise ValueError(f"{tag} values must be finite")
    return result


def _mxs_value(value: Any, *, property_name: str = "") -> str:
    """Serialize a structured value without accepting raw MAXScript expressions."""
    lowered_property = property_name.casefold()
    if lowered_property in _NODE_PROPERTIES and isinstance(value, str):
        safe_node = _mxs_str(value.strip())
        return f'mcpDcRequireNode "{safe_node}"'
    if lowered_property == "script" and isinstance(value, str):
        return f'"{_escape_script_literal(value)}"'
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Data Channel numeric properties must be finite")
        return repr(value)
    if isinstance(value, str):
        return f'"{_escape_script_literal(value)}"'
    if isinstance(value, Mapping):
        kind = str(value.get("type") or value.get("kind") or "").strip().lower()
        tagged_value = value.get("value")
        if kind == "node":
            node_name = str(value.get("name") or tagged_value or "").strip()
            if not node_name:
                raise ValueError("node value requires a non-empty name")
            return f'mcpDcRequireNode "{_mxs_str(node_name)}"'
        if kind == "point3":
            x, y, z = _tagged_sequence(tagged_value, tag="point3", length=3)
            return f"[{x!r},{y!r},{z!r}]"
        if kind == "color":
            red, green, blue = _tagged_sequence(tagged_value, tag="color", length=3)
            return f"(color {red!r} {green!r} {blue!r})"
        if kind == "name":
            token = str(tagged_value or "")
            if not token or not _PROPERTY_RE.fullmatch(token):
                raise ValueError("name value must be a simple MAXScript name token")
            return f"#{token}"
        if kind == "array":
            if not isinstance(tagged_value, Sequence) or isinstance(tagged_value, (str, bytes)):
                raise ValueError("array value must be a sequence")
            return "#(" + ",".join(_mxs_value(item) for item in tagged_value) + ")"
        raise ValueError(
            "Structured Data Channel values require type=node|point3|color|name|array"
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "#(" + ",".join(_mxs_value(item) for item in value) + ")"
    raise ValueError(f"Unsupported Data Channel property value: {type(value).__name__}")


def _requires_executable_authorization(operators: Sequence[Mapping[str, Any]]) -> bool:
    for operator in operators:
        display_name = _operator_display_name(operator.get("type", ""))
        if display_name in _EXECUTABLE_OPERATORS:
            return True
        if any(str(key).casefold() in {"script", "expression"} for key in (operator.get("params") or {})):
            return True
    return False


def _authorize_executable(allow_executable: bool) -> None:
    if not allow_executable:
        raise ValueError(
            "Executable Data Channel operators are blocked by default; "
            "pass allow_executable=true explicitly"
        )
    probe = client.send_command("true")
    meta = probe.get("meta") if isinstance(probe.get("meta"), dict) else {}
    if meta.get("safeMode", meta.get("safe_mode", True)) is not False:
        raise ValueError(
            "Executable Data Channel operators are blocked while MCP safe_mode is enabled"
        )


def _dc_catalog_helpers() -> list[str]:
    return [
        "fn mcpDcRequireNode nodeName = (",
        "    local theNode = getNodeByName nodeName exact:true",
        "    if theNode == undefined do throw (\"Data Channel node not found: \" + nodeName)",
        "    theNode",
        ")",
        "fn mcpDcAddOperatorByName theIF wantedName whereAt = (",
        "    local found = false",
        "    local idA = 0L",
        "    local idB = 0L",
        "    local operatorCount = theIF.NumberOperators()",
        "    for catalogIndex = 1 to operatorCount while not found do (",
        "        local candidateName = \"\"",
        "        theIF.OperatorName catalogIndex &candidateName",
        "        if stricmp candidateName wantedName == 0 do (",
        "            theIF.OperatorID catalogIndex &idA &idB",
        "            found = true",
        "        )",
        "    )",
        "    if not found do throw (\"Data Channel operator not available: \" + wantedName)",
        "    theIF.AddOperator idA idB whereAt",
        ")",
    ]


def _dc_json_helpers() -> str:
    return r'''
        fn mcpDcJsonEscape value = (
            local text = value as string
            text = substituteString text "\\" "\\\\"
            text = substituteString text "\"" "\\\""
            text = substituteString text "\r" "\\r"
            text = substituteString text "\n" "\\n"
            text = substituteString text "\t" "\\t"
            text
        )
        fn mcpDcJsonString value = ("\"" + (mcpDcJsonEscape value) + "\"")
        fn mcpDcIntArrayJson values = (
            local stream = stringStream ""
            format "[" to:stream
            for itemIndex = 1 to values.count do (
                if itemIndex > 1 do format "," to:stream
                format "%" values[itemIndex] to:stream
            )
            format "]" to:stream
            stream as string
        )
    '''


def _format_add_result(raw: str) -> str:
    if raw.startswith("OK|"):
        parts = raw.split("|", 8)
        if len(parts) >= 9:
            _, created, mod_idx, added, active_total, storage_total, order, warnings, obj_name = parts
            return _json.dumps({
                "modifier": "Data Channel",
                "createdModifier": created == "1",
                "modifierStackIndex": int(mod_idx),
                "operatorsAdded": int(added),
                "operatorsTotal": int(active_total),
                "operatorStorageCount": int(storage_total),
                "order": order,
                "warnings": [item for item in warnings.split(";") if item],
                "object": obj_name,
            })
        parts = raw.split("|", 6)
        if len(parts) >= 7:
            _, created, mod_idx, added, total, order, obj_name = parts
            return _json.dumps({
                "modifier": "Data Channel",
                "createdModifier": created == "1",
                "modifierStackIndex": int(mod_idx),
                "operatorsAdded": int(added),
                "operatorsTotal": int(total),
                "order": order,
                "object": obj_name,
            })
        # Legacy fallback
        _, op_count, order, obj_name = raw.split("|", 3)
        return _json.dumps({
            "modifier": "Data Channel",
            "operators": int(op_count),
            "order": order,
            "object": obj_name,
        })
    return raw


def _resolve_dc_modifier_lines(
    *,
    modifier_index: int,
    create_new: bool,
    display: bool,
) -> list[str]:
    """MAXScript to get or create the single Data Channel modifier on the object."""
    display_lit = "true" if display else "false"
    return [
        "local dcMod = undefined",
        "local created = 0",
        "local modStackIndex = 0",
        f"local forceNew = {'true' if create_new else 'false'}",
        f"local targetModIndex = {modifier_index}",
        "if targetModIndex < 0 do throw \"modifier_index must be zero or 1-based\"",
        "if forceNew then () else if targetModIndex > 0 then (",
        "    if targetModIndex > obj.modifiers.count do throw \"Modifier index out of range\"",
        "    if classof obj.modifiers[targetModIndex] != DataChannelModifier do throw (\"Not a DataChannelModifier at index \" + targetModIndex as string)",
        "    dcMod = obj.modifiers[targetModIndex]",
        "    modStackIndex = targetModIndex",
        ") else (",
        "    for i = 1 to obj.modifiers.count do (",
        "        if classof obj.modifiers[i] == DataChannelModifier do (",
        "            dcMod = obj.modifiers[i]",
        "            modStackIndex = i",
        "            exit",
        "        )",
        "    )",
        ")",
        "if dcMod == undefined then (",
        "    dcMod = DataChannelModifier()",
        f"    dcMod.display = {display_lit}",
        "    addModifier obj dcMod",
        "    modStackIndex = 1",
        "    created = 1",
        ")",
        "local dcIF = dcMod.DataChannelModifier",
        "local beforeStorageCount = dcMod.operators.count",
        "local beforeStackCount = dcIF.StackCount()",
    ]


def _find_dc_modifier_lines(*, modifier_index: int) -> list[str]:
    return [
        "local dcMod = undefined",
        "local modStackIndex = 0",
        f"local targetModIndex = {modifier_index}",
        "if targetModIndex < 0 do throw \"modifier_index must be zero or 1-based\"",
        "if targetModIndex > 0 then (",
        "    if targetModIndex > obj.modifiers.count do throw \"Modifier index out of range\"",
        "    if classof obj.modifiers[targetModIndex] != DataChannelModifier do throw (\"Not a DataChannelModifier at index \" + targetModIndex as string)",
        "    dcMod = obj.modifiers[targetModIndex]",
        "    modStackIndex = targetModIndex",
        ") else (",
        "    for stackIndex = 1 to obj.modifiers.count while dcMod == undefined do (",
        "        if classof obj.modifiers[stackIndex] == DataChannelModifier do (",
        "            dcMod = obj.modifiers[stackIndex]",
        "            modStackIndex = stackIndex",
        "        )",
        "    )",
        ")",
        "if dcMod == undefined do throw (\"No DataChannelModifier found on \" + obj.name)",
        "local dcIF = dcMod.DataChannelModifier",
        "local activeCount = dcIF.StackCount()",
    ]


def _property_assignment_lines(target: str, params: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, value in params.items():
        property_name = str(key or "").strip()
        if not _PROPERTY_RE.fullmatch(property_name):
            raise ValueError(f"Invalid Data Channel operator property: {key!r}")
        property_token = f"#{property_name}"
        lines.append(
            f'if not (isProperty {target} {property_token}) do throw '
            f'(\"Property {property_name} is not available on \" + ((classof {target}) as string))'
        )
        lines.append(
            f"setProperty {target} {property_token} "
            f"({_mxs_value(value, property_name=property_name)})"
        )
    return lines


def _operator_lines(
    operators: Sequence[Mapping[str, Any]],
    *,
    mod_var: str = "dcMod",
    interface_var: str = "dcIF",
    before_storage_var: str = "beforeStorageCount",
    before_stack_var: str = "beforeStackCount",
) -> list[str]:
    """Generate MAXScript to append operators to the DC internal stack."""
    lines: list[str] = []
    for op in operators:
        display_name = _operator_display_name(op.get("type", ""))
        lines.append(
            f'mcpDcAddOperatorByName {interface_var} "{_mxs_str(display_name)}" -1'
        )

    for i, op in enumerate(operators):
        params = op.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise ValueError("operator params must be an object")
        idx = f"{before_storage_var} + {i + 1}"
        for key, val in params.items():
            property_name = str(key or "").strip()
            if not _PROPERTY_RE.fullmatch(property_name):
                raise ValueError(f"Invalid Data Channel operator property: {key!r}")
            target = f"{mod_var}.operators[{idx}]"
            property_token = f"#{property_name}"
            lines.append(
                f'if not (isProperty {target} {property_token}) do throw '
                f'(\"Property {property_name} is not available on \" + ((classof {target}) as string))'
            )
            lines.append(
                f"setProperty {target} {property_token} "
                f"({_mxs_value(val, property_name=property_name)})"
            )

    for i, op in enumerate(operators):
        blend = op.get("blend")
        if blend is None:
            # A newly-created stack cannot evaluate until its first Input is
            # explicitly in Replace. Max leaves operator_ops at 0 otherwise.
            if i == 0 and _operator_role(_operator_display_name(op.get("type", ""))) == "input":
                idx = f"{before_storage_var} + {i + 1}"
                lines.append(
                    f"if {before_stack_var} == 0 do {mod_var}.operator_ops[{idx}] = "
                    f"{_BLEND_MODES['replace']}"
                )
        else:
            if isinstance(blend, str):
                blend_value = _BLEND_MODES.get(blend.strip().lower())
                if blend_value is None:
                    raise ValueError(f"Unknown Data Channel blend mode: {blend!r}")
            elif isinstance(blend, int) and not isinstance(blend, bool) and 0 <= blend <= 7:
                blend_value = blend
            else:
                raise ValueError(
                    "operator blend must be none/replace/add/subtract/multiply/"
                    "divide/dot/cross or 0..7"
                )
            idx = f"{before_storage_var} + {i + 1}"
            lines.append(f"{mod_var}.operator_ops[{idx}] = {blend_value}")

    return lines


def _result_line(*, warnings_var: str = "warningText") -> str:
    return (
        '"OK|" + (created as string) + "|" + (modStackIndex as string) + "|" + '
        "((dcIF.StackCount() - beforeStackCount) as string) + \"|\" + "
        "(dcIF.StackCount() as string) + \"|\" + "
        "(dcMod.operators.count as string) + \"|\" + "
        "(dcMod.operator_order as string) + \"|\" + "
        f"{warnings_var} + \"|\" + obj.name"
    )


@mcp.tool()
def add_data_channel(
    name: str,
    operators: DictList,
    order: Optional[IntList] = None,
    display: bool = True,
    modifier_index: int = 0,
    create_new: bool = False,
    expected_operator_count: Optional[int] = None,
    allow_executable: bool = False,
) -> str:
    """Append operators to a Data Channel modifier's internal stack.

    Reuses the first Data Channel modifier on the object by default. Operators
    are added via DataChannelModifier.AddOperator at StackCount() — not by
    stacking another Data Channel modifier on the object.

    modifier_index: 1-based object modifier stack slot (must be Data Channel).
    create_new: force a new Data Channel modifier on the object stack.
    """
    operator_list = list(operators or [])
    if not operator_list:
        return _json.dumps({
            "error": "operators must include at least one entry "
            "(typical graph: vertex_input + vertex_output)",
        })
    if modifier_index < 0:
        return _json.dumps({"error": "modifier_index must be zero or 1-based"})
    if expected_operator_count is not None and expected_operator_count < 0:
        return _json.dumps({"error": "expected_operator_count must be non-negative"})
    if any(not isinstance(operator, Mapping) for operator in operator_list):
        return _json.dumps({"error": "every operator entry must be an object"})

    try:
        if _requires_executable_authorization(operator_list):
            _authorize_executable(allow_executable)
        op_lines = _operator_lines(operator_list)
        probe_lines = _operator_lines(
            operator_list,
            mod_var="probeMod",
            interface_var="probeIF",
            before_storage_var="probeBeforeStorage",
            before_stack_var="probeBeforeStack",
        )
    except ValueError as exc:
        return _json.dumps({"error": str(exc)})

    roles = [_operator_role(_operator_display_name(operator.get("type", ""))) for operator in operator_list]
    warnings: list[str] = []
    if "input" not in roles:
        warnings.append("no input operator in appended graph")
    if "output" not in roles:
        warnings.append("no output operator in appended graph")
    warning_text = _escape_script_literal(";".join(warnings))

    body_lines = [
        *_dc_catalog_helpers(),
        "-- Preflight property types on an unattached modifier before touching the scene stack.",
        "local probeMod = DataChannelModifier()",
        "local probeIF = probeMod.DataChannelModifier",
        "local probeBeforeStorage = probeMod.operators.count",
        "local probeBeforeStack = probeIF.StackCount()",
        *probe_lines,
        *_resolve_dc_modifier_lines(
            modifier_index=modifier_index,
            create_new=create_new,
            display=display,
        ),
        (
            f"if beforeStackCount != {expected_operator_count} do throw "
            f'(\"Data Channel stack changed: expected {expected_operator_count}, found \" + beforeStackCount as string)'
            if expected_operator_count is not None
            else ""
        ),
        *op_lines,
        f'local warningText = "{warning_text}"',
    ]
    if order is not None:
        order_values = list(order)
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in order_values):
            return _json.dumps({"error": "order must contain non-negative integer storage indexes"})
        if len(set(order_values)) != len(order_values):
            return _json.dumps({"error": "order must not contain duplicates"})
        order_str = ", ".join(str(item) for item in order_values)
        body_lines.extend([
            f"local requestedOrder = #({order_str})",
            "if requestedOrder.count != dcIF.StackCount() do throw (\"Data Channel order length must equal active stack count \" + dcIF.StackCount() as string)",
            "for storageId in requestedOrder do if (findItem dcMod.operator_order storageId) == 0 do throw (\"Data Channel order references inactive storage index \" + storageId as string)",
            "dcMod.operator_order = requestedOrder",
        ])
    body_lines.append(_result_line())

    maxscript = _mxs_object_block(name, "\n        ".join(line for line in body_lines if line))
    try:
        response = client.send_command(maxscript)
        return _format_add_result(response.get("result", str(response)))
    except Exception as exc:
        return _json.dumps({"error": str(exc)})


@mcp.tool()
def list_dc_operators(
    query: str = "",
    include_properties: bool = False,
    limit: int = 50,
) -> str:
    """Discover the live Data Channel operator catalog.

    Use a narrow query with include_properties=true before building a graph;
    this keeps responses compact while exposing the exact properties available
    in the running 3ds Max version.
    """
    limit = max(1, min(int(limit), 100))
    safe_query = _mxs_str(str(query or "").strip().lower())
    property_block = ""
    if include_properties:
        property_block = r'''
                    dcIF.AddOperator idA idB -1
                    local storageIndex = dcMod.operators.count
                    local op = dcMod.operators[storageIndex]
                    format ",\"properties\":[" to:stream
                    local propNames = getPropNames op
                    local propWritten = 0
                    for propName in propNames where propName != #deprecated do (
                        if propWritten > 0 do format "," to:stream
                        local propValue = try (getProperty op propName) catch undefined
                        format "{\"name\":%,\"valueClass\":%,\"value\":%}" \
                            (mcpDcJsonString (propName as string)) \
                            (mcpDcJsonString ((classof propValue) as string)) \
                            (mcpDcJsonString (propValue as string)) to:stream
                        propWritten += 1
                    )
                    format "]" to:stream
        '''
    maxscript = rf'''(
    try (
        {_dc_json_helpers()}
        local dcMod = DataChannelModifier()
        local dcIF = dcMod.DataChannelModifier
        local catalogCount = dcIF.NumberOperators()
        local queryText = "{safe_query}"
        local stream = stringStream ""
        local written = 0
        format "{{\"catalogCount\":%,\"operators\":[" catalogCount to:stream
        for catalogIndex = 1 to catalogCount while written < {limit} do (
            local opName = ""
            local opTooltip = ""
            local opInfo = ""
            local idA = 0L
            local idB = 0L
            dcIF.OperatorName catalogIndex &opName
            if queryText == "" or findString (toLower opName) queryText != undefined do (
                dcIF.OperatorTooltip catalogIndex &opTooltip
                dcIF.OperatorInfo catalogIndex &opInfo
                dcIF.OperatorID catalogIndex &idA &idB
                if written > 0 do format "," to:stream
                format "{{\"catalogIndex\":%,\"name\":%,\"tooltip\":%,\"description\":%,\"classId\":[%,%]" \
                    catalogIndex \
                    (mcpDcJsonString opName) \
                    (mcpDcJsonString opTooltip) \
                    (mcpDcJsonString opInfo) \
                    (mcpDcJsonString (idA as string)) \
                    (mcpDcJsonString (idB as string)) to:stream
                {property_block}
                format "}}" to:stream
                written += 1
            )
        )
        format "],\"count\":%}}" written to:stream
        stream as string
    ) catch (
        "{{\"error\":" + (mcpDcJsonString (getCurrentException() as string)) + "}}"
    )
)'''
    try:
        response = client.send_command(maxscript)
        raw = response.get("result", str(response))
        payload = _json.loads(raw)
        aliases_by_name: dict[str, list[str]] = {}
        for alias, display_name in _OPERATOR_NAMES.items():
            aliases_by_name.setdefault(display_name, []).append(alias)
        for operator in payload.get("operators", []):
            display_name = str(operator.get("name", ""))
            operator["role"] = _operator_role(display_name)
            operator["aliases"] = aliases_by_name.get(display_name, [])
        return _json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        return _json.dumps({"error": str(exc)})


@mcp.tool()
def inspect_data_channel(
    name: str,
    modifier_index: int = 0,
    include_properties: bool = False,
    max_properties: int = 64,
) -> str:
    """Inspect the active Data Channel stack in processing order.

    operator_index is always the 1-based visible stack position. storageIndex
    is returned only as diagnostic proof because deleted operators leave stale
    entries in the modifier's internal operators array.
    """
    if modifier_index < 0:
        return _json.dumps({"error": "modifier_index must be zero or 1-based"})
    max_properties = max(0, min(int(max_properties), 256))
    safe = _mxs_str(name)
    property_block = ""
    if include_properties and max_properties:
        property_block = rf'''
                    format ",\"properties\":{{" to:stream
                    local props = getPropNames op
                    local propWritten = 0
                    for propName in props where propName != #deprecated and propWritten < {max_properties} do (
                        if propWritten > 0 do format "," to:stream
                        local propValue = try (getProperty op propName) catch undefined
                        local valueText = propValue as string
                        if (toLower (propName as string)) == "script" and valueText.count > 200 do valueText = (substring valueText 1 200) + "..."
                        format "%:{{\"valueClass\":%,\"value\":%}}" \
                            (mcpDcJsonString (propName as string)) \
                            (mcpDcJsonString ((classof propValue) as string)) \
                            (mcpDcJsonString valueText) to:stream
                        propWritten += 1
                    )
                    format "}}" to:stream
        '''
    maxscript = rf'''(
    try (
        {_dc_json_helpers()}
        local obj = getNodeByName "{safe}" exact:true
        if obj == undefined do throw "Object not found: {safe}"
        local dcMod = undefined
        local modStackIndex = 0
        if {modifier_index} > 0 then (
            if {modifier_index} > obj.modifiers.count do throw "Modifier index out of range"
            if classof obj.modifiers[{modifier_index}] != DataChannelModifier do throw "Not a DataChannelModifier at requested index"
            dcMod = obj.modifiers[{modifier_index}]
            modStackIndex = {modifier_index}
        ) else (
            for stackIndex = 1 to obj.modifiers.count while dcMod == undefined do (
                if classof obj.modifiers[stackIndex] == DataChannelModifier do (
                    dcMod = obj.modifiers[stackIndex]
                    modStackIndex = stackIndex
                )
            )
        )
        if dcMod == undefined do throw ("No DataChannelModifier found on " + obj.name)
        local dcIF = dcMod.DataChannelModifier
        local activeCount = dcIF.StackCount()
        local uiActive = false
        local uiErrorText = ""
        local currentModifier = try (modPanel.getCurrentObject()) catch undefined
        if currentModifier == dcMod do (
            uiActive = true
            local hwndRows = windows.getChildrenHWND #max
            if hwndRows != undefined do (
                for hwndRow in hwndRows where hwndRow.count >= 5 and uiErrorText == "" do (
                    local windowClass = hwndRow[4] as string
                    local windowText = hwndRow[5] as string
                    if windowClass == "CustStatus" and matchPattern windowText pattern:"Error :*" ignoreCase:true do (
                        uiErrorText = windowText
                    )
                )
            )
        )
        local stream = stringStream ""
        format "{{\"object\":%,\"modifierStackIndex\":%,\"display\":%,\"operatorCount\":%,\"operatorStorageCount\":%,\"storageOrder\":%,\"uiActive\":%,\"uiErrorText\":%,\"operators\":[" \
            (mcpDcJsonString obj.name) modStackIndex dcMod.display activeCount dcMod.operators.count \
            (mcpDcIntArrayJson dcMod.operator_order) uiActive (mcpDcJsonString uiErrorText) to:stream
        for stackPosition = 1 to activeCount do (
            if stackPosition > 1 do format "," to:stream
            local storageIndex = dcMod.operator_order[stackPosition] + 1
            local op = dcMod.operators[storageIndex]
            local opName = ""
            try (dcIF.StackOperatorName stackPosition &opName) catch (opName = (classof op) as string)
            local blendValue = dcMod.operator_ops[storageIndex]
            format "{{\"operatorIndex\":%,\"storageIndex\":%,\"name\":%,\"class\":%,\"enabled\":%,\"frozen\":%,\"blend\":%" \
                stackPosition storageIndex \
                (mcpDcJsonString opName) \
                (mcpDcJsonString ((classof op) as string)) \
                dcMod.operator_enabled[storageIndex] \
                dcMod.operator_frozen[storageIndex] \
                blendValue to:stream
            {property_block}
            format "}}" to:stream
        )
        format "]}}" to:stream
        stream as string
    ) catch (
        "{{\"error\":" + (mcpDcJsonString (getCurrentException() as string)) + "}}"
    )
)'''
    try:
        response = client.send_command(maxscript)
        raw = response.get("result", str(response))
        payload = _json.loads(raw)
        for operator in payload.get("operators", []):
            display_name = str(operator.get("name", ""))
            operator["role"] = _operator_role(display_name)
            blend_value = operator.get("blend")
            operator["blendName"] = next(
                (name for name, value in _BLEND_MODES.items() if value == blend_value and "_" not in name),
                "unknown",
            )
        roles = {operator.get("role") for operator in payload.get("operators", [])}
        warnings = []
        if payload.get("operatorCount", 0) and "input" not in roles:
            warnings.append("active stack has no input operator")
        if payload.get("operatorCount", 0) and "output" not in roles:
            warnings.append("active stack has no output operator")
        ui_active = bool(payload.pop("uiActive", False))
        ui_error_text = str(payload.pop("uiErrorText", "") or "").strip()
        structural_error = ""
        operators = payload.get("operators", [])
        if operators:
            first = operators[0]
            if first.get("role") != "input" or first.get("blend") != _BLEND_MODES["replace"]:
                structural_error = "Error : First operator must be an Input Operator and in Replace"
        error_text = ui_error_text or structural_error
        if error_text and error_text not in warnings:
            warnings.append(error_text)
        if error_text:
            validation_status = "invalid"
            validation_valid: Optional[bool] = False
            validation_source = "ui" if ui_error_text else "structural"
        elif ui_active:
            validation_status = "valid"
            validation_valid = True
            validation_source = "ui"
        else:
            validation_status = "structurally_valid"
            validation_valid = None
            validation_source = "structural"
        payload["validation"] = {
            "status": validation_status,
            "valid": validation_valid,
            "errorText": error_text or None,
            "source": validation_source,
            "uiActive": ui_active,
        }
        payload["warnings"] = warnings
        return _json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        return _json.dumps({"error": str(exc)})


@mcp.tool()
def set_data_channel_operator(
    name: str,
    operator_index: int,
    params: dict,
    modifier_index: int = 0,
    expected_operator_count: Optional[int] = None,
    allow_executable: bool = False,
) -> str:
    """Set properties on a 1-based visible stack operator with rollback on error."""
    if operator_index < 1:
        return _json.dumps({"error": "operator_index must be 1-based"})
    if not isinstance(params, Mapping) or not params:
        return _json.dumps({"error": "params must include at least one property"})
    if expected_operator_count is not None and expected_operator_count < 0:
        return _json.dumps({"error": "expected_operator_count must be non-negative"})
    try:
        if any(str(key).casefold() in {"script", "expression"} for key in params):
            _authorize_executable(allow_executable)
        assignment_lines = _property_assignment_lines("op", params)
    except ValueError as exc:
        return _json.dumps({"error": str(exc)})

    property_names = [str(key).strip() for key in params]
    validation_lines = [
        f'if not (isProperty op #{property_name}) do throw '
        f'(\"Property {property_name} is not available on \" + ((classof op) as string))'
        for property_name in property_names
    ]
    snapshot_lines = [f"append oldValues (getProperty op #{property_name})" for property_name in property_names]
    rollback_lines = [
        f"try (setProperty op #{property_name} oldValues[{index}]) catch ()"
        for index, property_name in enumerate(property_names, start=1)
    ]
    safe = _mxs_str(name)
    body_lines = [
        *_dc_catalog_helpers(),
        *_find_dc_modifier_lines(modifier_index=modifier_index),
        f"if {operator_index} > activeCount do throw \"Operator index out of range\"",
        (
            f"if activeCount != {expected_operator_count} do throw "
            f'(\"Data Channel stack changed: expected {expected_operator_count}, found \" + activeCount as string)'
            if expected_operator_count is not None
            else ""
        ),
        f"local storageIndex = dcMod.operator_order[{operator_index}] + 1",
        "local op = dcMod.operators[storageIndex]",
        *validation_lines,
        "local oldValues = #()",
        *snapshot_lines,
        "local updateError = undefined",
        "try (",
        *[f"    {line}" for line in assignment_lines],
        ") catch (updateError = getCurrentException())",
        "if updateError != undefined do (",
        *[f"    {line}" for line in rollback_lines],
        "    throw updateError",
        ")",
        (
            '"{\\"object\\":\\"" + obj.name + "\\",\\"modifierStackIndex\\":" + '
            '(modStackIndex as string) + ",\\"operatorIndex\\":" + '
            f'({operator_index} as string) + ",\\"storageIndex\\":" + (storageIndex as string) + '
            '",\\"class\\":\\"" + ((classof op) as string) + "\\",\\"updatedProperties\\":" + '
            f'"{_escape_script_literal(_json.dumps(property_names))}" + "}}"'
        ),
    ]
    maxscript = _mxs_object_block(name, "\n        ".join(line for line in body_lines if line))
    try:
        response = client.send_command(maxscript)
        return response.get("result", str(response))
    except Exception as exc:
        return _json.dumps({"error": str(exc)})


@mcp.tool()
def manage_data_channel_stack(
    name: str,
    action: str,
    operator_index: int = 0,
    order: Optional[IntList] = None,
    enabled: Optional[bool] = None,
    frozen: Optional[bool] = None,
    blend_mode: str = "",
    display: Optional[bool] = None,
    modifier_index: int = 0,
    expected_operator_count: Optional[int] = None,
) -> str:
    """Safely edit a Data Channel stack.

    Actions: delete, reorder, set_enabled, set_frozen, set_blend,
    set_display, select. operator_index and reorder entries are 1-based visible
    stack positions, never the modifier's stale internal storage indexes.
    """
    normalized_action = str(action or "").strip().lower()
    valid_actions = {
        "delete",
        "reorder",
        "set_enabled",
        "set_frozen",
        "set_blend",
        "set_display",
        "select",
    }
    if normalized_action not in valid_actions:
        return _json.dumps({"error": f"action must be one of {', '.join(sorted(valid_actions))}"})
    if modifier_index < 0:
        return _json.dumps({"error": "modifier_index must be zero or 1-based"})
    if expected_operator_count is not None and expected_operator_count < 0:
        return _json.dumps({"error": "expected_operator_count must be non-negative"})

    operation_lines: list[str] = []
    if normalized_action in {"delete", "set_enabled", "set_frozen", "set_blend", "select"}:
        if operator_index < 1:
            return _json.dumps({"error": "operator_index must be 1-based for this action"})
        operation_lines.extend([
            f"if {operator_index} > activeCount do throw \"Operator index out of range\"",
            f"local storageIndex = dcMod.operator_order[{operator_index}] + 1",
        ])

    if normalized_action == "delete":
        operation_lines.append(f"dcIF.DeleteStackOperator {operator_index}")
    elif normalized_action == "reorder":
        order_values = list(order or [])
        if not order_values:
            return _json.dumps({"error": "order is required for reorder"})
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in order_values):
            return _json.dumps({"error": "order must contain 1-based positive stack positions"})
        if len(set(order_values)) != len(order_values):
            return _json.dumps({"error": "order must not contain duplicates"})
        literal = ",".join(str(item) for item in order_values)
        operation_lines.extend([
            f"local requestedPositions = #({literal})",
            "if requestedPositions.count != activeCount do throw (\"Reorder requires exactly \" + activeCount as string + \" stack positions\")",
            "for position in requestedPositions do if position < 1 or position > activeCount do throw (\"Reorder position out of range: \" + position as string)",
            "local oldOrder = copy dcMod.operator_order #nomap",
            "local newOrder = for position in requestedPositions collect oldOrder[position]",
            "dcMod.operator_order = newOrder",
        ])
    elif normalized_action == "set_enabled":
        if enabled is None:
            return _json.dumps({"error": "enabled is required for set_enabled"})
        operation_lines.append(f"dcMod.operator_enabled[storageIndex] = {'true' if enabled else 'false'}")
    elif normalized_action == "set_frozen":
        if frozen is None:
            return _json.dumps({"error": "frozen is required for set_frozen"})
        operation_lines.append(f"dcMod.operator_frozen[storageIndex] = {'true' if frozen else 'false'}")
    elif normalized_action == "set_blend":
        token = str(blend_mode or "").strip().lower()
        if token.isdigit():
            blend_value = int(token)
        else:
            blend_value = _BLEND_MODES.get(token, -1)
        if not 0 <= blend_value <= 7:
            return _json.dumps({
                "error": "blend_mode must be none/replace/add/subtract/multiply/"
                "divide/dot/cross or 0..7"
            })
        operation_lines.append(f"dcMod.operator_ops[storageIndex] = {blend_value}")
    elif normalized_action == "set_display":
        if display is None:
            return _json.dumps({"error": "display is required for set_display"})
        operation_lines.append(f"dcMod.display = {'true' if display else 'false'}")
    elif normalized_action == "select":
        operation_lines.append(f"dcIF.SelectStackOperator {operator_index}")

    safe = _mxs_str(name)
    body_lines = [
        _dc_json_helpers(),
        *_find_dc_modifier_lines(modifier_index=modifier_index),
        (
            f"if activeCount != {expected_operator_count} do throw "
            f'(\"Data Channel stack changed: expected {expected_operator_count}, found \" + activeCount as string)'
            if expected_operator_count is not None
            else ""
        ),
        *operation_lines,
        "local resultStream = stringStream \"\"",
        (
            f'format "{{\\\"object\\\":%,\\\"modifierStackIndex\\\":%,\\\"action\\\":%,\\\"operatorCount\\\":%,\\\"operatorStorageCount\\\":%,\\\"storageOrder\\\":%}}" '
            f'(mcpDcJsonString obj.name) modStackIndex (mcpDcJsonString "{_mxs_str(normalized_action)}") '
            "(dcIF.StackCount()) dcMod.operators.count (mcpDcIntArrayJson dcMod.operator_order) to:resultStream"
        ),
        "resultStream as string",
    ]
    maxscript = _mxs_object_block(name, "\n        ".join(line for line in body_lines if line))
    try:
        response = client.send_command(maxscript)
        return response.get("result", str(response))
    except Exception as exc:
        return _json.dumps({"error": str(exc)})


@mcp.tool()
def add_dc_script_operator(
    name: str,
    script: str,
    element_type: int = 0,
    data_type: int = 0,
    output_to: str = "selection",
    modifier_index: int = 0,
    create_new: bool = False,
    add_output: bool = True,
    expected_operator_count: Optional[int] = None,
    allow_executable: bool = False,
) -> str:
    """Add the legacy MAXScript input operator with explicit executable authorization."""
    if element_type not in {0, 1}:
        return _json.dumps({"error": "element_type must be 0 (vertices) or 1 (faces)"})
    if data_type not in {0, 1}:
        return _json.dumps({"error": "data_type must be 0 (float) or 1 (point3)"})
    if not isinstance(script, str) or not script.strip():
        return _json.dumps({"error": "script must be a non-empty string"})
    if "on Process" not in script:
        script = f"""on Process theNode theMesh elementType outputType outputArray do
(
    if theMesh == undefined then return 0
    local nv = polyop.getNumVerts theMesh
    local nf = polyop.getNumFaces theMesh
{script}
)"""

    output_config = {
        "selection": ("vertex_output", {"output": 4, "channelNum": 1}),
        "position": ("vertex_output", {"output": 0, "channelNum": 1}),
        "vertex_color": ("vertex_output", {"output": 1, "channelNum": 0}),
        "map_channel": ("vertex_output", {"output": 2, "channelNum": 1}),
        "normals": ("vertex_output", {"output": 3, "channelNum": 1}),
        "mat_id": ("face_output", {"output": 1}),
    }
    normalized_output = str(output_to or "").strip().lower()
    if normalized_output not in output_config:
        return _json.dumps({"error": f"output_to must be one of {', '.join(sorted(output_config))}"})
    operators: list[dict[str, Any]] = [{
        "type": "maxscript",
        "params": {
            "script": script,
            "elementType": element_type,
            "dataType": data_type,
        },
    }]
    if add_output:
        output_type, output_params = output_config[normalized_output]
        operators.append({"type": output_type, "params": output_params})
    return add_data_channel(
        name=name,
        operators=operators,
        display=True,
        modifier_index=modifier_index,
        create_new=create_new,
        expected_operator_count=expected_operator_count,
        allow_executable=allow_executable,
    )


@mcp.tool()
def list_dc_presets() -> str:
    """List available Data Channel presets without creating a scene object."""
    maxscript = """(
    local dcMod = DataChannelModifier()
    local dcIF = dcMod.DataChannelModifier
    dcIF.GatherOperators()

    local count = dcIF.PresetCount()
    local result = "["
    for i = 1 to count do (
        local pname = ""
        dcIF.PresetName i &pname
        result += "\\"" + pname + "\\""
        if i < count do result += ", "
    )
    result += "]"
    result
)"""
    try:
        response = client.send_command(maxscript)
        return response.get("result", str(response))
    except Exception as exc:
        return _json.dumps({"error": str(exc)})


@mcp.tool()
def load_dc_preset(
    name: str,
    preset_name: str,
    modifier_index: int = 0,
    create_new: bool = False,
    expected_operator_count: Optional[int] = None,
) -> str:
    """Load a Data Channel preset into the object's DC modifier internal stack."""
    if not str(preset_name or "").strip():
        return _json.dumps({"error": "preset_name must be non-empty"})
    if expected_operator_count is not None and expected_operator_count < 0:
        return _json.dumps({"error": "expected_operator_count must be non-negative"})
    safe_preset = _mxs_str(preset_name)
    body = [
        *_resolve_dc_modifier_lines(
            modifier_index=modifier_index,
            create_new=create_new,
            display=True,
        ),
        (
            f"if beforeStackCount != {expected_operator_count} do throw "
            f'(\"Data Channel stack changed: expected {expected_operator_count}, found \" + beforeStackCount as string)'
            if expected_operator_count is not None
            else ""
        ),
        f'dcIF.LoadPreset "{safe_preset}"',
        (
            '"{\\"object\\":\\"" + obj.name + "\\",\\"modifierStackIndex\\":" + '
            '(modStackIndex as string) + ",\\"preset\\":\\"' + safe_preset +
            '\\",\\"operatorCount\\":" + (dcIF.StackCount() as string) + "}"'
        ),
    ]
    maxscript = _mxs_object_block(name, "\n        ".join(line for line in body if line))
    try:
        response = client.send_command(maxscript)
        return response.get("result", str(response))
    except Exception as exc:
        return _json.dumps({"error": str(exc)})
