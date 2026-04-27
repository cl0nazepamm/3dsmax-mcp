"""Wire Parameters tools — link parameters between objects with expressions.

Wire Parameters connects sub-anim parameters so changes to one object
automatically drive another (e.g., a sphere's radius controls a bend
modifier's angle). These tools expose wiring as first-class operations.
"""

from typing import Optional
import json as _json
from ..server import mcp, client
from src.helpers.maxscript import safe_string, safe_name, normalize_subanim_path


@mcp.tool()
def list_wireable_params(
    name: str,
    filter: Optional[str] = None,
    depth: int = 3,
    max_visits: int = 20000,
    max_results: int = 500,
    max_ms: int = 5000,
    max_fanout: int = 200,
) -> str:
    """Discover sub-anim parameters on an object that can be wired."""
    if client.native_available:
        payload = _json.dumps({
            "name": name,
            "filter": filter or "",
            "depth": min(max(depth, 1), 5),
            "max_visits": max_visits,
            "max_results": max_results,
            "max_ms": max_ms,
            "max_fanout": max_fanout,
        })
        response = client.send_command(payload, cmd_type="native:list_wireable_params")
        return response.get("result", "")

    safe = safe_string(name)
    d = min(max(depth, 1), 5)

    filter_check = ""
    if filter:
        safe_filter = safe_string(filter.lower())
        filter_check = f'local filterStr = "{safe_filter}"'

    maxscript = f"""(
    local obj = getNodeByName "{safe}"
    if obj == undefined do return "Object not found: {safe}"
    {filter_check}
    local results = #()

    fn walkSubAnims sa path depthLeft = (
        if depthLeft <= 0 do return()
        local n = sa.numsubs
        for i = 1 to n do (
            local sub = getSubAnim sa i
            if sub == undefined do continue
            local subName = try (getSubAnimName sa i) catch undefined
            if subName == undefined do continue
            local nameStr = subName as string
            local childPath = path + "[#" + nameStr + "]"

            local ctrl = sub.controller
            local isWireable = ctrl != undefined
            local childCount = sub.numsubs

            if childCount == 0 or isWireable do (
                local valStr = try ((sub.value as string)) catch "?"
                local typeStr = if ctrl != undefined then ((classof ctrl) as string) else "none"
                -- Sanitize value string
                valStr = substituteString valStr "\\"" "'"
                valStr = substituteString valStr "\\n" " "
                if valStr.count > 100 do valStr = (substring valStr 1 100) + "..."
                local entry = "{{\\"path\\": \\"" + childPath + "\\", \\"value\\": \\"" + valStr + "\\", \\"type\\": \\"" + typeStr + "\\", \\"is_wireable\\": " + (if isWireable then "true" else "false") + "}}"
                {('if (findString (toLower childPath) filterStr) != undefined do append results entry' if filter else 'append results entry')}
            )

            if childCount > 0 do (
                walkSubAnims sub childPath (depthLeft - 1)
            )
        )
    )

    walkSubAnims obj "" {d}

    local result = "["
    for i = 1 to results.count do (
        result += results[i]
        if i < results.count do result += ","
    )
    result += "]"
    result
)"""
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def wire_params(
    source_object: str,
    source_param: str,
    target_object: str,
    target_param: str,
    expression: str,
    two_way: bool = False,
    reverse_expression: Optional[str] = None,
) -> str:
    """Connect parameters between objects with a wire expression."""
    if client.native_available:
        payload = {
            "source_object": source_object,
            "source_param": source_param,
            "target_object": target_object,
            "target_param": target_param,
            "expression": expression,
            "two_way": two_way,
            "reverse_expression": reverse_expression or "",
        }
        response = client.send_command(_json.dumps(payload), cmd_type="native:wire_params")
        return response.get("result", "")

    if two_way and not reverse_expression:
        return "reverse_expression is required when two_way=True"

    safe_src_obj = safe_string(source_object)
    safe_tgt_obj = safe_string(target_object)
    safe_src_param = safe_string(normalize_subanim_path(source_param))
    safe_tgt_param = safe_string(normalize_subanim_path(target_param))
    safe_expr = safe_string(expression)

    # If path starts with "[", no dot separator needed (e.g. $'Obj'[#transform])
    src_sep = "" if safe_src_param.startswith("[") else "."
    tgt_sep = "" if safe_tgt_param.startswith("[") else "."

    lines = [
        f'local srcObj = getNodeByName "{safe_src_obj}"',
        f'if srcObj == undefined do return "Source object not found: {safe_src_obj}"',
        f'local tgtObj = getNodeByName "{safe_tgt_obj}"',
        f'if tgtObj == undefined do return "Target object not found: {safe_tgt_obj}"',
        f'local srcSA = execute("$\'" + srcObj.name + "\'{src_sep}" + "{safe_src_param}")',
        f'if srcSA == undefined do return "Source param not found: {safe_src_param}"',
        f'local tgtSA = execute("$\'" + tgtObj.name + "\'{tgt_sep}" + "{safe_tgt_param}")',
        f'if tgtSA == undefined do return "Target param not found: {safe_tgt_param}"',
    ]

    if two_way:
        safe_rev_expr = safe_string(reverse_expression)
        lines.append(f'paramWire.connect2way srcSA tgtSA "{safe_expr}" "{safe_rev_expr}"')
        lines.append(f'"Wired 2-way: " + srcObj.name + ".{safe_src_param} <-> " + tgtObj.name + ".{safe_tgt_param} (fwd: {safe_expr}, rev: {safe_rev_expr})"')
    else:
        lines.append(f'paramWire.connect srcSA tgtSA "{safe_expr}"')
        lines.append(f'"Wired: " + srcObj.name + ".{safe_src_param} -> " + tgtObj.name + ".{safe_tgt_param} ({safe_expr})"')

    maxscript = "(\n    " + "\n    ".join(lines) + "\n)"
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def get_wired_params(name: str) -> str:
    """Show existing wire connections on an object."""
    if client.native_available:
        payload = _json.dumps({"name": name})
        response = client.send_command(payload, cmd_type="native:get_wired_params")
        return response.get("result", "[]")

    safe = safe_string(name)
    maxscript = f"""(
    local obj = getNodeByName "{safe}"
    if obj == undefined do return "Object not found: {safe}"

    local wireClasses = #("Float_Wire", "Point3_Wire", "Position_Wire", "Rotation_Wire", "Scale_Wire")
    local results = #()

    fn findWires sa path = (
        local n = sa.numsubs
        for i = 1 to n do (
            local sub = getSubAnim sa i
            if sub == undefined do continue
            local subName = try (getSubAnimName sa i) catch undefined
            if subName == undefined do continue
            local nameStr = subName as string
            local childPath = path + "[#" + nameStr + "]"

            local ctrl = sub.controller
            if ctrl != undefined do (
                local clsName = (classof ctrl) as string
                local isWire = false
                for wc in wireClasses where wc == clsName do isWire = true
                if isWire do (
                    local nWires = try (ctrl.numWires) catch 0
                    local exprArr = "["
                    for w = 1 to nWires do (
                        local expr = try (ctrl.getExprText w) catch "?"
                        expr = substituteString expr "\\"" "'"
                        expr = substituteString expr "\\n" " "
                        exprArr += "\\"" + expr + "\\""
                        if w < nWires do exprArr += ","
                    )
                    exprArr += "]"
                    local entry = "{{\\"param_path\\": \\"" + childPath + "\\", \\"controller_class\\": \\"" + clsName + "\\", \\"num_wires\\": " + nWires as string + ", \\"expressions\\": " + exprArr + "}}"
                    append results entry
                )
            )

            if sub.numsubs > 0 do (
                findWires sub childPath
            )
        )
    )

    findWires obj ""

    local result = "["
    for i = 1 to results.count do (
        result += results[i]
        if i < results.count do result += ","
    )
    result += "]"
    result
)"""
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def unwire_params(
    object_name: str,
    param_path: str,
) -> str:
    """Disconnect a wired parameter."""
    if client.native_available:
        payload = _json.dumps({"object_name": object_name, "param_path": param_path})
        response = client.send_command(payload, cmd_type="native:unwire_params")
        return response.get("result", "")

    safe_obj = safe_string(object_name)
    safe_param = safe_string(normalize_subanim_path(param_path))
    param_sep = "" if safe_param.startswith("[") else "."
    maxscript = f"""(
    local obj = getNodeByName "{safe_obj}"
    if obj == undefined do return "Object not found: {safe_obj}"
    local sa = execute("$'" + obj.name + "'{param_sep}" + "{safe_param}")
    if sa == undefined do return "Param not found: {safe_param}"
    local ctrl = sa.controller
    if ctrl == undefined do return "No controller on: {safe_param}"
    local clsName = (classof ctrl) as string
    local wireClasses = #("Float_Wire", "Point3_Wire", "Position_Wire", "Rotation_Wire", "Scale_Wire")
    local isWire = false
    for wc in wireClasses where wc == clsName do isWire = true
    if not isWire do return "Not a wire controller: " + clsName
    paramWire.disconnect sa
    "Unwired: " + obj.name + ".{safe_param} (was " + clsName + ")"
)"""
    response = client.send_command(maxscript)
    return response.get("result", str(response))
