"""Deep inspection tools for 3ds Max objects.

Uses getPropNames/getProperty for universal property enumeration,
showProperties to:stringstream for declared type detection, and
InstanceMgr for instance awareness.
"""

import json as _json
from ..server import mcp, client
from src.helpers.maxscript import safe_string


@mcp.tool()
def inspect_object(name: str) -> str:
    """Get comprehensive properties of an object for exploration."""
    if client.native_available:
        try:
            payload = _json.dumps({"name": name})
            response = client.send_command(payload, cmd_type="native:inspect_object")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)
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
            result += "  \\\"baseObject\\\": \\\"" + ((classOf obj.baseobject) as string) + "\\\",\\n"

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

            -- Visibility & render flags
            result += "  \\\"isHidden\\\": " + (if obj.isHidden then "true" else "false") + ",\\n"
            result += "  \\\"isFrozen\\\": " + (if obj.isFrozen then "true" else "false") + ",\\n"
            result += "  \\\"renderable\\\": " + (if obj.renderable then "true" else "false") + ",\\n"
            try (result += "  \\\"primaryVisibility\\\": " + (if obj.primaryVisibility then "true" else "false") + ",\\n") catch ()
            try (result += "  \\\"secondaryVisibility\\\": " + (if obj.secondaryVisibility then "true" else "false") + ",\\n") catch ()
            try (result += "  \\\"receiveShadows\\\": " + (if obj.receiveshadows then "true" else "false") + ",\\n") catch ()
            try (result += "  \\\"castShadows\\\": " + (if obj.castshadows then "true" else "false") + ",\\n") catch ()

            -- Layer
            result += "  \\\"layer\\\": \\\"" + obj.layer.name + "\\\",\\n"

            -- Wire color
            result += "  \\\"wirecolor\\\": [" + (obj.wirecolor.r as string) + "," + (obj.wirecolor.g as string) + "," + (obj.wirecolor.b as string) + "],\\n"

            -- Instance detection
            local isInst = InstanceMgr.CanMakeObjectsUnique obj
            local instCount = 0
            if isInst do (
                local instArr = #()
                InstanceMgr.GetInstances obj &instArr
                instCount = instArr.count
            )
            result += "  \\\"isInstanced\\\": " + (if isInst then "true" else "false") + ",\\n"
            result += "  \\\"instanceCount\\\": " + (instCount as string) + ",\\n"

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

            -- Modifiers with enable state
            result += "  \\\"modifiers\\\": ["
            for i = 1 to obj.modifiers.count do (
                if i > 1 do result += ","
                local mod = obj.modifiers[i]
                result += "{{\\\"name\\\": \\\"" + mod.name + "\\\", \\\"class\\\": \\\"" + ((classOf mod) as string) + "\\\""
                result += ", \\\"enabled\\\": " + (if mod.enabled then "true" else "false")
                result += ", \\\"enabledInViews\\\": " + (if mod.enabledInViews then "true" else "false")
                result += ", \\\"enabledInRenders\\\": " + (if mod.enabledInRenders then "true" else "false")
                result += "}}"
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


@mcp.tool()
def inspect_properties(
    name: str,
    target: str = "object",
    modifier_index: int = 0,
) -> str:
    """Deep-inspect all properties of an object, modifier, base object, or material."""
    if client.native_available:
        try:
            payload = {"name": name, "target": target, "modifier_index": modifier_index}
            response = client.send_command(_json.dumps(payload), cmd_type="native:inspect_properties")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)

    # Build the MAXScript expression to get the target object
    if target == "baseobject":
        target_expr = "obj.baseobject"
    elif target == "modifier":
        target_expr = f"obj.modifiers[{modifier_index}]"
    elif target == "material":
        target_expr = "obj.material"
    else:
        target_expr = "obj"

    # Property blacklist — known crashers from extensive testing
    blacklist = "#(#adTextureLock, #notused, #thelist, #geometryOrientationLookAtNode, #target_distance)"

    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj == undefined then (
            "{{\\\"error\\\": \\\"Object not found: {safe}\\\"}}"
        ) else (
            local tgt = {target_expr}
            if tgt == undefined then (
                "{{\\\"error\\\": \\\"Target '{target}' is undefined on {safe}\\\"}}"
            ) else (
                local blacklist = {blacklist}
                local propNames = #()
                try (propNames = makeuniquearray (getpropnames tgt)) catch ()

                -- Build declared-type lookup from showProperties
                local typeMap = #()
                local typeNames = #()
                try (
                    local ss = stringstream ""
                    showproperties tgt to:ss
                    seek ss 0
                    while not (eof ss) do (
                        local myline = readline ss
                        -- Remove parenthesized annotations
                        local pstart = findstring myline "("
                        local pend = findstring myline ")"
                        if pstart != undefined and pend != undefined do
                            myline = replace myline pstart (pend - pstart + 1) ""
                        local parts = filterstring myline ".: "
                        if parts.count >= 2 do (
                            append typeNames (toLower parts[1])
                            append typeMap (trimleft (filterstring myline ":")[2])
                        )
                    )
                ) catch ()

                local result = "{{\\\"target\\\": \\\"" + ("{target}") + "\\\", \\\"class\\\": \\\"" + ((classof tgt) as string) + "\\\", \\\"propertyCount\\\": " + (propNames.count as string) + ", \\\"properties\\\": ["
                local first = true
                for pIdx = 1 to propNames.count do (
                    local p = propNames[pIdx]
                    if (finditem blacklist p) != 0 do continue
                    local val = undefined
                    local valStr = "null"
                    local rtType = "undefined"
                    local skip = false
                    try (
                        val = getproperty tgt p
                        valStr = val as string
                        rtType = (classof val) as string
                        -- Truncate long value strings
                        if valStr.count > 200 do valStr = (substring valStr 1 200) + "..."
                        -- Escape quotes in value
                        valStr = substituteString valStr "\\"" "'"
                        valStr = substituteString valStr "\\n" " "
                        valStr = substituteString valStr "\\r" ""
                    ) catch (skip = true)
                    if not skip do (
                        if not first do result += ","
                        first = false
                        -- Look up declared type
                        local declType = ""
                        local lookupKey = toLower (p as string)
                        local tIdx = finditem typeNames lookupKey
                        if tIdx != 0 do declType = typeMap[tIdx]

                        result += "{{\\\"name\\\": \\\"" + (p as string) + "\\\""
                        result += ", \\\"value\\\": \\\"" + valStr + "\\\""
                        result += ", \\\"runtimeType\\\": \\\"" + rtType + "\\\""
                        if declType != "" do
                            result += ", \\\"declaredType\\\": \\\"" + declType + "\\\""
                        result += "}}"
                    )
                )
                result += "]}}"
                result
            )
        )
    )"""
    response = client.send_command(maxscript, timeout=30.0)
    return response.get("result", "")


@mcp.tool()
def inspect_modifier_properties(name: str, modifier_index: int) -> str:
    """Inspect all properties of a specific modifier on an object."""
    return inspect_properties(name, target="modifier", modifier_index=modifier_index)


@mcp.tool()
def introspect_osl(
    class_name: str = "",
    name: str = "",
    osl_file: str = "",
    sub_material_index: int = 0,
) -> str:
    """Inspect the API surface of any material, texturemap, or modifier class."""
    if not class_name and not name:
        return '{"error":"Provide class_name or name"}'

    safe_class = safe_string(class_name)
    safe_name = safe_string(name)
    safe_osl = safe_string(osl_file)

    # Build the target expression
    if name:
        if sub_material_index > 0:
            target_setup = f'''
            local obj = getNodeByName "{safe_name}"
            if obj == undefined do throw "Object not found"
            if obj.material == undefined do throw "No material on object"
            local m = obj.material[{sub_material_index}]
            if m == undefined do throw "Sub-material index {sub_material_index} not found"
            '''
        else:
            target_setup = f'''
            local obj = getNodeByName "{safe_name}"
            if obj == undefined do throw "Object not found"
            if obj.material == undefined do throw "No material on object"
            local m = obj.material
            '''
    else:
        target_setup = f'local m = {safe_class}()'

    # OSL setup
    osl_setup = ""
    if osl_file and class_name.lower() in ("oslmap", "osl_map", "osl"):
        if "\\" in osl_file or "/" in osl_file:
            osl_setup = f'm.OSLPath = @"{safe_osl}"\nm.OSLAutoUpdate = true'
        else:
            osl_setup = f'm.OSLPath = (getDir #maxRoot) + "OSL\\\\{safe_osl}.osl"\nm.OSLAutoUpdate = true'

    maxscript = f"""(
        local esc = MCP_Server.escapeJsonString
        try (
            {target_setup}
            {osl_setup}

            -- Validate OSL compiled (shader name stays "Example" on failure)
            if (classOf m) == OSLMap and m.OSLShaderName == "Example" do (
                local badPath = try (m.OSLPath) catch ""
                throw ("OSL shader failed to compile — file not found or invalid: " + (badPath as string))
            )

            local cls = (classOf m) as string
            local scls = (superClassOf m) as string

            -- Properties from showProperties (name + type)
            local ss = stringStream ""
            showProperties m to:ss
            seek ss 0
            local propsJson = "["
            local pFirst = true
            while not (eof ss) do (
                local ln = readline ss
                local parts = filterString ln ":"
                if parts.count >= 2 do (
                    local propName = trimRight (trimLeft parts[1] " .")
                    local propType = trimLeft (trimRight parts[2])
                    if propName.count > 0 do (
                        if not pFirst do propsJson += ","
                        propsJson += "{{\\\"name\\\":\\\"" + (esc propName) + "\\\",\\\"type\\\":\\\"" + (esc propType) + "\\\"}}"
                        pFirst = false
                    )
                )
            )
            propsJson += "]"

            -- Interfaces
            local iss = stringStream ""
            showInterfaces m to:iss
            local ifaceStr = iss as string

            -- Output channels (multi-output maps like UberBitmap)
            local chJson = "[]"
            try (
                local nch = m.numIMultipleOutputChannels
                if nch != undefined and nch > 0 do (
                    chJson = "["
                    for i = 1 to nch do (
                        if i > 1 do chJson += ","
                        local cName = m.getIMultipleOutputChannelName i
                        local cType = (m.getIMultipleOutputChannelType i) as string
                        chJson += "{{\\\"index\\\":" + (i as string) + ",\\\"name\\\":\\\"" + (esc cName) + "\\\",\\\"type\\\":\\\"" + (esc cType) + "\\\"}}"
                    )
                    chJson += "]"
                )
            ) catch ()

            -- Cleanup temp instance if we created one (not from scene object)
            {"" if name else "try (delete m) catch ()"}

            "{{\\\"class\\\":\\\"" + (esc cls) + "\\\"," +
            "\\\"superClass\\\":\\\"" + (esc scls) + "\\\"," +
            "\\\"properties\\\":" + propsJson + "," +
            "\\\"outputChannels\\\":" + chJson + "," +
            "\\\"interfaces\\\":\\\"" + (esc ifaceStr) + "\\\"}}"
        ) catch (
            "{{\\\"error\\\":\\\"" + (esc (getCurrentException())) + "\\\"}}"
        )
    )"""
    response = client.send_command(maxscript, timeout=15.0)
    return response.get("result", "{}")
