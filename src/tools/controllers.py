"""Animation Controller tools — assign and manage controllers on sub-anim tracks.

Controllers drive animation in 3ds Max: script controllers for custom logic,
constraints for target-based behavior, noise for randomness, expressions for
math-driven motion, and list controllers for layered blending.
"""

from typing import Optional
import json as _json
from ..server import mcp, client
from ..coerce import DictList
from src.helpers.maxscript import safe_string, safe_name, normalize_subanim_path


# ── Controller type registry ────────────────────────────────────────
# Maps friendly names to MAXScript constructor expressions.
_CONTROLLER_MAP = {
    # Script controllers
    "float_script":             "float_script()",
    "position_script":          "position_script()",
    "rotation_script":          "rotation_script()",
    "scale_script":             "scale_script()",
    "point3_script":            "point3_script()",
    # Constraints
    "position_constraint":      "Position_Constraint()",
    "orientation_constraint":   "Orientation_Constraint()",
    "lookat_constraint":        "LookAt_Constraint()",
    "path_constraint":          "Path_Constraint()",
    "surface_constraint":       "Surface_Constraint()",
    "link_constraint":          "Link_Constraint()",
    "attachment_constraint":    "Attachment_Constraint()",
    # Noise controllers
    "noise_float":              "Noise_Float()",
    "noise_position":           "Noise_Position()",
    "noise_rotation":           "Noise_Rotation()",
    "noise_scale":              "Noise_Scale()",
    # List controllers
    "float_list":               "Float_List()",
    "position_list":            "Position_List()",
    "rotation_list":            "Rotation_List()",
    "scale_list":               "Scale_List()",
    # Expression controllers
    "float_expression":         "Float_Expression()",
    "position_expression":      "Position_Expression()",
    # Spring
    "spring":                   "Spring()",
}

# Maps non-list controller types to their corresponding list controller type.
_LIST_FOR_TYPE = {
    "float_script": "float_list", "noise_float": "float_list",
    "float_expression": "float_list",
    "position_script": "position_list", "noise_position": "position_list",
    "position_constraint": "position_list", "path_constraint": "position_list",
    "surface_constraint": "position_list", "spring": "position_list",
    "position_expression": "position_list",
    "rotation_script": "rotation_list", "noise_rotation": "rotation_list",
    "orientation_constraint": "rotation_list", "lookat_constraint": "rotation_list",
    "scale_script": "scale_list", "noise_scale": "scale_list",
    "point3_script": "position_list",
}

# Sets for type detection
_SCRIPT_TYPES = {"float_script", "position_script", "rotation_script", "scale_script", "point3_script"}
_CONSTRAINT_TYPES = {"position_constraint", "orientation_constraint", "lookat_constraint",
                     "path_constraint", "surface_constraint"}
_LINK_TYPE = "link_constraint"
_ATTACHMENT_TYPE = "attachment_constraint"
_EXPRESSION_TYPES = {"float_expression", "position_expression"}
_LIST_TYPES = {"float_list", "position_list", "rotation_list", "scale_list"}


def _build_prop_lines(prefix: str, params: dict) -> list[str]:
    """Build MAXScript lines to set properties on a controller variable."""
    lines = []
    for key, val in params.items():
        safe_key = safe_string(key)
        if isinstance(val, bool):
            lines.append(f'try ({prefix}.{safe_key} = {"true" if val else "false"}) catch ()')
        elif isinstance(val, (int, float)):
            lines.append(f'try ({prefix}.{safe_key} = {val}) catch ()')
        elif isinstance(val, str):
            safe_val = safe_string(val)
            lines.append(f'try ({prefix}.{safe_key} = "{safe_val}") catch ()')
    return lines


@mcp.tool()
def assign_controller(
    name: str,
    param_path: str,
    controller_type: str,
    script: Optional[str] = None,
    variables: Optional[DictList] = None,
    params: Optional[dict] = None,
    layer: bool = False,
) -> str:
    """Create and assign a controller to a sub-anim track."""
    if client.native_available:
        payload = {
            "name": name,
            "param_path": param_path,
            "controller_type": controller_type,
            "script": script or "",
            "variables": list(variables) if variables else [],
            "params": {k: str(v) for k, v in (params or {}).items()},
            "layer": layer,
        }
        response = client.send_command(_json.dumps(payload), cmd_type="native:assign_controller")
        return response.get("result", "")

    ct = controller_type.lower()
    if ct not in _CONTROLLER_MAP:
        keys = ", ".join(sorted(_CONTROLLER_MAP.keys()))
        return f"Unknown controller_type: {controller_type}. Available: {keys}"

    safe_obj = safe_name(name)
    safe_path = safe_string(normalize_subanim_path(param_path))
    sep = "" if safe_path.startswith("[") else "."
    ctor = _CONTROLLER_MAP[ct]

    # ── Layer mode: wrap in list controller, add on top ──
    if layer:
        if ct in _LIST_TYPES:
            return "Cannot layer a list controller — use layer=False to assign directly"
        list_type = _LIST_FOR_TYPE.get(ct)
        if not list_type:
            return f"Cannot layer {controller_type} — no list controller type available"
        list_ctor = _CONTROLLER_MAP[list_type]

        lines = [
            f'local obj = getNodeByName "{safe_obj}"',
            f'if obj == undefined do return "Object not found: {safe_obj}"',
            f'local sa = execute("$\'" + obj.name + "\'{sep}{safe_path}")',
            f'if sa == undefined do return "Param not found: {safe_path}"',
            # Wrap in list if not already one
            'local clsLower = toLower ((classof sa.controller) as string)',
            'if (findString clsLower "list") == undefined do (',
            '    local curVal = try (sa.value) catch undefined',
            f'    sa.controller = {list_ctor}',
            '    try ((getSubAnim sa.controller 1).controller.value = curVal) catch ()',
            ')',
            # Add new controller to Available slot (must use $ path)
            f'execute("$\'" + obj.name + "\'{sep}{safe_path}.controller.Available.controller = {ctor}")',
            # Get reference to the newly added controller (before Available and Weights)
            'local listCtrl = sa.controller',
            'local ctrl = (getSubAnim listCtrl (listCtrl.numsubs - 2)).controller',
        ]

        # Configure the new sub-controller (variables, script, params)
        lines.extend(_build_controller_config(ct, script, variables, params, "ctrl"))

        # Build result
        lines.append('local clsStr = (classof ctrl) as string')
        lines.append('local listClsStr = (classof listCtrl) as string')
        lines.append(f'"{{\\\"controller\\\": \\\"" + clsStr + "\\\", \\\"list\\\": \\\"" + listClsStr + "\\\", \\\"object\\\": \\\"" + obj.name + "\\\", \\\"param_path\\\": \\\"{safe_path}\\\", \\\"layered\\\": true}}"')

        maxscript = "(\n    " + "\n    ".join(lines) + "\n)"
        response = client.send_command(maxscript)
        return response.get("result", str(response))

    # ── Direct assignment mode ──
    lines = [
        f'local obj = getNodeByName "{safe_obj}"',
        f'if obj == undefined do return "Object not found: {safe_obj}"',
        f'local sa = execute("$\'" + obj.name + "\'{sep}{safe_path}")',
        f'if sa == undefined do return "Param not found: {safe_path}"',
        f'sa.controller = {ctor}',
        'local ctrl = sa.controller',
    ]

    lines.extend(_build_controller_config(ct, script, variables, params, "ctrl"))

    # Build result JSON
    lines.append('local clsStr = (classof ctrl) as string')
    lines.append(f'"{{\\\"controller\\\": \\\"" + clsStr + "\\\", \\\"object\\\": \\\"" + obj.name + "\\\", \\\"param_path\\\": \\\"{safe_path}\\\"}}"')

    maxscript = "(\n    " + "\n    ".join(lines) + "\n)"
    response = client.send_command(maxscript)
    return response.get("result", str(response))


def _build_controller_config(
    ct: str,
    script: Optional[str],
    variables: Optional[list[dict]],
    params: Optional[dict],
    ctrl_var: str,
) -> list[str]:
    """Build MAXScript lines to configure a controller (variables, script, params)."""
    lines = []

    # Add node variables / constraint targets
    if variables:
        if ct in _SCRIPT_TYPES:
            for var in variables:
                vname = safe_string(var.get("var_name", ""))
                vobj = safe_name(var.get("object", ""))
                lines.append(f'local varNode = getNodeByName "{vobj}"')
                lines.append(f'if varNode != undefined do {ctrl_var}.addNode "{vname}" varNode')
        elif ct in _CONSTRAINT_TYPES:
            for var in variables:
                tobj = safe_name(var.get("object", ""))
                weight = var.get("weight", 50.0)
                lines.append(f'local tgtNode = getNodeByName "{tobj}"')
                lines.append(f'if tgtNode != undefined do {ctrl_var}.appendTarget tgtNode {weight}')
        elif ct == _LINK_TYPE:
            for var in variables:
                tobj = safe_name(var.get("object", ""))
                frame = var.get("frame", 0)
                lines.append(f'local tgtNode = getNodeByName "{tobj}"')
                lines.append(f'if tgtNode != undefined do {ctrl_var}.addTarget tgtNode {frame}')
        elif ct == _ATTACHMENT_TYPE:
            for var in variables:
                tobj = safe_name(var.get("object", ""))
                face = var.get("face", 1)
                lines.append(f'local tgtNode = getNodeByName "{tobj}"')
                lines.append(f'if tgtNode != undefined do {ctrl_var}.appendTarget tgtNode {face}')
        elif ct in _EXPRESSION_TYPES:
            for var in variables:
                vname = safe_string(var.get("var_name", ""))
                tobj = safe_name(var.get("object", ""))
                tpath = safe_string(var.get("target_param_path", ""))
                tsep = "" if tpath.startswith("[") else "."
                lines.append(f'local tgtNode = getNodeByName "{tobj}"')
                lines.append(f'if tgtNode != undefined do (')
                lines.append(f'    local tgtSA = execute("$\'" + tgtNode.name + "\'{tsep}{tpath}")')
                lines.append(f'    if tgtSA != undefined do {ctrl_var}.addScalarTarget "{vname}" tgtSA.controller')
                lines.append(f')')

    # Set script/expression text
    if script is not None:
        safe_script = (script
                       .replace("\\", "\\\\")
                       .replace('"', '\\"')
                       .replace("\n", "\\n")
                       .replace("\r", "\\r")
                       .replace("\t", "\\t"))
        if ct in _SCRIPT_TYPES:
            lines.append(f'{ctrl_var}.script = "{safe_script}"')
        elif ct in _EXPRESSION_TYPES:
            lines.append(f'{ctrl_var}.setExpression "{safe_script}"')
            lines.append(f'{ctrl_var}.update()')

    # Set arbitrary properties
    if params:
        lines.extend(_build_prop_lines(ctrl_var, params))

    return lines


@mcp.tool()
def inspect_controller(
    name: str,
    param_path: str,
) -> str:
    """Inspect the controller on a specific sub-anim track."""
    if client.native_available:
        payload = _json.dumps({"name": name, "param_path": param_path})
        response = client.send_command(payload, cmd_type="native:inspect_controller")
        return response.get("result", "")

    safe_obj = safe_name(name)
    safe_path = safe_string(normalize_subanim_path(param_path))
    sep = "" if safe_path.startswith("[") else "."

    maxscript = f"""(
    local obj = getNodeByName "{safe_obj}"
    if obj == undefined do return "Object not found: {safe_obj}"
    local sa = execute("$'" + obj.name + "'{sep}{safe_path}")
    if sa == undefined do return "Param not found: {safe_path}"
    local ctrl = sa.controller
    if ctrl == undefined do return "No controller on: {safe_path}"

    local clsStr = (classof ctrl) as string
    local superStr = (superClassOf ctrl) as string
    local valStr = try ((sa.value as string)) catch "?"
    valStr = substituteString valStr "\\"" "'"
    valStr = substituteString valStr "\\n" " "
    if valStr.count > 200 do valStr = (substring valStr 1 200) + "..."

    local result = "{{\\n"
    result += "  \\"controller\\": \\"" + clsStr + "\\",\\n"
    result += "  \\"superclass\\": \\"" + superStr + "\\",\\n"
    result += "  \\"object\\": \\"" + obj.name + "\\",\\n"
    result += "  \\"param_path\\": \\"{safe_path}\\",\\n"
    result += "  \\"value\\": \\"" + valStr + "\\",\\n"

    -- Properties table
    result += "  \\"properties\\": ["
    local props = #()
    try (props = getPropNames ctrl) catch ()
    local first = true
    for p in props do (
        local pVal = try ((getProperty ctrl p) as string) catch "?"
        pVal = substituteString pVal "\\"" "'"
        pVal = substituteString pVal "\\n" " "
        if pVal.count > 200 do pVal = (substring pVal 1 200) + "..."
        local pType = try ((classof (getProperty ctrl p)) as string) catch "?"
        if not first do result += ","
        first = false
        result += "\\n    {{\\"name\\": \\"" + (p as string) + "\\", \\"value\\": \\"" + pVal + "\\", \\"runtimeType\\": \\"" + pType + "\\"}}"
    )
    result += "\\n  ]"

    -- Type-specific sections
    local clsLower = toLower clsStr

    -- Script controllers
    if (findString clsLower "script") != undefined do (
        local scriptText = try (ctrl.script) catch ""
        scriptText = substituteString scriptText "\\"" "'"
        scriptText = substituteString scriptText "\\n" "\\\\n"
        scriptText = substituteString scriptText "\\r" ""
        if scriptText.count > 500 do scriptText = (substring scriptText 1 500) + "..."
        result += ",\\n  \\"script\\": \\"" + scriptText + "\\""

        -- Node variables
        local numNodes = try (ctrl.getCount()) catch 0
        result += ",\\n  \\"node_variables\\": ["
        local nFirst = true
        for i = 1 to numNodes do (
            local nName = try (ctrl.getName i) catch "?"
            local nTarget = try ((ctrl.getNode i).name) catch "null"
            if not nFirst do result += ","
            nFirst = false
            result += "\\n    {{\\"name\\": \\"" + nName + "\\", \\"target\\": \\"" + nTarget + "\\"}}"
        )
        result += "\\n  ]"
    )

    -- Constraint controllers (position, orientation, lookat, path, surface)
    if (findString clsLower "constraint") != undefined and (findString clsLower "link") == undefined and (findString clsLower "attachment") == undefined do (
        local numTgts = try (ctrl.getNumTargets()) catch 0
        result += ",\\n  \\"targets\\": ["
        local tFirst = true
        for i = 1 to numTgts do (
            local tgtNode = try (ctrl.getTarget i) catch undefined
            local tgtName = if tgtNode != undefined then tgtNode.name else "null"
            local tgtWeight = try (ctrl.getWeight i) catch 0.0
            if not tFirst do result += ","
            tFirst = false
            result += "\\n    {{\\"target\\": \\"" + tgtName + "\\", \\"weight\\": " + (tgtWeight as string) + "}}"
        )
        result += "\\n  ]"
    )

    -- Link constraint
    if (findString clsLower "link") != undefined do (
        local numLinks = try (ctrl.getNumTargets()) catch 0
        result += ",\\n  \\"targets\\": ["
        local lFirst = true
        for i = 1 to numLinks do (
            local lNode = try (ctrl.getTarget i) catch undefined
            local lName = if lNode != undefined then lNode.name else "null"
            local lFrame = try (ctrl.getFrameNumber i) catch 0
            if not lFirst do result += ","
            lFirst = false
            result += "\\n    {{\\"target\\": \\"" + lName + "\\", \\"frame\\": " + (lFrame as string) + "}}"
        )
        result += "\\n  ]"
    )

    -- Expression controllers
    if (findString clsLower "expression") != undefined do (
        local exprText = try (ctrl.getExpression()) catch ""
        exprText = substituteString exprText "\\"" "'"
        exprText = substituteString exprText "\\n" "\\\\n"
        if exprText.count > 500 do exprText = (substring exprText 1 500) + "..."
        result += ",\\n  \\"expression\\": \\"" + exprText + "\\""
        local nScalars = try (ctrl.NumScalars) catch 0
        local nVectors = try (ctrl.NumVectors) catch 0
        result += ",\\n  \\"num_scalars\\": " + (nScalars as string)
        result += ",\\n  \\"num_vectors\\": " + (nVectors as string)
    )

    -- List controllers
    if (findString clsLower "list") != undefined do (
        result += ",\\n  \\"sub_controllers\\": ["
        local subCount = ctrl.numsubs
        local sFirst = true
        for i = 1 to subCount do (
            local subCtrl = try (getSubAnim ctrl i) catch undefined
            if subCtrl == undefined do continue
            local subName = try ((getSubAnimName ctrl i) as string) catch "?"
            local subCls = try ((classof subCtrl.controller) as string) catch "?"
            if not sFirst do result += ","
            sFirst = false
            result += "\\n    {{\\"index\\": " + (i as string) + ", \\"name\\": \\"" + subName + "\\", \\"class\\": \\"" + subCls + "\\"}}"
        )
        result += "\\n  ]"
        local avgVal = try (ctrl.average as string) catch "?"
        result += ",\\n  \\"average\\": \\"" + avgVal + "\\""
    )

    result += "\\n}}"
    result
)"""
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def inspect_track_view(
    name: str,
    depth: int = 4,
    filter: Optional[str] = None,
    include_values: bool = True,
) -> str:
    """Inspect an object's controller/track tree in a Track View-style hierarchy."""
    if client.native_available:
        payload = _json.dumps({
            "name": name,
            "depth": min(max(int(depth), 1), 6),
            "filter": filter or "",
            "include_values": include_values,
        })
        response = client.send_command(payload, cmd_type="native:inspect_track_view")
        return response.get("result", "")

    safe_obj = safe_name(name)
    safe_filter = safe_string((filter or "").lower())
    max_depth = min(max(int(depth), 1), 6)
    include_values_str = "true" if include_values else "false"

    maxscript = f"""(
    local esc = MCP_Server.escapeJsonString
    local obj = getNodeByName "{safe_obj}"
    if obj == undefined do return "{{\\"error\\":\\"Object not found: {safe_obj}\\"}}"

    local filterStr = "{safe_filter}"
    local includeValues = {include_values_str}

    fn matchesFilter text filterText = (
        if filterText == "" then return true
        if text == undefined then return false
        (findString (toLower text) filterText) != undefined
    )

    fn compactValue sub includeVals = (
        if not includeVals then return ""
        local valStr = try ((sub.value) as string) catch ""
        valStr = esc valStr
        if valStr.count > 120 do valStr = (substring valStr 1 120) + "..."
        valStr
    )

    fn buildTrack sub path trackName depthLeft filterText includeVals = (
        if depthLeft < 0 then return undefined

        local ctrl = try (sub.controller) catch undefined
        local ctrlClass = if ctrl != undefined then ((classof ctrl) as string) else ""
        local ctrlSuper = if ctrl != undefined then ((superClassOf ctrl) as string) else ""
        local rawName = if trackName != undefined then (trackName as string) else "?"
        local pathStr = path
        local valueStr = compactValue sub includeVals

        local childrenJson = "["
        local childCount = 0
        local firstChild = true

        if depthLeft > 0 do (
            local numSubs = try (sub.numsubs) catch 0
            for i = 1 to numSubs do (
                local child = try (getSubAnim sub i) catch undefined
                if child == undefined do continue
                local childName = try ((getSubAnimName sub i) as string) catch undefined
                if childName == undefined do continue
                local childPath = pathStr + "[#" + childName + "]"
                local childJson = buildTrack child childPath childName (depthLeft - 1) filterText includeVals
                if childJson != undefined do (
                    if not firstChild do childrenJson += ","
                    firstChild = false
                    childrenJson += childJson
                    childCount += 1
                )
            )
        )
        childrenJson += "]"

        local haystack = (toLower rawName) + " " + (toLower pathStr) + " " + (toLower ctrlClass)
        local keep = matchesFilter haystack filterText or childCount > 0
        if not keep then return undefined

        local json = "{{"
        json += "\\"name\\":\\"" + (esc rawName) + "\\""
        json += ",\\"path\\":\\"" + (esc pathStr) + "\\""
        if ctrlClass != "" then json += ",\\"controller\\":\\"" + (esc ctrlClass) + "\\""
        if ctrlSuper != "" then json += ",\\"controllerSuperclass\\":\\"" + (esc ctrlSuper) + "\\""
        if valueStr != "" then json += ",\\"value\\":\\"" + valueStr + "\\""
        json += ",\\"childCount\\":" + (childCount as string)
        json += ",\\"children\\":" + childrenJson
        json += "}}"
        json
    )

    local tracksJson = "["
    local firstTrack = true
    local rootCount = 0
    local numSubs = try (obj.numsubs) catch 0
    for i = 1 to numSubs do (
        local child = try (getSubAnim obj i) catch undefined
        if child == undefined do continue
        local childName = try ((getSubAnimName obj i) as string) catch undefined
        if childName == undefined do continue
        local childPath = "[#" + childName + "]"
        local childJson = buildTrack child childPath childName ({max_depth} - 1) filterStr includeValues
        if childJson != undefined do (
            if not firstTrack do tracksJson += ","
            firstTrack = false
            tracksJson += childJson
            rootCount += 1
        )
    )
    tracksJson += "]"

    "{{" + \
      "\\"object\\":\\"" + (esc obj.name) + "\\"," + \
      "\\"class\\":\\"" + (esc ((classof obj) as string)) + "\\"," + \
      "\\"depth\\":" + ({max_depth} as string) + "," + \
      "\\"filter\\":\\"" + (esc filterStr) + "\\"," + \
      "\\"rootTrackCount\\":" + (rootCount as string) + "," + \
      "\\"tracks\\":" + tracksJson + \
    "}}"
)"""
    response = client.send_command(maxscript, timeout=30.0)
    return response.get("result", str(response))


@mcp.tool()
def add_controller_target(
    name: str,
    param_path: str,
    target_object: str,
    var_name: Optional[str] = None,
    weight: float = 50.0,
    frame: int = 0,
) -> str:
    """Add a node variable or constraint target to an existing controller."""
    # Always use MAXScript TCP path — ctrl.addNode triggers script re-evaluation
    # which causes re-entrancy inside the native ExecuteSync handler.
    safe_obj = safe_name(name)
    safe_path = safe_string(normalize_subanim_path(param_path))
    safe_target = safe_name(target_object)
    safe_var = safe_string(var_name) if var_name else ""
    sep = "" if safe_path.startswith("[") else "."

    lines = [
        f'local obj = getNodeByName "{safe_obj}"',
        f'if obj == undefined do return "Object not found: {safe_obj}"',
        f'local sa = execute("$\'" + obj.name + "\'{sep}{safe_path}")',
        f'if sa == undefined do return "Param not found: {safe_path}"',
        'local ctrl = sa.controller',
        f'if ctrl == undefined do return "No controller on: {safe_path}"',
        f'local tgtNode = getNodeByName "{safe_target}"',
        f'if tgtNode == undefined do return "Target not found: {safe_target}"',
        'local clsStr = toLower ((classof ctrl) as string)',
        'local done = false',
    ]

    # Script controllers: addNode
    lines.append('if (findString clsStr "script") != undefined do (')
    if safe_var:
        lines.append(f'    ctrl.addNode "{safe_var}" tgtNode')
        lines.append('    done = true')
    else:
        lines.append('    return "var_name is required for script controllers"')
    lines.append(')')

    # Standard constraints: appendTarget
    lines.append('if not done and (findString clsStr "constraint") != undefined and (findString clsStr "link") == undefined and (findString clsStr "attachment") == undefined do (')
    lines.append(f'    ctrl.appendTarget tgtNode {weight}')
    lines.append('    done = true')
    lines.append(')')

    # Link constraint: addTarget with frame
    lines.append('if not done and (findString clsStr "link") != undefined do (')
    lines.append(f'    ctrl.addTarget tgtNode {frame}')
    lines.append('    done = true')
    lines.append(')')

    # Attachment constraint: appendTarget with face
    lines.append('if not done and (findString clsStr "attachment") != undefined do (')
    lines.append(f'    ctrl.appendTarget tgtNode {int(frame)}')
    lines.append('    done = true')
    lines.append(')')

    # Expression controllers: not supported here
    lines.append('if not done and (findString clsStr "expression") != undefined do (')
    lines.append('    return "Use assign_controller with variables for expression scalar targets (requires target_param_path)"')
    lines.append(')')

    lines.append('if not done do return "Controller type not supported for adding targets: " + (classof ctrl) as string')
    lines.append('"Added target \'" + tgtNode.name + "\' to " + (classof ctrl) as string + " on " + obj.name')

    maxscript = "(\n    " + "\n    ".join(lines) + "\n)"
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def set_controller_props(
    name: str,
    param_path: str,
    script: Optional[str] = None,
    params: Optional[dict] = None,
) -> str:
    """Modify script text or properties on an existing controller."""
    if client.native_available:
        payload = {
            "name": name,
            "param_path": param_path,
            "script": script or "",
            "params": {k: str(v) for k, v in (params or {}).items()},
        }
        response = client.send_command(_json.dumps(payload), cmd_type="native:set_controller_props")
        return response.get("result", "")

    safe_obj = safe_name(name)
    safe_path = safe_string(normalize_subanim_path(param_path))
    sep = "" if safe_path.startswith("[") else "."

    lines = [
        f'local obj = getNodeByName "{safe_obj}"',
        f'if obj == undefined do return "Object not found: {safe_obj}"',
        f'local sa = execute("$\'" + obj.name + "\'{sep}{safe_path}")',
        f'if sa == undefined do return "Param not found: {safe_path}"',
        'local ctrl = sa.controller',
        f'if ctrl == undefined do return "No controller on: {safe_path}"',
        'local clsStr = toLower ((classof ctrl) as string)',
    ]

    if script is not None:
        safe_script = (script
                       .replace("\\", "\\\\")
                       .replace('"', '\\"')
                       .replace("\n", "\\n")
                       .replace("\r", "\\r")
                       .replace("\t", "\\t"))
        lines.append(f'if (findString clsStr "script") != undefined then (')
        lines.append(f'    ctrl.script = "{safe_script}"')
        lines.append(f') else if (findString clsStr "expression") != undefined then (')
        lines.append(f'    ctrl.setExpression "{safe_script}"')
        lines.append(f'    ctrl.update()')
        lines.append(f') else (')
        lines.append(f'    return "Controller does not support script/expression: " + (classof ctrl) as string')
        lines.append(f')')

    if params:
        lines.extend(_build_prop_lines("ctrl", params))

    lines.append('"Updated " + (classof ctrl) as string + " on " + obj.name + " ' + safe_path + '"')

    maxscript = "(\n    " + "\n    ".join(lines) + "\n)"
    response = client.send_command(maxscript)
    return response.get("result", str(response))
