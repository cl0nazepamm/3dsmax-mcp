"""Data Channel modifier tools — procedural per-vertex/face data processing.

The Data Channel modifier is 3ds Max's node-based data processing system
(similar to Houdini VOPs) that lives in the modifier stack. It chains
operators that read mesh data, process it, and output to channels like
position, selection, vertex color, UVs, normals, etc.
"""

from typing import Optional
from ..server import mcp, client
from ..coerce import DictList, IntList
from src.helpers.maxscript import safe_string


# ── Operator ID registry ────────────────────────────────────────────
# Maps operator names to their (idA, idB) class IDs for AddOperator().
_OP_IDS = {
    # Input operators
    "vertex_input":       (3658656257, 0),
    "face_input":         (38019502, 0),
    "edge_input":         (590351565, 0),
    "xyz_space":          (236038690, 0),
    "component_space":    (236038707, 0),
    "curvature":          (236108612, 0),
    "velocity":           (237091669, 0),
    "node_influence":     (3416675101, 0),
    "tension_deform":     (1215902043, 0),
    "distort":            (301607866, 0),
    "maxscript":          (2597005274, 0),
    "maxscript_process":  (3180516783, 0),
    "expression_float":   (1185521650, 0),
    "expression_point3":  (1185521649, 0),
    # Process operators
    "vector":             (1155607757, 0),
    "scale":              (283192250, 0),
    "clamp":              (551627706, 0),
    "invert":             (1135524769, 0),
    "normalize":          (103725985, 0),
    "curve":              (1944136634, 0),
    "smooth":             (2481007546, 0),
    "decay":              (284830921, 0),
    "point3_to_float":    (1137503061, 0),
    "convert_subobject":  (2888899789, 0),
    "geo_quantize":       (496046533, 0),
    "color_space":        (3257339550, 0),
    # Output operators
    "vertex_output":      (2882382387, 0),
    "face_output":        (52689454, 0),
    "edge_output":        (17934909, 0),
    # Composite operators
    "transform_elements": (655960264, 0),
    "color_elements":     (1270620223, 0),
    "delta_mush":         (3367109027, 0),
}


@mcp.tool()
def add_data_channel(
    name: str,
    operators: DictList,
    order: Optional[IntList] = None,
    display: bool = True,
) -> str:
    """Add a Data Channel modifier with a complete operator graph."""
    safe = safe_string(name)

    # Build MAXScript to add DC modifier and configure operators
    lines = [
        f'local obj = getNodeByName "{safe}"',
        'if obj == undefined do return "Object not found"',
        'local dcMod = DataChannelModifier()',
        f'dcMod.display = {"true" if display else "false"}',
        'addModifier obj dcMod',
        'local dcIF = dcMod.DataChannelModifier',
    ]

    # Add each operator
    for i, op in enumerate(operators):
        op_type = op.get("type", "").lower()
        if op_type not in _OP_IDS:
            return f"Unknown operator type: {op_type}. Available: {', '.join(sorted(_OP_IDS.keys()))}"
        id_a, id_b = _OP_IDS[op_type]
        lines.append(f'dcIF.AddOperator {id_a}L {id_b}L {i}')

    # Configure operator parameters
    for i, op in enumerate(operators):
        params = op.get("params", {})
        idx = i + 1  # 1-based in MAXScript
        for key, val in params.items():
            if key == "node" and isinstance(val, str):
                # Node reference
                safe_node = safe_string(val)
                lines.append(f'dcMod.operators[{idx}].{key} = getNodeByName "{safe_node}"')
            elif key == "script" and isinstance(val, str):
                # Script operator — need to escape the script content
                safe_script = val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
                lines.append(f'dcMod.operators[{idx}].{key} = "{safe_script}"')
            elif isinstance(val, bool):
                lines.append(f'dcMod.operators[{idx}].{key} = {"true" if val else "false"}')
            elif isinstance(val, (int, float)):
                lines.append(f'dcMod.operators[{idx}].{key} = {val}')
            elif isinstance(val, str):
                # String value — could be MAXScript expression like "color 255 0 0"
                lines.append(f'dcMod.operators[{idx}].{key} = {val}')

    # Set blend modes (operator_ops)
    for i, op in enumerate(operators):
        blend = op.get("blend")
        if blend is not None:
            idx = i + 1  # 1-based
            lines.append(f'dcMod.operator_ops[{idx}] = {blend}')

    # Set processing order
    if order is not None:
        order_str = ", ".join(str(o) for o in order)
        lines.append(f'dcMod.operator_order = #({order_str})')

    # Build result
    lines.append('local result = "{"')
    lines.append('result += "\\"modifier\\": \\"Data Channel\\","')
    lines.append('result += "\\"operators\\": " + dcMod.operators.count as string + ","')
    lines.append('result += "\\"order\\": \\"" + dcMod.operator_order as string + "\\","')
    lines.append('result += "\\"object\\": \\"" + obj.name + "\\""')
    lines.append('result += "}"')
    lines.append('result')

    maxscript = "(\n    " + "\n    ".join(lines) + "\n)"
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def inspect_data_channel(
    name: str,
    modifier_index: int = 1,
) -> str:
    """Inspect a Data Channel modifier's operator graph."""
    safe = safe_string(name)
    maxscript = f"""(
    local obj = getNodeByName "{safe}"
    if obj == undefined do return "Object not found: {safe}"
    local dcMod = undefined
    if {modifier_index} > 0 and {modifier_index} <= obj.modifiers.count do (
        local m = obj.modifiers[{modifier_index}]
        if classof m == DataChannelModifier do dcMod = m
    )
    if dcMod == undefined do (
        -- Try to find first DC modifier
        for m in obj.modifiers where classof m == DataChannelModifier do (
            dcMod = m
            exit
        )
    )
    if dcMod == undefined do return "No DataChannelModifier found on " + obj.name

    local result = "{{\\n"
    result += "  \\"object\\": \\"" + obj.name + "\\",\\n"
    result += "  \\"display\\": " + dcMod.display as string + ",\\n"
    result += "  \\"order\\": \\"" + dcMod.operator_order as string + "\\",\\n"
    result += "  \\"blend_modes\\": \\"" + dcMod.operator_ops as string + "\\",\\n"
    result += "  \\"operators\\": [\\n"

    for i = 1 to dcMod.operators.count do (
        local op = dcMod.operators[i]
        local cls = (classof op) as string
        local enabled = dcMod.operator_enabled[i]
        local frozen = dcMod.operator_frozen[i]

        result += "    {{\\"index\\": " + i as string
        result += ", \\"class\\": \\"" + cls + "\\""
        result += ", \\"enabled\\": " + enabled as string
        result += ", \\"frozen\\": " + frozen as string

        -- Get all properties
        local props = getPropNames op
        for p in props where p != #deprecated do (
            local val = try ((getProperty op p) as string) catch "?"
            local pname = p as string
            -- Truncate long scripts
            if pname == "script" and val.count > 200 do (
                val = (substring val 1 200) + "..."
            )
            val = substituteString val "\\"" "'"
            val = substituteString val "\\n" " "
            result += ", \\"" + pname + "\\": \\"" + val + "\\""
        )

        result += "}}"
        if i < dcMod.operators.count do result += ","
        result += "\\n"
    )

    result += "  ]\\n}}"
    result
)"""
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def set_data_channel_operator(
    name: str,
    operator_index: int,
    params: dict,
    modifier_index: int = 1,
) -> str:
    """Set properties on a specific operator in a Data Channel modifier."""
    safe = safe_string(name)

    lines = [
        f'local obj = getNodeByName "{safe}"',
        f'if obj == undefined do return "Object not found: {safe}"',
        f'if {modifier_index} > obj.modifiers.count do return "Modifier index out of range"',
        f'local dcMod = obj.modifiers[{modifier_index}]',
        f'if classof dcMod != DataChannelModifier do return "Not a DataChannelModifier"',
        f'if {operator_index} > dcMod.operators.count do return "Operator index out of range"',
        f'local op = dcMod.operators[{operator_index}]',
    ]

    for key, val in params.items():
        if key == "node" and isinstance(val, str):
            safe_node = safe_string(val)
            lines.append(f'op.{key} = getNodeByName "{safe_node}"')
        elif key == "script" and isinstance(val, str):
            safe_script = val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
            lines.append(f'op.{key} = "{safe_script}"')
        elif isinstance(val, bool):
            lines.append(f'op.{key} = {"true" if val else "false"}')
        elif isinstance(val, (int, float)):
            lines.append(f'op.{key} = {val}')
        elif isinstance(val, str):
            lines.append(f'op.{key} = {val}')

    lines.append(f'"Updated operator {operator_index} (" + (classof op) as string + ") on " + obj.name')

    maxscript = "(\n    " + "\n    ".join(lines) + "\n)"
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def add_dc_script_operator(
    name: str,
    script: str,
    element_type: int = 0,
    data_type: int = 0,
    output_to: str = "selection",
    modifier_index: int = 0,
) -> str:
    """Add a Data Channel modifier with a MAXScript operator for custom per-vertex/face logic."""
    safe = safe_string(name)

    # Wrap script if needed
    if "on Process" not in script:
        # User provided body-only — wrap it
        script = f"""on Process theNode theMesh elementType outputType outputArray do
(
    if theMesh == undefined then return 0
    local nv = polyop.getNumVerts theMesh
    local nf = polyop.getNumFaces theMesh
{script}
)"""

    # Escape script for MAXScript string
    safe_script = (script
                   .replace("\\", "\\\\")
                   .replace('"', '\\"')
                   .replace("\n", "\\n")
                   .replace("\r", "\\r")
                   .replace("\t", "\\t"))

    # Determine output operator and params
    output_config = {
        "selection":     ("vertex_output", 4, 1),   # output=4(selection), channelNum=1
        "position":      ("vertex_output", 0, 1),   # output=0(position)
        "vertex_color":  ("vertex_output", 1, 0),   # output=1(vertColor)
        "map_channel":   ("vertex_output", 2, 1),   # output=2(mapChannel), channelNum=1
        "normals":       ("vertex_output", 3, 1),   # output=3(normals)
        "mat_id":        ("face_output", 1, 0),      # face output=1(matID)
    }

    out_type, out_val, out_chan = output_config.get(output_to, ("vertex_output", 4, 1))

    # Build MAXScript
    mxs_id_a, mxs_id_b = _OP_IDS["maxscript"]

    if modifier_index > 0:
        # Add to existing DC modifier
        lines = [
            f'local obj = getNodeByName "{safe}"',
            f'if obj == undefined do return "Object not found: {safe}"',
            f'local dcMod = obj.modifiers[{modifier_index}]',
            'if classof dcMod != DataChannelModifier do return "Not a DataChannelModifier"',
            'local dcIF = dcMod.DataChannelModifier',
            f'local pos = dcIF.StackCount()',
            f'dcIF.AddOperator {mxs_id_a}L {mxs_id_b}L pos',
            f'local scriptOp = dcMod.operators[dcMod.operators.count]',
            f'scriptOp.script = "{safe_script}"',
            f'scriptOp.elementtype = {element_type}',
            f'scriptOp.DataType = {data_type}',
        ]
    else:
        # Create new DC modifier with script + output
        out_id_a, out_id_b = _OP_IDS[out_type]
        lines = [
            f'local obj = getNodeByName "{safe}"',
            f'if obj == undefined do return "Object not found: {safe}"',
            'local dcMod = DataChannelModifier()',
            'dcMod.display = true',
            'addModifier obj dcMod',
            'local dcIF = dcMod.DataChannelModifier',
            # Add script operator
            f'dcIF.AddOperator {mxs_id_a}L {mxs_id_b}L 0',
            # Add output operator
            f'dcIF.AddOperator {out_id_a}L {out_id_b}L 1',
            # Configure script operator
            f'dcMod.operators[1].script = "{safe_script}"',
            f'dcMod.operators[1].elementtype = {element_type}',
            f'dcMod.operators[1].DataType = {data_type}',
            # Configure output
            f'dcMod.operators[2].output = {out_val}',
            f'dcMod.operators[2].channelNum = {out_chan}',
        ]

    lines.append('"Added Script Operator to " + obj.name + " -> output: ' + output_to + '"')

    maxscript = "(\n    " + "\n    ".join(lines) + "\n)"
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def list_dc_presets() -> str:
    """List all available Data Channel modifier presets."""
    maxscript = """(
    local b = Box name:"__dc_temp__" length:1 width:1 height:1
    convertToMesh b
    local dcMod = DataChannelModifier()
    addModifier b dcMod
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
    delete b
    result
)"""
    response = client.send_command(maxscript)
    return response.get("result", str(response))


@mcp.tool()
def load_dc_preset(
    name: str,
    preset_name: str,
) -> str:
    """Load a Data Channel modifier preset onto an object."""
    safe = safe_string(name)
    safe_preset = safe_string(preset_name)
    maxscript = f"""(
    local obj = getNodeByName "{safe}"
    if obj == undefined do return "Object not found: {safe}"
    local dcMod = DataChannelModifier()
    addModifier obj dcMod
    local dcIF = dcMod.DataChannelModifier
    dcIF.LoadPreset "{safe_preset}"
    "Loaded preset '{safe_preset}' onto " + obj.name + " (" + dcIF.StackCount() as string + " operators)"
)"""
    response = client.send_command(maxscript)
    return response.get("result", str(response))
