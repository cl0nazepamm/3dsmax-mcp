"""Shared PBR material builder used by palette_laydown and smart_import.

Emits MAXScript text that creates one renderer-specific PBR material per texture
group (output of material_detection._group_texture_files_for_pbr). The module
exposes three composable primitives:

  * pbr_helpers_preamble_lines() — top-level fn declarations
  * pbr_renderer_setup_lines() — renderer-specific shared setup (OSL paths, etc.)
  * pbr_per_group_lines() — the inner body that builds ONE material into mat_var

Callers wrap pbr_per_group_lines output in try/catch and append their own
finalize logic (Material Editor slot placement, scene-node assignment, …).
"""

from pathlib import Path

from ..helpers.maxscript import safe_string

from .material_detection import _COLOR_CHANNELS
from .material_shell import (
    UBER_OUT_B as _UBER_OUT_B,
    UBER_OUT_G as _UBER_OUT_G,
    UBER_OUT_R as _UBER_OUT_R,
)


# Octane Channel_picker.colorChannel values for ORM split (R/G/B)
_OCTANE_CH_R = 0
_OCTANE_CH_G = 1
_OCTANE_CH_B = 2


# Renderer wiring configs and slot mappings
_PBR_SLOT_CANDIDATES: dict[str, dict[str, list[str]]] = {
    "openpbr": {
        "diffuse":      ["base_color_map", "baseColor_map", "basecolor_map", "base_map", "diffuse_map"],
        "ao":           ["base_color_map", "baseColor_map", "basecolor_map", "base_map", "diffuse_map"],
        "roughness":    ["roughness_map", "specular_roughness_map", "base_roughness_map"],
        "glossiness":   ["roughness_map", "specular_roughness_map", "base_roughness_map"],
        "metallic":     ["metalness_map", "metallic_map", "base_metalness_map"],
        "normal":       ["bump_map", "normal_map"],
        "bump":         ["bump_map", "normal_map"],
        "displacement": ["displacement_map"],
        "opacity":      ["opacity_map", "cutout_map", "transparency_map"],
        "emission":     ["emission_color_map", "emit_color_map", "emission_map"],
        "translucency": ["transmission_color_map", "trans_color_map", "transmission_map"],
        "specular":     ["specular_color_map", "refl_color_map"],
    },
    "physical": {
        "diffuse":      ["base_color_map"],
        "ao":           ["base_color_map"],
        "roughness":    ["roughness_map"],
        "glossiness":   ["roughness_map"],
        "metallic":     ["metalness_map"],
        "normal":       ["bump_map"],
        "bump":         ["bump_map"],
        "displacement": ["displacement_map"],
        "opacity":      ["cutout_map"],
        "emission":     ["emit_color_map", "emission_map"],
        "translucency": ["trans_color_map", "transparency_map"],
        "specular":     ["refl_color_map"],
    },
    "materialx": {
        "diffuse":      ["base_color_map", "baseColor_map", "basecolor_map", "base_map", "diffuse_map"],
        "ao":           ["base_color_map", "baseColor_map", "basecolor_map", "base_map", "diffuse_map"],
        "roughness":    ["roughness_map", "specular_roughness_map", "base_roughness_map"],
        "glossiness":   ["roughness_map", "specular_roughness_map", "base_roughness_map"],
        "metallic":     ["metalness_map", "metallic_map", "base_metalness_map"],
        "normal":       ["bump_map", "normal_map"],
        "bump":         ["bump_map", "normal_map"],
        "displacement": ["displacement_map"],
        "opacity":      ["opacity_map", "cutout_map", "transparency_map"],
        "emission":     ["emission_color_map", "emit_color_map", "emission_map"],
        "translucency": ["transmission_color_map", "trans_color_map", "transmission_map"],
        "specular":     ["specular_color_map", "refl_color_map"],
    },
    "arnold": {
        "diffuse":      ["base_color_shader"],
        "ao":           ["base_color_shader"],
        "roughness":    ["specular_roughness_shader"],
        "glossiness":   ["specular_roughness_shader"],
        "metallic":     ["metalness_shader"],
        "normal":       ["normal_shader"],
        "bump":         ["normal_shader"],
        "opacity":      ["opacity_shader"],
        "emission":     ["emission_color_shader"],
        "translucency": ["transmission_shader"],
        "specular":     ["specular_color_shader"],
    },
    "redshift": {
        "diffuse":      ["base_color_map"],
        "ao":           ["base_color_map"],
        "roughness":    ["refl_roughness_map"],
        "glossiness":   ["refl_roughness_map"],
        "metallic":     ["metalness_map"],
        "normal":       ["bump_input"],
        "bump":         ["bump_input"],
        "displacement": ["displacement_input"],
        "opacity":      ["opacity_color_map"],
        "emission":     ["emission_color_map"],
        "translucency": ["refr_color_map"],
        "specular":     ["refl_color_map"],
    },
    "vray": {
        "diffuse":      ["texmap_diffuse", "diffuse_texmap", "diffuseMap"],
        "ao":           ["texmap_diffuse", "diffuse_texmap", "diffuseMap"],
        "roughness":    ["texmap_roughness", "roughness_texmap", "texmap_reflectionRoughness", "reflectionRoughness_texmap", "reflection_roughness_texmap"],
        "glossiness":   ["texmap_reflectionGlossiness", "reflectionGlossiness_texmap", "reflection_glossiness_texmap"],
        "metallic":     ["texmap_metalness", "metalness_texmap", "texmap_metallic", "metallic_texmap"],
        "normal":       ["texmap_bump", "bump_texmap", "bumpMap"],
        "bump":         ["texmap_bump", "bump_texmap", "bumpMap"],
        "displacement": ["texmap_displacement", "displacement_texmap", "displacementMap"],
        "opacity":      ["texmap_opacity", "opacity_texmap", "opacityMap"],
        "emission":     ["texmap_self_illumination", "selfIllumination_texmap", "self_illumination_texmap"],
        "translucency": ["texmap_translucent", "translucent_texmap", "texmap_translucency"],
        "specular":     ["texmap_reflection", "reflection_texmap", "reflectionMap"],
    },
    "octane_standard": {
        "diffuse":      ["baseColor_tex", "albedo_tex"],
        "ao":           ["baseColor_tex", "albedo_tex"],
        "roughness":    ["roughness_tex"],
        "glossiness":   ["roughness_tex"],
        "metallic":     ["metallic_tex"],
        "normal":       ["normal_tex"],
        "bump":         ["bump_tex"],
        "displacement": ["displacement"],
        "opacity":      ["opacity_tex"],
        "emission":     ["emissionColor_tex", "emission"],
        "translucency": ["transmissionColor_tex", "transmission_tex"],
        "specular":     ["specularColor_tex", "specular_tex"],
    },
    "octane_pbr": {
        "diffuse":      ["baseColor_tex"],
        "ao":           ["baseColor_tex"],
        "roughness":    ["roughness_tex"],
        "glossiness":   ["roughness_tex"],
        "metallic":     ["metallic_tex"],
        "normal":       ["normal_tex"],
        "bump":         ["bump_tex"],
        "displacement": ["displacement"],
        "opacity":      ["opacity_tex"],
        "emission":     ["emissionColor_tex", "emission"],
        "translucency": ["transmissionColor_tex"],
        "specular":     ["specularColor_tex", "specular_tex"],
    },
    "octane_universal": {
        "diffuse":      ["albedo_tex"],
        "ao":           ["albedo_tex"],
        "roughness":    ["roughness_tex"],
        "glossiness":   ["roughness_tex"],
        "metallic":     ["metallic_tex"],
        "normal":       ["normal_tex"],
        "bump":         ["bump_tex"],
        "displacement": ["displacement"],
        "opacity":      ["opacity_tex"],
        "emission":     ["emission"],
        "translucency": ["transmission_tex"],
        "specular":     ["specular_tex"],
    },
}


RENDERER_LABELS: dict[str, str] = {
    "openpbr": "OpenPBR-first",
    "materialx": "OpenPBR + MaterialX OSL",
    "physical": "PhysicalMaterial",
    "arnold": "Arnold ai_standard_surface",
    "redshift": "Redshift RS_Standard_Material",
    "vray": "V-Ray VRayMtl",
    "octane_standard": "Octane Std Surface (Std_Surface_Mtl)",
    "octane_pbr": "Octane Open PBR Surface (Open_PBR_Surf__Mtl)",
    "octane_universal": "Octane Universal (Universal_material)",
}


def _ms_path(p: Path) -> str:
    """Convert a Path to a MAXScript-safe forward-slash string."""
    return str(p).replace("\\", "/")


def _ms_name_array(values: list[str]) -> str:
    return "#(" + ", ".join(f'"{safe_string(v)}"' for v in values) + ")"


def groups_need_uberbitmap_osl(groups: list[dict], renderer: str) -> bool:
    """True when the legacy UberBitmap2 OSL helper is needed for ORM splits."""
    if renderer == "materialx" or renderer.startswith("octane"):
        return False
    return any("orm" in group["channels"] for group in groups)


def pbr_helpers_preamble_lines() -> list[str]:
    """MAXScript fn declarations used by every PBR material build."""
    return [
        "fn mcp_setFirstMap target propNames tex = (",
        "    for propName in propNames do (",
        "        try (setProperty target (propName as name) tex; return propName) catch ()",
        "    )",
        "    undefined",
        ")",
        "fn mcp_setFirstValue target propNames value = (",
        "    for propName in propNames do (",
        "        try (setProperty target (propName as name) value; return propName) catch ()",
        "    )",
        "    undefined",
        ")",
        "fn mcp_enableMapSlot target slotName = (",
        "    if slotName != undefined do (",
        '        try (setProperty target ((slotName + "_on") as name) true) catch ()',
        '        try (setProperty target ((slotName + "_enable") as name) true) catch ()',
        '        try (',
        '            local s = slotName as string',
        '            if matchPattern s pattern:"*_tex" do (',
        '                local prefix = substring s 1 (s.count - 4)',
        '                setProperty target ((prefix + "_input_type") as name) 2',
        '            )',
        '        ) catch ()',
        "    )",
        ")",
        "fn mcp_createOpenPbrPreferred matName = (",
        "    local m = undefined",
        "    try (m = OpenPBRMaterial name:matName) catch ()",
        "    if m == undefined do try (m = OpenPBR_Material name:matName) catch ()",
        "    if m == undefined do try (m = OpenPBR_Mtl name:matName) catch ()",
        "    if m == undefined do try (m = PhysicalMaterial name:matName) catch ()",
        '    if m == undefined do throw "OpenPBRMaterial/OpenPBR_Material/OpenPBR_Mtl/PhysicalMaterial are unavailable"',
        "    m",
        ")",
    ]


def pbr_renderer_setup_lines(
    renderer: str,
    *,
    needs_uberbitmap_osl: bool,
) -> list[str]:
    """Renderer-specific top-level setup (OSL paths, MaterialX OSL helpers, …)."""
    lines: list[str] = []
    if renderer == "materialx":
        lines.extend([
            "fn mcp_materialXOslRoot = (",
            "    local roots = #(",
            '        "C:\\\\ProgramData\\\\Autodesk\\\\ApplicationPlugins\\\\USD for 3ds Max 2027\\\\Contents\\\\MaterialX_plugin\\\\Contents\\\\OSL\\\\MaterialX",',
            '        "C:\\\\ProgramData\\\\Autodesk\\\\ApplicationPlugins\\\\USD for 3ds Max 2026\\\\Contents\\\\MaterialX_plugin\\\\Contents\\\\OSL\\\\MaterialX",',
            '        "C:\\\\ProgramData\\\\Autodesk\\\\ApplicationPlugins\\\\USD for 3ds Max 2025\\\\Contents\\\\MaterialX_plugin\\\\Contents\\\\OSL\\\\MaterialX"',
            "    )",
            '    for root in roots where doesFileExist (root + "\\\\tiledimage_color3.osl") do return root',
            '    throw "USD MaterialX OSL nodes are unavailable. Expected tiledimage_color3.osl under ProgramData Autodesk ApplicationPlugins."',
            ")",
            "fn mcp_materialXOslPath fileName = (",
            "    local path = (mcp_materialXOslRoot()) + \"\\\\\" + fileName",
            '    if not (doesFileExist path) do throw ("MaterialX OSL file is unavailable: " + path)',
            "    path",
            ")",
            "fn mcp_makeMaterialXOslMap fileName nodeName = (",
            "    local m = OSLMap()",
            "    m.name = nodeName",
            "    m.OSLPath = mcp_materialXOslPath fileName",
            "    m.OSLAutoUpdate = true",
            "    m",
            ")",
        ])
    if needs_uberbitmap_osl:
        lines.append('local oslPath = (getDir #maxRoot) + "OSL\\\\UberBitmap2.osl"')
    return lines


def pbr_per_group_lines(
    group: dict,
    *,
    idx: int,
    mat_var: str,
    mat_name: str,
    renderer: str,
    include_displacement: bool,
) -> list[str]:
    """Inner-body lines (4-space pre-indented) that build ONE PBR material into ``mat_var``.

    The caller wraps these lines in ``try (...) catch (...)`` and appends finalize
    logic. The block defines locals ``channelList`` and ``skippedList`` for the
    caller to embed in summary messages.
    """
    is_octane = renderer.startswith("octane")
    channels: dict[str, Path] = group["channels"]
    lines: list[str] = []

    if renderer in {"openpbr", "materialx"}:
        lines.append(f'    local {mat_var} = mcp_createOpenPbrPreferred "{mat_name}"')
    elif renderer == "physical":
        lines.append(f'    local {mat_var} = PhysicalMaterial name:"{mat_name}"')
    elif renderer == "arnold":
        lines.append(f'    local {mat_var} = ai_standard_surface name:"{mat_name}"')
    elif renderer == "redshift":
        lines.append(f'    local {mat_var} = RS_Standard_Material name:"{mat_name}"')
    elif renderer == "octane_standard":
        lines.append(f'    local {mat_var} = Std_Surface_Mtl name:"{mat_name}"')
    elif renderer == "octane_pbr":
        lines.append(f'    local {mat_var} = Open_PBR_Surf__Mtl name:"{mat_name}"')
    elif renderer == "octane_universal":
        lines.append(f'    local {mat_var} = Universal_material name:"{mat_name}"')
    else:
        lines.append(f'    local {mat_var} = VRayMtl name:"{mat_name}"')
        lines.append(f"    try ({mat_var}.brdf_useRoughness = true) catch ()")

    lines.extend([
        "    local channelList = \"\"",
        "    local skippedList = \"\"",
        f'    local specDefault = mcp_setFirstValue {mat_var} #("specular_color", "specularColor", "specular", "refl_color", "reflection") (color 255 255 255)',
    ])

    map_vars: dict[str, str] = {}

    def add_wire(slot_var: str, channel_label: str, tex_var: str, candidates: list[str]) -> None:
        lines.extend([
            f"    local {slot_var} = mcp_setFirstMap {mat_var} {_ms_name_array(candidates)} {tex_var}",
            f"    mcp_enableMapSlot {mat_var} {slot_var}",
            f'    if {slot_var} != undefined then channelList += "{channel_label}->" + {slot_var} + ", " else skippedList += "{channel_label}, "',
        ])

    def add_bitmap(var: str, channel: str, fpath: Path) -> None:
        path_literal = safe_string(_ms_path(fpath))
        tex_name = safe_string(fpath.stem)
        if renderer == "materialx":
            is_color = channel in _COLOR_CHANNELS
            file_name = "tiledimage_vector3.osl" if channel == "normal" else (
                "tiledimage_color3.osl" if is_color else "tiledimage_float.osl"
            )
            colorspace = "srgb_texture" if is_color else ""
            lines.extend([
                f'    local {var} = mcp_makeMaterialXOslMap "{file_name}" "{tex_name}"',
                f'    {var}.file = @"{path_literal}"',
                f'    {var}.file_colorspace = "{colorspace}"',
            ])
            if channel == "normal":
                lines.append(f"    {var}.default1 = [0.5, 0.5, 1.0]")
        elif renderer == "arnold":
            color_space = "sRGB" if channel in _COLOR_CHANNELS else "Raw"
            lines.append(f'    local {var} = ai_image name:"{tex_name}" filename:@"{path_literal}" color_space:"{color_space}"')
        elif is_octane:
            # Leave colorSpace at Octane's default (_OctaneBuildIn_LINEAR_sRGB).
            # Other strings like "sRGB" or "_OctaneBuildIn_LINEAR" fall through to
            # OCIO lookup and error when no OCIO config is loaded. The material's
            # slot semantics (basecolor vs roughness vs metallic) drive how Octane
            # interprets the texture data, not this string.
            lines.append(f'    local {var} = Image_MTX()')
            lines.append(f'    {var}.name = "{tex_name}"')
            lines.append(f'    {var}.filename = @"{path_literal}"')
        else:
            lines.append(f'    local {var} = Bitmaptexture name:"{tex_name}" filename:@"{path_literal}"')

    def add_orm_split(prefix: str, fpath: Path) -> dict[str, str]:
        path_literal = safe_string(_ms_path(fpath))
        tex_name = safe_string(fpath.stem)
        uber = f"{prefix}_orm"
        out_r = f"{prefix}_orm_r"
        out_g = f"{prefix}_orm_g"
        out_b = f"{prefix}_orm_b"
        if renderer == "materialx":
            lines.extend([
                f'    local {uber} = mcp_makeMaterialXOslMap "tiledimage_color3.osl" "{tex_name}"',
                f'    {uber}.file = @"{path_literal}"',
                f'    {uber}.file_colorspace = ""',
                f'    local {out_r} = mcp_makeMaterialXOslMap "extract_color3.osl" "{tex_name}_AO_R"',
                f"    {out_r}.In_map = {uber}",
                f"    {out_r}.index = 0",
                f'    local {out_g} = mcp_makeMaterialXOslMap "extract_color3.osl" "{tex_name}_Roughness_G"',
                f"    {out_g}.In_map = {uber}",
                f"    {out_g}.index = 1",
                f'    local {out_b} = mcp_makeMaterialXOslMap "extract_color3.osl" "{tex_name}_Metallic_B"',
                f"    {out_b}.In_map = {uber}",
                f"    {out_b}.index = 2",
            ])
            return {"ao": out_r, "roughness": out_g, "metallic": out_b}
        if is_octane:
            lines.extend([
                f'    local {uber} = Image_MTX()',
                f'    {uber}.name = "{tex_name}"',
                f'    {uber}.filename = @"{path_literal}"',
                f'    local {out_r} = Channel_picker()',
                f'    {out_r}.name = "{tex_name}_AO_R"',
                f'    {out_r}.texture_tex = {uber}',
                f'    {out_r}.texture_input_type = 2',
                f'    {out_r}.colorChannel = {_OCTANE_CH_R}',
                f'    local {out_g} = Channel_picker()',
                f'    {out_g}.name = "{tex_name}_Rough_G"',
                f'    {out_g}.texture_tex = {uber}',
                f'    {out_g}.texture_input_type = 2',
                f'    {out_g}.colorChannel = {_OCTANE_CH_G}',
                f'    local {out_b} = Channel_picker()',
                f'    {out_b}.name = "{tex_name}_Metal_B"',
                f'    {out_b}.texture_tex = {uber}',
                f'    {out_b}.texture_input_type = 2',
                f'    {out_b}.colorChannel = {_OCTANE_CH_B}',
            ])
            return {"ao": out_r, "roughness": out_g, "metallic": out_b}
        lines.extend([
            f"    local {uber} = OSLMap()",
            f'    {uber}.name = "{tex_name}"',
            f"    {uber}.OSLPath = oslPath",
            f"    {uber}.OSLAutoUpdate = true",
            f'    {uber}.filename = @"{path_literal}"',
            f"    local {out_r} = MultiOutputChannelTexmapToTexmap()",
            f"    {out_r}.sourceMap = {uber}",
            f"    {out_r}.outputChannelIndex = {_UBER_OUT_R}",
            f"    local {out_g} = MultiOutputChannelTexmapToTexmap()",
            f"    {out_g}.sourceMap = {uber}",
            f"    {out_g}.outputChannelIndex = {_UBER_OUT_G}",
            f"    local {out_b} = MultiOutputChannelTexmapToTexmap()",
            f"    {out_b}.sourceMap = {uber}",
            f"    {out_b}.outputChannelIndex = {_UBER_OUT_B}",
        ])
        return {"ao": out_r, "roughness": out_g, "metallic": out_b}

    for channel, fpath in channels.items():
        if channel == "orm":
            for split_channel, split_var in add_orm_split(f"g{idx}", fpath).items():
                map_vars.setdefault(split_channel, split_var)
        else:
            var = f"g{idx}_{channel}"
            add_bitmap(var, channel, fpath)
            map_vars[channel] = var

    ao_var = map_vars.get("ao")
    slots = _PBR_SLOT_CANDIDATES[renderer]

    if "diffuse" in map_vars:
        diffuse_var = map_vars["diffuse"]
        if ao_var:
            if renderer == "materialx":
                comp_var = f"g{idx}_diffuse_ao"
                lines.extend([
                    f'    local {comp_var} = mcp_makeMaterialXOslMap "multiply_color3FA.osl" "Diffuse_AO"',
                    f"    {comp_var}.in1_map = {diffuse_var}",
                    f"    {comp_var}.in2_map = {ao_var}",
                ])
            elif renderer == "arnold":
                comp_var = f"g{idx}_diffuse_ao"
                lines.extend([
                    f'    local {comp_var} = ai_multiply name:"Diffuse_AO"',
                    f"    {comp_var}.input1_shader = {diffuse_var}",
                    f"    {comp_var}.input2_shader = {ao_var}",
                ])
            elif is_octane:
                # Multiply_MTX collapses color to greyscale regardless of
                # textureValueType. Multiply_texture preserves RGB.
                comp_var = f"g{idx}_diffuse_ao"
                lines.extend([
                    f'    local {comp_var} = Multiply_texture()',
                    f'    {comp_var}.name = "Diffuse_AO"',
                    f"    {comp_var}.texture1_tex = {diffuse_var}",
                    f"    {comp_var}.texture1_input_type = 2",
                    f"    {comp_var}.texture2_tex = {ao_var}",
                    f"    {comp_var}.texture2_input_type = 2",
                ])
            else:
                comp_var = f"g{idx}_diffuse_ao"
                lines.extend([
                    f"    local {comp_var} = CompositeTexturemap()",
                    f'    {comp_var}.name = "Diffuse_AO"',
                    f"    {comp_var}.mapList[1] = {diffuse_var}",
                    f"    {comp_var}.mapList[2] = {ao_var}",
                    f"    {comp_var}.blendMode[2] = 5",
                ])
            add_wire(f"slot_{idx}_diffuse", "diffuse(+ao)", comp_var, slots["diffuse"])
        else:
            add_wire(f"slot_{idx}_diffuse", "diffuse", diffuse_var, slots["diffuse"])
    elif "ao" in map_vars:
        lines.append('    skippedList += "ao(no diffuse), "')

    if "roughness" in map_vars:
        add_wire(f"slot_{idx}_roughness", "roughness", map_vars["roughness"], slots["roughness"])
    elif "glossiness" in map_vars:
        inv_var = f"g{idx}_gloss_to_rough"
        if renderer == "materialx":
            lines.extend([
                f'    local {inv_var} = mcp_makeMaterialXOslMap "invert_float.osl" "GlossToRough"',
                f"    {inv_var}.In_map = {map_vars['glossiness']}",
                f"    {inv_var}.amount = 1.0",
            ])
        elif is_octane:
            lines.extend([
                f'    local {inv_var} = Invert_MTX()',
                f'    {inv_var}.name = "GlossToRough"',
                f"    {inv_var}.input_tex = {map_vars['glossiness']}",
                f"    {inv_var}.input_input_type = 2",
            ])
        else:
            lines.extend([
                f'    local {inv_var} = Output name:"GlossToRough"',
                f"    {inv_var}.map1 = {map_vars['glossiness']}",
                f"    {inv_var}.output.invert = true",
            ])
        add_wire(f"slot_{idx}_glossiness", "glossiness(inverted)", inv_var, slots["glossiness"])
    elif "roughness" in map_vars:
        add_wire(f"slot_{idx}_roughness_orm", "roughness(orm)", map_vars["roughness"], slots["roughness"])

    if "metallic" in map_vars:
        add_wire(f"slot_{idx}_metallic", "metallic", map_vars["metallic"], slots["metallic"])

    if "normal" in map_vars:
        if renderer == "materialx":
            final_normal = f"g{idx}_normal_node"
            lines.extend([
                f'    local {final_normal} = mcp_makeMaterialXOslMap "normalmap.osl" "NormalMap"',
                f"    {final_normal}.In_map = {map_vars['normal']}",
                f'    {final_normal}.space = "tangent"',
                f"    {final_normal}.scale = 1.0",
            ])
        elif renderer == "arnold":
            normal_node = f"g{idx}_normal_node"
            final_normal = normal_node
            lines.extend([
                f'    local {normal_node} = ai_normal_map name:"NormalMap" input_shader:{map_vars["normal"]}',
            ])
            if "bump" in map_vars:
                bump_node = f"g{idx}_normal_bump_node"
                lines.extend([
                    f'    local {bump_node} = ai_bump2d name:"NormalBump"',
                    f"    {bump_node}.bump_map_shader = {map_vars['bump']}",
                    f"    {bump_node}.normal_shader = {normal_node}",
                ])
                final_normal = bump_node
        elif renderer == "redshift":
            final_normal = f"g{idx}_normal_node"
            lines.extend([
                f'    local {final_normal} = RS_BumpMap name:"NormalMap"',
                f"    {final_normal}.input_map = {map_vars['normal']}",
                f"    {final_normal}.inputType = 1",
            ])
        elif renderer == "vray":
            final_normal = f"g{idx}_normal_node"
            lines.extend([
                f'    local {final_normal} = VRayNormalMap name:"NormalMap"',
                f"    {final_normal}.normal_map = {map_vars['normal']}",
            ])
            if "bump" in map_vars:
                lines.append(f"    {final_normal}.bump_map = {map_vars['bump']}")
        elif is_octane:
            # Octane materials have both normal_tex and bump_tex slots; wire each directly.
            # Bump is wired below in its own block when also present.
            final_normal = map_vars['normal']
        else:
            final_normal = f"g{idx}_normal_node"
            lines.extend([
                f'    local {final_normal} = Normal_Bump name:"NormalBump"',
                f"    {final_normal}.normal_map = {map_vars['normal']}",
            ])
            if "bump" in map_vars:
                lines.append(f"    {final_normal}.bump_map = {map_vars['bump']}")
        add_wire(f"slot_{idx}_normal", "normal", final_normal, slots["normal"])
        if is_octane and "bump" in map_vars:
            add_wire(f"slot_{idx}_bump", "bump", map_vars["bump"], slots["bump"])
    elif "bump" in map_vars:
        if renderer == "materialx":
            height_node = f"g{idx}_height_to_normal"
            bump_node = f"g{idx}_bump_node"
            lines.extend([
                f'    local {height_node} = mcp_makeMaterialXOslMap "heighttonormal_vector3.osl" "BumpHeightToNormal"',
                f"    {height_node}.In_map = {map_vars['bump']}",
                f"    {height_node}.scale = 1.0",
                f'    local {bump_node} = mcp_makeMaterialXOslMap "normalmap.osl" "BumpNormalMap"',
                f"    {bump_node}.In_map = {height_node}",
                f'    {bump_node}.space = "tangent"',
                f"    {bump_node}.scale = 1.0",
            ])
        elif renderer == "arnold":
            bump_node = f"g{idx}_bump_node"
            lines.extend([
                f'    local {bump_node} = ai_bump2d name:"Bump"',
                f"    {bump_node}.bump_map_shader = {map_vars['bump']}",
            ])
        elif renderer == "redshift":
            bump_node = f"g{idx}_bump_node"
            lines.extend([
                f'    local {bump_node} = RS_BumpMap name:"Bump"',
                f"    {bump_node}.input_map = {map_vars['bump']}",
                f"    {bump_node}.inputType = 0",
            ])
        elif renderer == "vray":
            bump_node = map_vars["bump"]
        elif is_octane:
            bump_node = map_vars["bump"]
        else:
            bump_node = f"g{idx}_bump_node"
            lines.extend([
                f'    local {bump_node} = Normal_Bump name:"Bump"',
                f"    {bump_node}.bump_map = {map_vars['bump']}",
            ])
        add_wire(f"slot_{idx}_bump", "bump", bump_node, slots["bump"])

    optional_channels = ("displacement", "opacity", "emission", "translucency", "specular")
    if not include_displacement:
        optional_channels = ("opacity", "emission", "translucency", "specular")

    for channel in optional_channels:
        if channel not in map_vars:
            continue
        candidates = slots.get(channel)
        if not candidates:
            lines.append(f'    skippedList += "{channel}, "')
            continue
        wire_var = map_vars[channel]
        if channel == "displacement" and is_octane:
            # Octane's displacement slot rejects raw Image_MTX; wrap in Texture_displacement.
            td_var = f"g{idx}_disp_node"
            lines.extend([
                f'    local {td_var} = Texture_displacement()',
                f'    {td_var}.name = "Displacement"',
                f'    {td_var}.texture_tex = {wire_var}',
                f'    {td_var}.texture_input_type = 2',
            ])
            wire_var = td_var
        add_wire(f"slot_{idx}_{channel}", channel, wire_var, candidates)

    if "ior" in map_vars:
        lines.append('    skippedList += "ior(no map slot), "')

    return lines
