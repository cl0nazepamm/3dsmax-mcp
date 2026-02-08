from ..server import mcp, client


def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


@mcp.tool()
def inspect_object(name: str) -> str:
    """Get comprehensive properties of an object for exploration.

    Returns class info, all gettable properties with current values,
    modifier list with their properties, and material info.

    Args:
        name: The object name (e.g. "Box001")

    Returns detailed JSON property dump.
    """
    safe = _safe_name(name)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj == undefined then (
            "Object not found: {safe}"
        ) else (
            local result = "{{\\n"

            -- Basic info
            result += "  \\\"name\\\": \\\"" + obj.name + "\\\",\\n"
            result += "  \\\"class\\\": \\\"" + ((classOf obj) as string) + "\\\",\\n"
            result += "  \\\"superclass\\\": \\\"" + ((superClassOf obj) as string) + "\\\",\\n"

            -- Transform
            result += "  \\\"position\\\": [" + (obj.pos.x as string) + "," + (obj.pos.y as string) + "," + (obj.pos.z as string) + "],\\n"
            result += "  \\\"rotation\\\": [" + (obj.rotation.x as string) + "," + (obj.rotation.y as string) + "," + (obj.rotation.z as string) + "],\\n"
            result += "  \\\"scale\\\": [" + (obj.scale.x as string) + "," + (obj.scale.y as string) + "," + (obj.scale.z as string) + "],\\n"

            -- Hierarchy
            local parentName = if obj.parent != undefined then obj.parent.name else "null"
            result += "  \\\"parent\\\": \\\"" + parentName + "\\\",\\n"
            local childNames = for c in obj.children collect c.name
            result += "  \\\"children\\\": ["
            for i = 1 to childNames.count do (
                if i > 1 do result += ","
                result += "\\\"" + childNames[i] + "\\\""
            )
            result += "],\\n"

            -- Visibility
            result += "  \\\"isHidden\\\": " + (if obj.isHidden then "true" else "false") + ",\\n"
            result += "  \\\"isFrozen\\\": " + (if obj.isFrozen then "true" else "false") + ",\\n"

            -- Layer
            result += "  \\\"layer\\\": \\\"" + obj.layer.name + "\\\",\\n"

            -- Wire color
            result += "  \\\"wirecolor\\\": [" + (obj.wirecolor.r as string) + "," + (obj.wirecolor.g as string) + "," + (obj.wirecolor.b as string) + "],\\n"

            -- Mesh info (if applicable)
            try (
                local m = snapshotAsMesh obj
                result += "  \\\"numVerts\\\": " + (m.numVerts as string) + ",\\n"
                result += "  \\\"numFaces\\\": " + (m.numFaces as string) + ",\\n"
                delete m
            ) catch (
                result += "  \\\"numVerts\\\": null,\\n"
                result += "  \\\"numFaces\\\": null,\\n"
            )

            -- Bounding box
            local bbMin = obj.min
            local bbMax = obj.max
            local dims = bbMax - bbMin
            result += "  \\\"boundingBox\\\": {{\\\"min\\\": [" + (bbMin.x as string) + "," + (bbMin.y as string) + "," + (bbMin.z as string) + "], \\\"max\\\": [" + (bbMax.x as string) + "," + (bbMax.y as string) + "," + (bbMax.z as string) + "], \\\"dimensions\\\": [" + (dims.x as string) + "," + (dims.y as string) + "," + (dims.z as string) + "]}},\\n"

            -- Modifiers
            result += "  \\\"modifiers\\\": ["
            for i = 1 to obj.modifiers.count do (
                if i > 1 do result += ","
                local mod = obj.modifiers[i]
                result += "{{\\\"name\\\": \\\"" + mod.name + "\\\", \\\"class\\\": \\\"" + ((classOf mod) as string) + "\\\"}}"
            )
            result += "],\\n"

            -- Material
            if obj.material != undefined then (
                result += "  \\\"material\\\": {{\\\"name\\\": \\\"" + obj.material.name + "\\\", \\\"class\\\": \\\"" + ((classOf obj.material) as string) + "\\\"}}\\n"
            ) else (
                result += "  \\\"material\\\": null\\n"
            )

            result += "}}"
            result
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
