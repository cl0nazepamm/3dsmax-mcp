"""Material creation, assignment, and property manipulation tools for 3ds Max.

Covers the full material workflow: creating materials by class, assigning them
to objects, setting properties, creating texture maps, writing OSL shaders,
and managing Multi/Sub-Object sub-material slots.
Works with all material/map types: Arnold (ai_standard_surface), Physical,
Standard, OSLMap, Bitmaptexture, ai_bump2d, and any MAXScript-creatable class.
"""

from typing import Optional
from ..server import mcp, client


def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


@mcp.tool()
def assign_material(
    names: list[str],
    material_class: str,
    material_name: str = "",
    params: str = "",
) -> str:
    """Create a material and assign it to one or more objects.

    Use this when the user wants to apply a new material to objects — e.g.
    "make the body chrome", "give it a glass material", "assign Arnold surface".
    Creates the material, optionally sets initial parameters, and assigns it.
    To modify an existing material's properties, use set_material_property instead.

    Args:
        names: List of object names to assign the material to.
        material_class: Material class name (e.g. "ai_standard_surface",
                        "PhysicalMaterial", "StandardMaterial", "Multimaterial").
        material_name: Optional name for the material. Auto-generated if empty.
        params: Optional MAXScript parameters for creation
                (e.g. "base_color:(color 200 50 50) metalness:1.0").

    Returns:
        Confirmation with material name and assigned object count.
    """
    safe_mat_name = _safe_name(material_name)
    name_param = f' name:"{safe_mat_name}"' if material_name else ""
    name_arr = "#(" + ", ".join(f'"{_safe_name(n)}"' for n in names) + ")"

    maxscript = f"""(
        try (
            mat = {material_class}{name_param} {params}
            nameList = {name_arr}
            assignCount = 0
            notFound = #()
            for n in nameList do (
                obj = getNodeByName n
                if obj != undefined then (
                    obj.material = mat
                    assignCount += 1
                ) else (
                    append notFound n
                )
            )
            msg = "Created " + (classof mat) as string + " \\\"" + mat.name + "\\\" and assigned to " + (assignCount as string) + " object(s)"
            if notFound.count > 0 do msg += " | Not found: " + (notFound as string)
            msg
        ) catch (
            "Error: " + (getCurrentException())
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_material_property(
    name: str,
    property: str,
    value: str,
    sub_material_index: int = 0,
) -> str:
    """Set a property on an object's material (or sub-material).

    Use this to change any material parameter — colors, floats, booleans,
    texture map slots, or clearing maps. This is the write counterpart to
    inspect_properties with target="material". Handles all material types
    including Arnold (ai_standard_surface), Physical, Standard, and
    Multi/Sub-Object (use sub_material_index to target a sub-material).

    Common patterns:
    - Set color: property="base_color" value="color 200 50 50"
    - Set float: property="metalness" value="1.0"
    - Set bool: property="thin_walled" value="true"
    - Clear a texture map: property="base_color_shader" value="undefined"
    - Assign a map by variable: property="specular_color_shader" value="thinFilm"
      (where thinFilm was created via execute_maxscript)

    Args:
        name: The object name whose material to modify (e.g. "CC_Base_Body").
        property: Material property name (e.g. "base_color", "metalness",
                  "specular_roughness", "coat", "base_color_shader").
                  Use inspect_properties with target="material" to discover names.
        value: Value as a MAXScript expression (e.g. "1.0", "color 255 0 0",
               "true", "undefined").
        sub_material_index: For Multi/Sub-Object materials, 1-based index of
                           the sub-material to modify. 0 = modify the top-level
                           material directly (default).

    Returns:
        Confirmation with the property name and new value, or error message.
    """
    safe = _safe_name(name)
    safe_prop = _safe_name(property)

    if sub_material_index > 0:
        mat_expr = f"obj.material[{sub_material_index}]"
        mat_label = f"sub-material [{sub_material_index}]"
    else:
        mat_expr = "obj.material"
        mat_label = "material"

    maxscript = f"""(
        obj = getNodeByName "{safe}"
        if obj == undefined then (
            "Object not found: {safe}"
        ) else if obj.material == undefined then (
            "No material assigned to {safe}"
        ) else (
            mat = {mat_expr}
            if mat == undefined then (
                "Sub-material index {sub_material_index} not found on {safe}"
            ) else (
                try (
                    mat.{safe_prop} = {value}
                    readback = (getproperty mat #{safe_prop}) as string
                    "Set " + mat.name + ".{safe_prop} = " + readback
                ) catch (
                    "Error setting {safe_prop}: " + (getCurrentException())
                )
            )
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_material_properties(
    name: str,
    properties: dict[str, str],
    sub_material_index: int = 0,
) -> str:
    """Set multiple properties on an object's material in a single call.

    Use this when you need to change several material parameters at once —
    e.g. setting up a chrome look (metalness, base_color, specular_roughness,
    coat all in one call). Much more efficient than multiple set_material_property
    calls. Each property-value pair is a MAXScript expression.

    Common use cases:
    - Chrome: {"metalness": "1.0", "base_color": "color 200 210 230",
               "specular_roughness": "0.05", "coat": "0.8"}
    - Glass: {"transmission": "0.9", "specular_roughness": "0.0",
              "specular_IOR": "1.5", "thin_walled": "true"}
    - Clear all maps: {"base_color_shader": "undefined",
                       "specular_shader": "undefined",
                       "subsurface_shader": "undefined"}

    Args:
        name: The object name whose material to modify.
        properties: Dictionary of property names to MAXScript value expressions.
                    e.g. {"metalness": "1.0", "base_color": "color 200 50 50"}
        sub_material_index: For Multi/Sub-Object materials, 1-based index.
                           0 = top-level material (default).

    Returns:
        Summary of all properties set and any errors encountered.
    """
    safe = _safe_name(name)

    if sub_material_index > 0:
        mat_expr = f"obj.material[{sub_material_index}]"
    else:
        mat_expr = "obj.material"

    # Build the property-setting lines
    set_lines = []
    for prop, val in properties.items():
        safe_prop = _safe_name(prop)
        set_lines.append(
            f'try (mat.{safe_prop} = {val}; append okList "{safe_prop}") '
            f'catch (append errList ("{safe_prop}: " + (getCurrentException())))'
        )
    set_block = "\n            ".join(set_lines)

    maxscript = f"""(
        obj = getNodeByName "{safe}"
        if obj == undefined then (
            "Object not found: {safe}"
        ) else if obj.material == undefined then (
            "No material assigned to {safe}"
        ) else (
            mat = {mat_expr}
            if mat == undefined then (
                "Sub-material index {sub_material_index} not found on {safe}"
            ) else (
                okList = #()
                errList = #()
                {set_block}
                msg = "Set " + (okList.count as string) + " properties on " + mat.name
                if okList.count > 0 do (
                    msg += ": "
                    for i = 1 to okList.count do (
                        if i > 1 do msg += ", "
                        msg += okList[i]
                    )
                )
                if errList.count > 0 do (
                    msg += " | Errors: "
                    for i = 1 to errList.count do (
                        if i > 1 do msg += "; "
                        msg += errList[i]
                    )
                )
                msg
            )
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def create_texture_map(
    map_class: str,
    map_name: str = "",
    params: str = "",
    properties: dict[str, str] | None = None,
    global_var: str = "",
) -> str:
    """Create a texture map and store it as a MAXScript global variable.

    Use this when you need to create texture maps (OSLMap, Bitmaptexture,
    ai_bump2d, tyBitmap, Noise, Checker, etc.) that will be wired into
    material shader slots via set_material_property. The map is stored as
    a MAXScript global so it can be referenced by name in later calls.

    Common patterns:
    - OSL map: map_class="OSLMap", params='', then set OSLPath via properties
    - Bitmap: map_class="Bitmaptexture", properties={"fileName": '"C:/tex.png"'}
    - Arnold bump: map_class="ai_bump2d", properties={"bump_height": "0.02"}
    - Noise: map_class="Noise", properties={"size": "10.0"}

    Args:
        map_class: Texture map class name (e.g. "OSLMap", "Bitmaptexture",
                   "ai_bump2d", "tyBitmap", "Noise", "Checker", "Gradient").
        map_name: Optional display name for the map.
        params: Optional MAXScript creation parameters.
        properties: Optional dict of property names to MAXScript values to set
                    after creation. Useful for OSLMap (set OSLPath first, then
                    set exposed params in a follow-up call).
        global_var: MAXScript global variable name to store the map as.
                    If empty, auto-generated from map_name or map_class.
                    Use this name in set_material_property value field to wire it.

    Returns:
        Confirmation with the global variable name to reference this map.
    """
    safe_map_name = _safe_name(map_name)
    name_param = f' name:"{safe_map_name}"' if map_name else ""

    # Generate global var name if not provided
    if not global_var:
        base = map_name if map_name else map_class
        # Clean to valid MAXScript identifier
        global_var = "".join(c if c.isalnum() or c == "_" else "_" for c in base)
        if global_var[0].isdigit():
            global_var = "m_" + global_var

    # Build property-setting lines
    prop_lines = ""
    if properties:
        lines = []
        for prop, val in properties.items():
            safe_prop = _safe_name(prop)
            lines.append(
                f'try (global {global_var} ; {global_var}.{safe_prop} = {val}; '
                f'append okList "{safe_prop}") '
                f'catch (append errList ("{safe_prop}: " + (getCurrentException())))'
            )
        prop_lines = "\n            ".join(lines)

    maxscript = f"""(
        try (
            global {global_var} = {map_class}{name_param} {params}
            okList = #()
            errList = #()
            {"" if not prop_lines else prop_lines}
            msg = "Created " + (classof {global_var}) as string
            if {global_var}.name != undefined do msg += " \\\"" + {global_var}.name + "\\\""
            msg += " as global '{global_var}'"
            if okList.count > 0 do (
                msg += " | Set: "
                for i = 1 to okList.count do (if i > 1 do msg += ", "; msg += okList[i])
            )
            if errList.count > 0 do (
                msg += " | Errors: "
                for i = 1 to errList.count do (if i > 1 do msg += "; "; msg += errList[i])
            )
            msg
        ) catch (
            "Error: " + (getCurrentException())
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_texture_map_properties(
    global_var: str,
    properties: dict[str, str],
) -> str:
    """Set properties on a texture map stored as a MAXScript global variable.

    Use this after create_texture_map to configure map parameters — especially
    useful for OSLMap where parameters are only exposed AFTER setting OSLPath.
    Two-step OSL workflow: (1) create_texture_map with OSLPath, (2) this tool
    to set the dynamically exposed shader parameters.

    Args:
        global_var: The global variable name from create_texture_map.
        properties: Dict of property names to MAXScript value expressions.
                    e.g. {"IrisSize": "0.4", "PupilColor": "color 1 1 1"}

    Returns:
        Summary of properties set and any errors.
    """
    lines = []
    for prop, val in properties.items():
        safe_prop = _safe_name(prop)
        lines.append(
            f'try ({global_var}.{safe_prop} = {val}; append okList "{safe_prop}") '
            f'catch (append errList ("{safe_prop}: " + (getCurrentException())))'
        )
    set_block = "\n            ".join(lines)

    maxscript = f"""(
        try (
            global {global_var}
            if {global_var} == undefined then (
                "Error: global '{global_var}' not found"
            ) else (
                okList = #()
                errList = #()
                {set_block}
                msg = "Set " + (okList.count as string) + " properties on " + {global_var}.name
                if okList.count > 0 do (
                    msg += ": "
                    for i = 1 to okList.count do (if i > 1 do msg += ", "; msg += okList[i])
                )
                if errList.count > 0 do (
                    msg += " | Errors: "
                    for i = 1 to errList.count do (if i > 1 do msg += "; "; msg += errList[i])
                )
                msg
            )
        ) catch (
            "Error: " + (getCurrentException())
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_sub_material(
    name: str,
    sub_material_index: int,
    material_class: str = "",
    material_name: str = "",
    params: str = "",
    source_index: int = 0,
) -> str:
    """Create or assign a sub-material in a Multi/Sub-Object material slot.

    Use this to populate individual slots of a Multimaterial — e.g. after
    creating a Multimaterial with assign_material, fill each slot with the
    correct shader type. Can create a new material at the slot, or copy
    a reference from another slot (for shared sub-materials like L/R eyes).

    Args:
        name: Object name that has the Multimaterial assigned.
        sub_material_index: 1-based slot index to set (e.g. 1, 2, 3, 4).
        material_class: Material class to create (e.g. "ai_standard_surface",
                        "PhysicalMaterial"). Leave empty if using source_index.
        material_name: Optional name for the new sub-material.
        params: Optional MAXScript creation parameters.
        source_index: If > 0, copies the reference from this slot index instead
                      of creating a new material. Useful for shared sub-materials
                      (e.g. slot 3 = slot 1 for symmetric parts).

    Returns:
        Confirmation of the sub-material assignment.
    """
    safe = _safe_name(name)
    safe_mat_name = _safe_name(material_name)
    name_param = f' name:"{safe_mat_name}"' if material_name else ""

    if source_index > 0:
        # Reference from another slot
        maxscript = f"""(
            obj = getNodeByName "{safe}"
            if obj == undefined then "Object not found: {safe}"
            else if obj.material == undefined then "No material on {safe}"
            else if (classof obj.material) != Multimaterial then "Material is not Multimaterial"
            else (
                try (
                    srcMat = obj.material.materialList[{source_index}]
                    if srcMat == undefined then "Source slot {source_index} is empty"
                    else (
                        obj.material.materialList[{sub_material_index}] = srcMat
                        "Sub[{sub_material_index}] = Sub[{source_index}] (" + srcMat.name + ") — shared reference"
                    )
                ) catch ("Error: " + (getCurrentException()))
            )
        )"""
    else:
        # Create new material at slot
        maxscript = f"""(
            obj = getNodeByName "{safe}"
            if obj == undefined then "Object not found: {safe}"
            else if obj.material == undefined then "No material on {safe}"
            else if (classof obj.material) != Multimaterial then "Material is not Multimaterial"
            else (
                try (
                    newMat = {material_class}{name_param} {params}
                    obj.material.materialList[{sub_material_index}] = newMat
                    "Sub[{sub_material_index}] = " + newMat.name + " (" + (classof newMat) as string + ")"
                ) catch ("Error: " + (getCurrentException()))
            )
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def write_osl_shader(
    shader_name: str,
    osl_code: str,
    global_var: str = "",
    properties: dict[str, str] | None = None,
) -> str:
    """Write an OSL shader to disk and create an OSLMap from it.

    Use this for procedural shading — write OSL code, auto-save to 3ds Max's
    temp/osl_shaders/ directory, create an OSLMap that loads the shader, and
    store it as a MAXScript global variable ready to wire into materials via
    set_material_property.

    The shader file is saved to: {3dsMax temp}/osl_shaders/{shader_name}.osl
    After loading, the OSLMap exposes all shader parameters as properties.
    Use the optional properties dict to set initial parameter values.

    Args:
        shader_name: Name for the shader file and OSLMap (e.g. "ProceduralIris").
                     Used as filename ({shader_name}.osl) and map display name.
        osl_code: Complete OSL shader source code. Must include the shader
                  function with typed parameters and output(s).
        global_var: MAXScript global variable name. If empty, derived from
                    shader_name. Use this name to reference the map later.
        properties: Optional dict of shader parameter values to set after
                    loading (e.g. {"PupilSize": "0.16", "IrisColor": "color 56 97 46"}).

    Returns:
        Confirmation with file path and global variable name.
    """
    if not global_var:
        global_var = "".join(c if c.isalnum() or c == "_" else "_" for c in shader_name)
        if global_var[0].isdigit():
            global_var = "m_" + global_var

    # Escape the OSL code for MAXScript string embedding
    safe_osl = osl_code.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    safe_shader_name = _safe_name(shader_name)

    # Build property-setting lines
    prop_lines = ""
    if properties:
        lines = []
        for prop, val in properties.items():
            safe_prop = _safe_name(prop)
            lines.append(
                f'try (global {global_var} ; {global_var}.{safe_prop} = {val}; '
                f'append okList "{safe_prop}") '
                f'catch (append errList ("{safe_prop}: " + (getCurrentException())))'
            )
        prop_lines = "\n            ".join(lines)

    maxscript = f"""(
        try (
            oslDir = (getDir #temp) + "\\\\osl_shaders\\\\"
            makeDir oslDir
            oslPath = oslDir + "{safe_shader_name}.osl"
            oslContent = "{safe_osl}"
            f = createFile oslPath
            format "%" oslContent to:f
            close f

            global {global_var} = OSLMap name:"{safe_shader_name}"
            {global_var}.OSLPath = oslPath
            {global_var}.OSLAutoUpdate = true

            okList = #()
            errList = #()
            {"" if not prop_lines else prop_lines}

            msg = "OSL shader written to " + oslPath + " | Global: {global_var}"
            if okList.count > 0 do (
                msg += " | Set: "
                for i = 1 to okList.count do (if i > 1 do msg += ", "; msg += okList[i])
            )
            if errList.count > 0 do (
                msg += " | Errors: "
                for i = 1 to errList.count do (if i > 1 do msg += "; "; msg += errList[i])
            )
            msg
        ) catch (
            "Error: " + (getCurrentException())
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")