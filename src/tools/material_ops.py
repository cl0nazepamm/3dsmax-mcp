"""Material creation, assignment, and property manipulation tools for 3ds Max.

Covers the full material workflow: creating materials by class, assigning them
to objects, setting properties, creating texture maps, writing OSL shaders,
and managing Multi/Sub-Object sub-material slots.
Works with all material/map types: Arnold (ai_standard_surface), Physical,
Standard, OSLMap, Bitmaptexture, ai_bump2d, and any MAXScript-creatable class.
"""

from pathlib import Path
from typing import Optional
from ..server import mcp, client


# ---------------------------------------------------------------------------
# Texture-from-folder constants & helpers
# ---------------------------------------------------------------------------

# Supported image extensions for texture scanning
_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr",
    ".tga", ".hdr", ".bmp", ".dds", ".tx",
}

# Channel patterns — priority-ordered, longest match wins within each channel.
# Order within the dict also defines priority (roughness before glossiness).
_DEFAULT_CHANNEL_PATTERNS: dict[str, list[str]] = {
    "diffuse":       ["_basecolor", "_base_color", "_albedo", "_diffuse", "_diff", "_color", "_col"],
    "ao":            ["_ambientocclusion", "_occlusion", "_ao"],
    "roughness":     ["_roughness", "_rough"],
    "glossiness":    ["_glossiness", "_gloss"],
    "metallic":      ["_metallic", "_metalness", "_metal"],
    "normal":        ["_normalgl", "_normaldx", "_normal", "_nrm", "_nor"],
    "bump":          ["_bump", "_bmp", "_height"],
    "displacement":  ["_displacement", "_displace", "_disp"],
    "opacity":       ["_opacity", "_alpha", "_transparency"],
    "emission":      ["_emissive", "_emission", "_emit"],
    "translucency":  ["_translucency", "_translucent", "_transmission"],
    "ior":           ["_ior"],
    "specular":      ["_specular", "_spec", "_reflection", "_refl"],
}

# Color-data maps (sRGB vs Raw / linear)
_COLOR_CHANNELS = {"diffuse", "specular", "emission"}

# Renderer wiring configs: material class, bitmap class, and slot mappings
_RENDERER_CONFIGS: dict[str, dict] = {
    "arnold": {
        "material_class": "ai_standard_surface",
        "bitmap_class": "ai_image",
        "bitmap_path_prop": "filename",
        "color_space_prop": "color_space",
        "color_space_srgb": "sRGB",
        "color_space_raw": "Raw",
        "slots": {
            "diffuse":       "base_color_shader",
            "roughness":     "specular_roughness_shader",
            "glossiness":    "specular_roughness_shader",   # + invert
            "metallic":      "metalness_shader",
            "opacity":       "opacity_shader",
            "emission":      "emission_color_shader",
            "translucency":  "transmission_shader",
            "specular":      "specular_color_shader",
        },
        # Normal/bump/displacement handled specially
    },
    "physical": {
        "material_class": "PhysicalMaterial",
        "bitmap_class": "Bitmaptexture",
        "bitmap_path_prop": "fileName",
        "color_space_prop": None,  # Physical uses gamma on bitmap
        "slots": {
            "diffuse":       "base_color_map",
            "roughness":     "roughness_map",
            "glossiness":    "roughness_map",  # + invert
            "metallic":      "metalness_map",
            "opacity":       "cutout_map",
            "emission":      "emit_color_map",
            "translucency":  "trans_color_map",
            "specular":      "refl_color_map",
        },
    },
    "redshift": {
        "material_class": "RS_Standard_Material",
        "bitmap_class": "Bitmaptexture",
        "bitmap_path_prop": "fileName",
        "color_space_prop": None,
        "slots": {
            "diffuse":       "base_color_map",
            "roughness":     "refl_roughness_map",
            "glossiness":    "refl_roughness_map",  # + invert
            "metallic":      "metalness_map",
            "opacity":       "opacity_color_map",
            "emission":      "emission_color_map",
            "translucency":  "refr_color_map",
            "specular":      "refl_color_map",
        },
    },
}


def _scan_texture_folder(folder: str) -> list[Path]:
    """Return all image files in *folder* (non-recursive)."""
    p = Path(folder)
    if not p.is_dir():
        return []
    return [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS]


def _match_textures_to_channels(
    files: list[Path],
    patterns: dict[str, list[str]],
) -> dict[str, Path]:
    """Match texture files to PBR channels using suffix patterns.

    Longest match wins.  Each file is claimed by at most one channel.
    Roughness takes priority over glossiness (dict ordering).
    """
    matched: dict[str, Path] = {}
    claimed: set[Path] = set()

    for channel, suffixes in patterns.items():
        best_file: Path | None = None
        best_len = 0
        for f in files:
            if f in claimed:
                continue
            stem = f.stem.lower()
            for suffix in suffixes:
                if stem.endswith(suffix) and len(suffix) > best_len:
                    best_file = f
                    best_len = len(suffix)
        if best_file is not None:
            matched[channel] = best_file
            claimed.add(best_file)

    return matched


def _ms_path(p: Path) -> str:
    """Convert a Path to a MAXScript-safe forward-slash string."""
    return str(p).replace("\\", "/")


def _build_arnold_maxscript(
    matched: dict[str, Path],
    material_name: str,
    assign_to: list[str] | None,
) -> str:
    """Generate MAXScript for Arnold (ai_standard_surface) material setup."""
    lines: list[str] = []
    safe_mat = _safe_name(material_name)
    lines.append(f'mat = ai_standard_surface name:"{safe_mat}"')
    lines.append('summary = "Arnold ai_standard_surface"')
    lines.append('channelList = ""')

    for channel, fpath in matched.items():
        var = f"bm_{channel}"
        fp = _ms_path(fpath)
        is_color = channel in _COLOR_CHANNELS
        cs = "sRGB" if is_color else "Raw"

        # Create ai_image bitmap
        lines.append(f'{var} = ai_image name:"{channel}" filename:"{fp}" color_space:"{cs}"')

        if channel == "diffuse":
            # Check if AO exists to composite
            if "ao" in matched:
                ao_fp = _ms_path(matched["ao"])
                lines.append(f'bm_ao = ai_image name:"ao" filename:"{ao_fp}" color_space:"Raw"')
                lines.append('comp = ai_layer_rgba name:"Diffuse_AO"')
                lines.append(f'comp.input1_shader = {var}')
                lines.append('comp.enable2 = true')
                lines.append('comp.input2_shader = bm_ao')
                lines.append('comp.operation2 = 5')  # multiply (layer 2)
                lines.append('mat.base_color_shader = comp')
                lines.append('channelList += "diffuse(+ao), "')
            else:
                lines.append(f'mat.base_color_shader = {var}')
                lines.append('channelList += "diffuse, "')
        elif channel == "ao":
            # Handled inside diffuse block above; skip standalone
            continue
        elif channel == "glossiness":
            lines.append(f'inv = ai_color_correct name:"GlossToRough" input_shader:{var}')
            lines.append('inv.invert = true')
            lines.append('mat.specular_roughness_shader = inv')
            lines.append('channelList += "glossiness(inverted), "')
        elif channel == "normal":
            lines.append(f'nrmMap = ai_normal_map name:"NormalMap" input_shader:{var}')
            if "bump" in matched:
                bump_fp = _ms_path(matched["bump"])
                lines.append(f'bm_bump_h = ai_image name:"bump" filename:"{bump_fp}" color_space:"Raw"')
                lines.append('bmpNode = ai_bump2d name:"Bump"')
                lines.append('bmpNode.bump_map_shader = bm_bump_h')
                lines.append('bmpNode.normal_shader = nrmMap')
                lines.append('mat.normal_shader = bmpNode')
                lines.append('channelList += "normal(+bump), "')
            else:
                lines.append('bmpNode = ai_bump2d name:"NormalBump"')
                lines.append('bmpNode.normal_shader = nrmMap')
                lines.append('mat.normal_shader = bmpNode')
                lines.append('channelList += "normal, "')
        elif channel == "bump":
            # Handled inside normal block if normal exists
            if "normal" not in matched:
                lines.append('bmpNode = ai_bump2d name:"Bump"')
                lines.append(f'bmpNode.bump_map_shader = {var}')
                lines.append('mat.normal_shader = bmpNode')
                lines.append('channelList += "bump, "')
        elif channel == "displacement":
            # Displacement is modifier-based, note it but don't wire
            lines.append('channelList += "displacement(skipped-modifier-based), "')
        elif channel == "ior":
            lines.append('channelList += "ior(skipped-no-map-slot), "')
        else:
            # Standard slot wiring
            slot = _RENDERER_CONFIGS["arnold"]["slots"].get(channel)
            if slot:
                lines.append(f'mat.{slot} = {var}')
                lines.append(f'channelList += "{channel}, "')

    # Assign to objects
    if assign_to:
        names_arr = "#(" + ", ".join(f'"{_safe_name(n)}"' for n in assign_to) + ")"
        lines.append(f'nameList = {names_arr}')
        lines.append('assignCount = 0')
        lines.append('for n in nameList do (obj = getNodeByName n; if obj != undefined then (obj.material = mat; assignCount += 1))')
        lines.append('summary += " | Assigned to " + (assignCount as string) + " object(s)"')

    lines.append('summary += " | Channels: " + channelList')
    lines.append('summary')

    return "(\n    " + "\n    ".join(lines) + "\n)"


def _build_physical_maxscript(
    matched: dict[str, Path],
    material_name: str,
    assign_to: list[str] | None,
) -> str:
    """Generate MAXScript for PhysicalMaterial setup."""
    lines: list[str] = []
    safe_mat = _safe_name(material_name)
    lines.append(f'mat = PhysicalMaterial name:"{safe_mat}"')
    lines.append('summary = "PhysicalMaterial"')
    lines.append('channelList = ""')

    for channel, fpath in matched.items():
        var = f"bm_{channel}"
        fp = _ms_path(fpath)

        # Create Bitmaptexture
        lines.append(f'{var} = Bitmaptexture name:"{channel}" fileName:"{fp}"')

        if channel == "diffuse":
            if "ao" in matched:
                ao_fp = _ms_path(matched["ao"])
                lines.append(f'bm_ao = Bitmaptexture name:"ao" fileName:"{ao_fp}"')
                lines.append('comp = CompositeTexturemap name:"Diffuse_AO"')
                lines.append(f'comp.mapList[1] = {var}')
                lines.append('comp.mapList[2] = bm_ao')
                lines.append('comp.blendMode[2] = 5')  # multiply
                lines.append('mat.base_color_map = comp')
                lines.append('channelList += "diffuse(+ao), "')
            else:
                lines.append('mat.base_color_map = ' + var)
                lines.append('channelList += "diffuse, "')
        elif channel == "ao":
            continue
        elif channel == "glossiness":
            lines.append(f'inv = Output name:"GlossToRough"')
            lines.append(f'inv.map1 = {var}')
            lines.append('inv.output.invert = true')
            lines.append('mat.roughness_map = inv')
            lines.append('channelList += "glossiness(inverted), "')
        elif channel == "normal":
            lines.append(f'nrmBump = Normal_Bump name:"NormalBump"')
            lines.append(f'nrmBump.normal_map = {var}')
            if "bump" in matched:
                bump_fp = _ms_path(matched["bump"])
                lines.append(f'bm_bump_h = Bitmaptexture name:"bump" fileName:"{bump_fp}"')
                lines.append('nrmBump.bump_map = bm_bump_h')
                lines.append('mat.bump_map = nrmBump')
                lines.append('channelList += "normal(+bump), "')
            else:
                lines.append('mat.bump_map = nrmBump')
                lines.append('channelList += "normal, "')
        elif channel == "bump":
            if "normal" not in matched:
                lines.append(f'nrmBump = Normal_Bump name:"BumpOnly"')
                lines.append(f'nrmBump.bump_map = {var}')
                lines.append('mat.bump_map = nrmBump')
                lines.append('channelList += "bump, "')
        elif channel == "displacement":
            lines.append(f'mat.displacement_map = {var}')
            lines.append('channelList += "displacement, "')
        elif channel == "ior":
            lines.append('channelList += "ior(skipped-no-map-slot), "')
        else:
            slot = _RENDERER_CONFIGS["physical"]["slots"].get(channel)
            if slot:
                lines.append(f'mat.{slot} = {var}')
                lines.append(f'channelList += "{channel}, "')

    if assign_to:
        names_arr = "#(" + ", ".join(f'"{_safe_name(n)}"' for n in assign_to) + ")"
        lines.append(f'nameList = {names_arr}')
        lines.append('assignCount = 0')
        lines.append('for n in nameList do (obj = getNodeByName n; if obj != undefined then (obj.material = mat; assignCount += 1))')
        lines.append('summary += " | Assigned to " + (assignCount as string) + " object(s)"')

    lines.append('summary += " | Channels: " + channelList')
    lines.append('summary')

    return "(\n    " + "\n    ".join(lines) + "\n)"


def _build_redshift_maxscript(
    matched: dict[str, Path],
    material_name: str,
    assign_to: list[str] | None,
) -> str:
    """Generate MAXScript for Redshift (RS_Standard_Material) setup."""
    lines: list[str] = []
    safe_mat = _safe_name(material_name)
    lines.append(f'mat = RS_Standard_Material name:"{safe_mat}"')
    lines.append('summary = "Redshift RS_Standard_Material"')
    lines.append('channelList = ""')

    for channel, fpath in matched.items():
        var = f"bm_{channel}"
        fp = _ms_path(fpath)

        lines.append(f'{var} = Bitmaptexture name:"{channel}" fileName:"{fp}"')

        if channel == "diffuse":
            if "ao" in matched:
                ao_fp = _ms_path(matched["ao"])
                lines.append(f'bm_ao = Bitmaptexture name:"ao" fileName:"{ao_fp}"')
                lines.append('comp = CompositeTexturemap name:"Diffuse_AO"')
                lines.append(f'comp.mapList[1] = {var}')
                lines.append('comp.mapList[2] = bm_ao')
                lines.append('comp.blendMode[2] = 5')
                lines.append('mat.base_color_map = comp')
                lines.append('channelList += "diffuse(+ao), "')
            else:
                lines.append(f'mat.base_color_map = {var}')
                lines.append('channelList += "diffuse, "')
        elif channel == "ao":
            continue
        elif channel == "glossiness":
            lines.append(f'inv = Output name:"GlossToRough"')
            lines.append(f'inv.map1 = {var}')
            lines.append('inv.output.invert = true')
            lines.append('mat.refl_roughness_map = inv')
            lines.append('channelList += "glossiness(inverted), "')
        elif channel == "normal":
            lines.append('rsBump = RS_BumpMap name:"NormalBump"')
            lines.append(f'rsBump.input_map = {var}')
            lines.append('rsBump.inputType = 1')  # tangent-space normal
            if "bump" in matched:
                bump_fp = _ms_path(matched["bump"])
                lines.append(f'bm_bump_h = Bitmaptexture name:"bump" fileName:"{bump_fp}"')
                # Redshift: chain bump into the bump map input
                lines.append('rsBumpH = RS_BumpMap name:"BumpHeight"')
                lines.append('rsBumpH.input_map = bm_bump_h')
                lines.append('rsBumpH.inputType = 0')  # bump
                lines.append('-- Redshift: wire normal to bump_input, height bump separate')
                lines.append('mat.bump_input = rsBump')
                lines.append('channelList += "normal(+bump partially), "')
            else:
                lines.append('mat.bump_input = rsBump')
                lines.append('channelList += "normal, "')
        elif channel == "bump":
            if "normal" not in matched:
                lines.append('rsBump = RS_BumpMap name:"Bump"')
                lines.append(f'rsBump.input_map = {var}')
                lines.append('rsBump.inputType = 0')
                lines.append('mat.bump_input = rsBump')
                lines.append('channelList += "bump, "')
        elif channel == "displacement":
            lines.append(f'mat.displacement_input = {var}')
            lines.append('channelList += "displacement, "')
        elif channel == "ior":
            lines.append('channelList += "ior(skipped-no-map-slot), "')
        else:
            slot = _RENDERER_CONFIGS["redshift"]["slots"].get(channel)
            if slot:
                lines.append(f'mat.{slot} = {var}')
                lines.append(f'channelList += "{channel}, "')

    if assign_to:
        names_arr = "#(" + ", ".join(f'"{_safe_name(n)}"' for n in assign_to) + ")"
        lines.append(f'nameList = {names_arr}')
        lines.append('assignCount = 0')
        lines.append('for n in nameList do (obj = getNodeByName n; if obj != undefined then (obj.material = mat; assignCount += 1))')
        lines.append('summary += " | Assigned to " + (assignCount as string) + " object(s)"')

    lines.append('summary += " | Channels: " + channelList')
    lines.append('summary')

    return "(\n    " + "\n    ".join(lines) + "\n)"


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


@mcp.tool()
def create_material_from_textures(
    texture_folder: str,
    material_class: str = "",
    material_name: str = "",
    assign_to: list[str] | None = None,
    custom_patterns: dict[str, list[str]] | None = None,
) -> str:
    """Create a fully-wired PBR material from a folder of texture maps.

    Point at a folder containing named texture files (e.g. *_basecolor.png,
    *_roughness.png, *_normal.png) and this tool will auto-detect channels,
    create the appropriate material for the current renderer, wire all maps
    with correct color spaces, and optionally assign to objects.

    Supports Arnold (ai_standard_surface), Physical Material, and Redshift.
    Auto-detects the active renderer if material_class is not specified.
    Handles compositing (diffuse+AO), inversion (gloss->rough), and
    intermediate nodes (normal maps, bump2d).

    Args:
        texture_folder: Path to the folder containing texture files.
        material_class: Force a specific material class ("ai_standard_surface",
                        "PhysicalMaterial", "RS_Standard_Material").
                        If empty, auto-detects from the current renderer.
        material_name: Name for the created material. If empty, derived from
                       the folder name.
        assign_to: Optional list of object names to assign the material to.
        custom_patterns: Optional dict overriding channel-to-suffix matching.
                         Keys are channel names (e.g. "diffuse", "roughness"),
                         values are lists of suffixes (e.g. ["_basecolor", "_albedo"]).

    Returns:
        Summary of created material, matched channels, and assignment status.
    """
    # -- Step 1: Scan folder (Python-side) --
    files = _scan_texture_folder(texture_folder)
    if not files:
        return f"No image files found in: {texture_folder}"

    # -- Step 2: Match textures to channels (Python-side) --
    patterns = dict(_DEFAULT_CHANNEL_PATTERNS)
    if custom_patterns:
        patterns.update(custom_patterns)

    matched = _match_textures_to_channels(files, patterns)
    if not matched:
        suffixes = [f.stem for f in files[:10]]
        return f"No textures matched any channel pattern. File stems: {suffixes}"

    # -- Step 3: Determine renderer / material class --
    renderer = ""
    if material_class:
        class_lower = material_class.lower()
        if "ai_standard" in class_lower or "arnold" in class_lower:
            renderer = "arnold"
        elif "physical" in class_lower:
            renderer = "physical"
        elif "rs_standard" in class_lower or "redshift" in class_lower:
            renderer = "redshift"
        else:
            return (f"Unsupported material_class: {material_class}. "
                    "Use ai_standard_surface, PhysicalMaterial, or RS_Standard_Material.")
    else:
        # Auto-detect from active renderer
        detect_ms = '(classof renderers.current) as string'
        resp = client.send_command(detect_ms)
        renderer_class = resp.get("result", "").strip().lower()
        if "arnold" in renderer_class:
            renderer = "arnold"
        elif "redshift" in renderer_class:
            renderer = "redshift"
        else:
            renderer = "physical"  # safe fallback

    # -- Step 4: Derive material name --
    if not material_name:
        material_name = Path(texture_folder).name

    # -- Step 5: Build MAXScript --
    if renderer == "arnold":
        maxscript = _build_arnold_maxscript(matched, material_name, assign_to)
    elif renderer == "redshift":
        maxscript = _build_redshift_maxscript(matched, material_name, assign_to)
    else:
        maxscript = _build_physical_maxscript(matched, material_name, assign_to)

    # Wrap in try/catch
    maxscript = f"""(
    try (
        {maxscript}
    ) catch (
        "Error: " + (getCurrentException())
    )
)"""

    # -- Step 6: Send to Max --
    response = client.send_command(maxscript)
    return response.get("result", "")