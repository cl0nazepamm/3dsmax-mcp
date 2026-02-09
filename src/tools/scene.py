from ..server import mcp, client


@mcp.tool()
def get_scene_info(
    class_name: str = "",
    pattern: str = "",
    layer: str = "",
    limit: int = 100,
    offset: int = 0,
    roots_only: bool = False,
) -> str:
    """Get a list of all objects in the current 3ds Max scene.

    Returns a formatted list with each object's name, class, position, and visibility.

    Args:
        class_name: Filter by class name (e.g. "Box", "Sphere", "Dummy").
        pattern: Wildcard name filter (e.g. "Wall*", "*Light*").
        layer: Filter by layer name.
        limit: Max objects to return (default 100, cap).
        offset: Pagination offset.
        roots_only: Only top-level objects (no parent).
    """
    has_filter = class_name or pattern or layer or roots_only

    if not has_filter and offset == 0:
        # Summary mode — compact overview regardless of scene size
        maxscript = r"""(
            local totalCount = objects.count
            local hiddenCount = 0
            local frozenCount = 0
            local classMap = #()
            local classNames = #()
            local layerNames = #()
            for obj in objects do (
                if obj.isHidden do hiddenCount += 1
                if obj.isFrozen do frozenCount += 1
                local cn = (classOf obj) as string
                local idx = findItem classNames cn
                if idx == 0 then (
                    append classNames cn
                    append classMap 1
                ) else (
                    classMap[idx] += 1
                )
                local ln = obj.layer.name
                if (findItem layerNames ln) == 0 do append layerNames ln
            )
            local classPairs = ""
            for i = 1 to classNames.count do (
                if i > 1 do classPairs += ","
                classPairs += "\"" + classNames[i] + "\":" + (classMap[i] as string)
            )
            local layerList = ""
            for i = 1 to layerNames.count do (
                if i > 1 do layerList += ","
                layerList += "\"" + layerNames[i] + "\""
            )
            "{\"totalObjects\":" + (totalCount as string) + \
            ",\"classCounts\":{" + classPairs + "}" + \
            ",\"layers\":[" + layerList + "]" + \
            ",\"hiddenCount\":" + (hiddenCount as string) + \
            ",\"frozenCount\":" + (frozenCount as string) + "}"
        )"""
        response = client.send_command(maxscript)
        return response.get("result", "{}")

    # Filtered mode — return per-object details, capped at limit
    # Build MAXScript filter conditions
    conditions = []
    if class_name:
        conditions.append(
            '((classOf obj) as string) == "' + class_name.replace('"', '') + '"'
        )
    if pattern:
        safe_pattern = pattern.replace('"', '').replace("\\", "\\\\")
        conditions.append('matchPattern obj.name pattern:"' + safe_pattern + '"')
    if layer:
        conditions.append('obj.layer.name == "' + layer.replace('"', '') + '"')
    if roots_only:
        conditions.append("obj.parent == undefined")

    filter_expr = " and ".join(conditions) if conditions else "true"

    maxscript = (
        '(\n'
        '    local matched = #()\n'
        '    for obj in objects where (' + filter_expr + ') do append matched obj\n'
        '    local totalMatched = matched.count\n'
        '    local startIdx = ' + str(offset + 1) + '\n'
        '    local endIdx = amin #(matched.count, ' + str(offset + limit) + ')\n'
        '    local arr = #()\n'
        '    for i = startIdx to endIdx do (\n'
        '        local obj = matched[i]\n'
        '        local posStr = "[" + (obj.pos.x as string) + "," + \\\n'
        '                       (obj.pos.y as string) + "," + \\\n'
        '                       (obj.pos.z as string) + "]"\n'
        '        local parentName = if obj.parent != undefined then obj.parent.name else ""\n'
        '        local parentField = if parentName == "" then "null" else ("\\"" + parentName + "\\"") \n'
        '        local entry = "{" + \\\n'
        '            "\\"name\\":\\"" + obj.name + "\\"," + \\\n'
        '            "\\"class\\":\\"" + ((classOf obj) as string) + "\\"," + \\\n'
        '            "\\"position\\":" + posStr + "," + \\\n'
        '            "\\"parent\\":" + parentField + "," + \\\n'
        '            "\\"numChildren\\":" + (obj.children.count as string) + "," + \\\n'
        '            "\\"isHidden\\":" + (if obj.isHidden then "true" else "false") + "," + \\\n'
        '            "\\"isFrozen\\":" + (if obj.isFrozen then "true" else "false") + "," + \\\n'
        '            "\\"layer\\":\\"" + obj.layer.name + "\\"" + \\\n'
        '        "}"\n'
        '        append arr entry\n'
        '    )\n'
        '    local result = "{\\"totalMatched\\":" + (totalMatched as string) + ",\\"objects\\":["\n'
        '    for i = 1 to arr.count do (\n'
        '        if i > 1 do result += ","\n'
        '        result += arr[i]\n'
        '    )\n'
        '    result += "]}"\n'
        '    result\n'
        ')\n'
    )
    response = client.send_command(maxscript)
    return response.get("result", '{"totalMatched":0,"objects":[]}')


@mcp.tool()
def get_selection() -> str:
    """Get information about the currently selected objects in 3ds Max.

    Returns a formatted list with each selected object's name, class,
    position, and wireframe color.
    """
    maxscript = r"""(
        local arr = #()
        for obj in selection do (
            local posStr = "[" + (obj.pos.x as string) + "," + \
                           (obj.pos.y as string) + "," + \
                           (obj.pos.z as string) + "]"
            local colorStr = "[" + (obj.wirecolor.r as string) + "," + \
                             (obj.wirecolor.g as string) + "," + \
                             (obj.wirecolor.b as string) + "]"
            local entry = "{" + \
                "\"name\":\"" + obj.name + "\"," + \
                "\"class\":\"" + ((classOf obj) as string) + "\"," + \
                "\"position\":" + posStr + "," + \
                "\"wirecolor\":" + colorStr + \
            "}"
            append arr entry
        )
        local result = "["
        for i = 1 to arr.count do (
            if i > 1 do result += ","
            result += arr[i]
        )
        result += "]"
        result
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "[]")
