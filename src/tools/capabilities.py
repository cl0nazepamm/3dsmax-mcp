from ..server import mcp, client


@mcp.tool()
def get_plugin_capabilities() -> str:
    """Get 3ds Max version, available renderers, installed plugins, and class counts."""
    maxscript = r"""(
        local esc = MCP_Server.escapeJsonString

        -- Version
        local ver = maxVersion()
        local verYear = (1998 + (ver[1] / 1000)) as integer

        -- Current renderer
        local curRenderer = esc ((classOf renderers.current) as string)

        -- Available renderers
        local rArr = ""
        local rClasses = for c in RendererClass.classes collect (c as string)
        for i = 1 to rClasses.count do (
            if i > 1 do rArr += ","
            rArr += "\"" + (esc rClasses[i]) + "\""
        )

        -- Plugin detection
        local hasForestPack = false
        try (if Forest_Pro != undefined do hasForestPack = true) catch ()

        local hasForestLite = false
        try (if Forest_Lite != undefined do hasForestLite = true) catch ()

        local hasTyFlow = false
        try (if tyFlow != undefined do hasTyFlow = true) catch ()

        local hasRailClone = false
        try (if RailClone_Pro != undefined do hasRailClone = true) catch ()

        local hasPhoenixFD = false
        try (if PhoenixFDLiquid != undefined do hasPhoenixFD = true) catch ()

        -- Class counts
        local matCount = Material.classes.count
        local geoCount = GeometryClass.classes.count
        local modCount = Modifier.classes.count

        fn boolStr val = (if val then "true" else "false")

        -- Build JSON
        "{\"maxVersion\":" + (verYear as string) + \
        ",\"renderer\":\"" + curRenderer + "\"" + \
        ",\"renderers\":[" + rArr + "]" + \
        ",\"plugins\":{" + \
            "\"forestPack\":" + (boolStr hasForestPack) + \
            ",\"forestLite\":" + (boolStr hasForestLite) + \
            ",\"tyFlow\":" + (boolStr hasTyFlow) + \
            ",\"railClone\":" + (boolStr hasRailClone) + \
            ",\"phoenixFD\":" + (boolStr hasPhoenixFD) + \
        "}" + \
        ",\"materialClasses\":" + (matCount as string) + \
        ",\"geometryClasses\":" + (geoCount as string) + \
        ",\"modifierClasses\":" + (modCount as string) + \
        "}"
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "{}")
