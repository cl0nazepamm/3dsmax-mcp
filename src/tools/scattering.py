"""Scattering tools for plugin-based and native workflows in 3ds Max."""

from __future__ import annotations

from ..server import mcp, client


def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _name_array(names: list[str]) -> str:
    return "#(" + ", ".join(f'"{_safe_name(name)}"' for name in names) + ")"


def _float_array(values: list[float]) -> str:
    return "#(" + ", ".join(f"{float(value):.6f}" for value in values) + ")"


@mcp.tool()
def scatter_forest_pack(
    surfaces: list[str],
    geometry: list[str],
    probabilities: list[float] | None = None,
    density: int = 100,
    seed: int = 12345,
    scale_min: float = 85.0,
    scale_max: float = 115.0,
    z_rotation_min: float = -180.0,
    z_rotation_max: float = 180.0,
    source_width_cm: float = 5.0,
    source_height_cm: float = 5.0,
    icon_size_cm: float = 30.0,
    density_units_x_cm: float = 300.0,
    density_units_y_cm: float = 300.0,
    facing_mode: int = 0,
    name: str = "ForestScatter",
    viewport_mode: int = 2,
    render_mode: int = 0,
) -> str:
    """Create a Forest Pack scatter object and wire surfaces + source geometry.

    This uses Forest Pack's native parameter arrays (`surflist`, `cobjlist`,
    `namelist`, `problist`, `geomlist`) so it works in a fully procedural way
    without opening plugin UIs.

    Args:
        surfaces: Distribution surface object names.
        geometry: Source object names to scatter.
        probabilities: Optional per-source weights. Must match `geometry` count.
            Defaults to equal weights.
        density: Forest Pack max density value.
        seed: Random seed.
        scale_min: Uniform min scale percent.
        scale_max: Uniform max scale percent.
        z_rotation_min: Minimum Z random rotation in degrees.
        z_rotation_max: Maximum Z random rotation in degrees.
        source_width_cm: Source width in centimeters (explicit world units).
        source_height_cm: Source height in centimeters (explicit world units).
        icon_size_cm: Forest icon size in centimeters (explicit world units).
        density_units_x_cm: Density map size X in centimeters (explicit world units).
        density_units_y_cm: Density map size Y in centimeters (explicit world units).
        facing_mode: Forest facing direction.
            0 = surface normal (good for sprinkles),
            1 = up/world-aligned (good for trees).
        name: Forest object name.
        viewport_mode: Forest `vmesh` mode (integer enum).
        render_mode: Forest `rmesh` mode (integer enum).

    Returns:
        JSON string summary or JSON error payload.
    """
    if not surfaces:
        raise ValueError("surfaces must contain at least one object name.")
    if not geometry:
        raise ValueError("geometry must contain at least one object name.")

    if probabilities is None:
        weights = [1.0] * len(geometry)
    else:
        if len(probabilities) != len(geometry):
            raise ValueError(
                "probabilities length must match geometry length "
                f"({len(probabilities)} != {len(geometry)})."
            )
        weights = [float(p) for p in probabilities]

    safe_name = _safe_name(name or "ForestScatter")
    surface_arr = _name_array(surfaces)
    geometry_arr = _name_array(geometry)
    weight_arr = _float_array(weights)

    density_value = max(0, int(density))
    seed_value = int(seed)
    smin = float(scale_min)
    smax = float(scale_max)
    rmin = float(z_rotation_min)
    rmax = float(z_rotation_max)
    source_w = max(0.001, float(source_width_cm))
    source_h = max(0.001, float(source_height_cm))
    icon_cm = max(0.001, float(icon_size_cm))
    dens_x_cm = max(0.001, float(density_units_x_cm))
    dens_y_cm = max(0.001, float(density_units_y_cm))
    facing = int(facing_mode)
    vmode = int(viewport_mode)
    rmode = int(render_mode)

    if facing not in (0, 1):
        raise ValueError("facing_mode must be 0 (normal) or 1 (up).")

    maxscript = f"""(
        local surfaceNames = {surface_arr}
        local geometryNames = {geometry_arr}
        local probValues = {weight_arr}

        fn jsonStringArray arr =
        (
            local s = "["
            for i = 1 to arr.count do (
                if i > 1 do s += ","
                s += "\\\"" + arr[i] + "\\\""
            )
            s += "]"
            s
        )

        local forestClass = undefined
        try (forestClass = Forest_Pro) catch ()
        if forestClass == undefined then (
            "{{\\"error\\":\\"Forest Pack is not installed (Forest_Pro unavailable).\\"}}"
        ) else (
            local missingSurfaces = #()
            local missingGeometry = #()
            local surfaceNodes = #()
            local geometryNodes = #()

            for n in surfaceNames do (
                local node = getNodeByName n
                if node == undefined then append missingSurfaces n else append surfaceNodes node
            )

            for n in geometryNames do (
                local node = getNodeByName n
                if node == undefined then append missingGeometry n else append geometryNodes node
            )

            if missingSurfaces.count > 0 or missingGeometry.count > 0 then (
                "{{\\"error\\":\\"Missing objects\\",\\"missingSurfaces\\":" + \
                (jsonStringArray missingSurfaces) + ",\\"missingGeometry\\":" + \
                (jsonStringArray missingGeometry) + "}}"
            ) else (
                local fp = undefined
                try (fp = Forest_Pro name:"{safe_name}") catch ()
                if fp == undefined then (
                    "{{\\"error\\":\\"Failed to create Forest_Pro object.\\"}}"
                ) else (
                    local geomTypeList = for i = 1 to geometryNodes.count collect 2
                    local areaTypeList = for i = 1 to surfaceNodes.count collect 3
                    local areaIncExcList = for i = 1 to surfaceNodes.count collect 0
                    local areaProjectList = for i = 1 to surfaceNodes.count collect 2
                    local areaActiveList = for i = 1 to surfaceNodes.count collect true
                    local areaIdList = for i = 1 to surfaceNodes.count collect i
                    local areaNameList = for i = 1 to surfaceNodes.count collect "Surface Area"
                    local areaNodeList = for i = 1 to surfaceNodes.count collect undefined
                    fp.surflist = surfaceNodes
                    fp.arnodelist = areaNodeList
                    fp.arnamelist = areaNameList
                    fp.artypelist = areaTypeList
                    fp.arincexclist = areaIncExcList
                    fp.arprojectlist = areaProjectList
                    fp.pf_aractivelist = areaActiveList
                    fp.aridlist = areaIdList
                    fp.cobjlist = geometryNodes
                    fp.namelist = geometryNames
                    fp.problist = probValues
                    fp.geomlist = geomTypeList
                    local sourceWidthWU = units.decodeValue "{source_w}cm"
                    local sourceHeightWU = units.decodeValue "{source_h}cm"
                    local iconSizeWU = units.decodeValue "{icon_cm}cm"
                    local densityUnitsXWU = units.decodeValue "{dens_x_cm}cm"
                    local densityUnitsYWU = units.decodeValue "{dens_y_cm}cm"
                    fp.widthlist = #(sourceWidthWU)
                    fp.heightlist = #(sourceHeightWU)

                    fp.maxdensity = {density_value}
                    fp.units_x = densityUnitsXWU
                    fp.units_y = densityUnitsYWU
                    fp.seed = {seed_value}
                    fp.iconSize = iconSizeWU
                    fp.vmesh = {vmode}
                    fp.rmesh = {rmode}
                    fp.direction = {facing}

                    fp.applyScale = true
                    fp.scalelock = true
                    fp.scalexmin = {smin}
                    fp.scaleymin = {smin}
                    fp.scalezmin = {smin}
                    fp.scalexmax = {smax}
                    fp.scaleymax = {smax}
                    fp.scalezmax = {smax}

                    fp.applyRotation = true
                    fp.zrotmin = {rmin}
                    fp.zrotmax = {rmax}

                    "{{\\"name\\":\\"" + fp.name + "\\",\\"surfaceCount\\":" + \
                    (fp.surflist.count as string) + ",\\"geometryCount\\":" + \
                    (fp.cobjlist.count as string) + ",\\"distributionCount\\":" + \
                    (fp.arnodelist.count as string) + ",\\"density\\":" + \
                    (fp.maxdensity as string) + ",\\"seed\\":" + \
                    (fp.seed as string) + "}}"
                )
            )
        )
    )"""

    response = client.send_command(maxscript)
    return response.get("result", '{"error":"No response from 3ds Max"}')
