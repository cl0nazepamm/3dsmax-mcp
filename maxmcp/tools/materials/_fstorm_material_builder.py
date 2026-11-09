"""Native FStorm material graphs (property readback: FStorm 2.0.0Z / Max 2027).

Keep these graphs separate from the generic builder: FStormBitmap handles normals
itself, and FStormMix supplies the AO multiply. Unsupported inputs are reported
instead of wired into unrelated slots.
"""

from ...core.maxscript import safe_string
from ._material_detection import _COLOR_CHANNELS


def fstorm_setup_lines() -> list[str]:
    return [
        "fn mcp_fstormBitmap path nodeName rawData normalData = (",
        '    if path != "" and not (doesFileExist path) do throw ("FStorm texture not found: " + path)',
        "    local tex = FStormBitmap()",
        "    tex.name = nodeName",
        "    tex.input_gamma = not rawData",
        "    tex.gamma = 1.0",
        "    tex.normal_map = normalData",
        '    if path != "" do tex.filename = path',
        '    if tex.input_gamma != (not rawData) or tex.normal_map != normalData do throw "FStorm bitmap settings did not persist"',
        "    tex",
        ")",
    ]


def fstorm_group_lines(group: dict, *, idx: int, mat_var: str, mat_name: str,
                       renderer: str, include_displacement: bool) -> list[str]:
    pbr = renderer == "fstorm_pbr"
    material_class = "FStormPBR" if pbr else "FStorm"
    # (map property, enable property) -- PBR translucence is deliberately irregular.
    slots = {
        "diffuse": ("color_tex", "color_tex_on") if pbr else ("diffuse_tex", "diffuse_tex_on"),
        "roughness": ("roughness_tex", "roughness_tex_on") if pbr else ("reflection_glossy_tex", "reflection_glossy_tex_on"),
        "normal": ("bump_tex", "bump_tex_on") if pbr else ("bump_texture", "bump_texture_on"),
        "bump": ("bump_tex", "bump_tex_on") if pbr else ("bump_texture", "bump_texture_on"),
        "opacity": ("opacity_tex", "opacity_tex_on"),
        "emission": ("emission_texture", "emission_texture_on"),
        "translucency": ("translucence_texture", "translucence_tex_on") if pbr else ("translucence_tex", "translucence_tex_on"),
        "specular": ("reflection_texture", "reflection_texture_on") if pbr else ("reflection_tex", "reflection_tex_on"),
        "displacement": ("displacement_texture", "displacement_texture_on"),
    }
    if pbr:
        slots["metallic"] = ("metalness_tex", "metalness_tex_on")
    channels = group["channels"]
    lines = [
        f'    local {mat_var} = {material_class} name:"{mat_name}"',
        '    local channelList = ""',
        '    local skippedList = ""',
        f"    {mat_var}.reflection = color 255 255 255",
    ]

    def skip(label: str):
        lines.append(f'    skippedList += "{label}, "')

    def bitmap(channel: str, *, normal=False) -> str:
        path = channels[channel]
        var = f"g{idx}_{channel}"
        raw = "false" if channel in _COLOR_CHANNELS else "true"
        path_literal = safe_string(str(path).replace("\\", "/"))
        lines.append(f'    local {var} = mcp_fstormBitmap "{path_literal}" '
                     f'"{safe_string(path.stem)}" {raw} {str(normal).lower()}')
        return var

    def wire(channel: str, var: str, label: str | None = None):
        slot, enabled = slots[channel]
        lines.extend([
            f"    {mat_var}.{slot} = {var}",
            f"    {mat_var}.{enabled} = true",
            f'    if {mat_var}.{slot} != {var} or not {mat_var}.{enabled} do throw "FStorm map assignment did not persist: {slot}"',
            f'    channelList += "{label or channel}->{slot}, "',
        ])

    if "diffuse" in channels:
        base = bitmap("diffuse")
        if "ao" in channels:
            ao = bitmap("ao")
            mixed = f"g{idx}_diffuse_ao"
            lines.extend([
                f'    local {mixed} = FStormMix name:"Diffuse_AO"',
                f"    {mixed}.texture1 = {base}",
                f"    {mixed}.texture2 = {ao}",
                f"    {mixed}.texture1_on = true",
                f"    {mixed}.texture2_on = true",
                f"    {mixed}.mix_mod = 3",
                f'    if {mixed}.texture1 != {base} or {mixed}.texture2 != {ao} or {mixed}.mix_mod != 3 do throw "FStorm AO wiring did not persist"',
            ])
            base = mixed
        wire("diffuse", base, "diffuse(+ao)" if "ao" in channels else "diffuse")
    elif "ao" in channels:
        skip("ao(no diffuse)")

    if "roughness" in channels or "glossiness" in channels:
        channel = "roughness" if "roughness" in channels else "glossiness"
        tex = bitmap(channel)
        if pbr and channel == "glossiness":
            lines.extend([f"    {tex}.color_correct_on = true", f"    {tex}.inverted = true"])
        elif not pbr:
            # Legacy material interprets the same slot as glossy or roughness.
            lines.append(f"    {mat_var}.refl_glossiness_invert = {1 if channel == 'roughness' else 0}")
        wire("roughness", tex, "glossiness(inverted)" if pbr and channel == "glossiness" else channel)
        if "roughness" in channels and "glossiness" in channels:
            skip("glossiness(roughness takes priority)")

    if "normal" in channels:
        wire("normal", bitmap("normal", normal=True))
        if "bump" in channels:
            skip("bump(normal takes priority; one bump slot)")
    elif "bump" in channels:
        wire("bump", bitmap("bump"))

    for channel in ("metallic", "opacity", "emission", "translucency", "specular", "displacement"):
        if channel not in channels:
            continue
        if channel == "metallic" and not pbr:
            skip("metallic(use FStormPBR)")
            continue
        if channel == "displacement" and not include_displacement:
            skip("displacement(disabled)")
            continue
        if channel == "emission":
            lines.extend([f"    {mat_var}.emission_enabled = true", f"    {mat_var}.emission_power = 1.0"])
        if channel == "displacement":
            lines.append(f"    {mat_var}.displacement_on = true")
        wire(channel, bitmap(channel))

    if "orm" in channels:
        skip("orm(unpack into separate AO, roughness and metallic images)")
    if "ior" in channels:
        skip("ior(FStorm uses a nonlinear RGB-to-IOR mapping)")
    for channel in channels:
        if channel not in {*slots, "metallic", "ao", "glossiness", "orm", "ior"}:
            skip(f"{safe_string(channel)}(unsupported)")
    return lines
