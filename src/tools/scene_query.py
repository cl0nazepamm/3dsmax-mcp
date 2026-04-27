"""Advanced scene query tools using MAXScript reflection APIs.

Uses getclassinstances, refs.dependentnodes, refs.dependents, and
InstanceMgr for deep scene introspection that goes far beyond simple
object iteration.
"""

from typing import Optional
import json as _json
from ..server import mcp, client
from src.helpers.maxscript import safe_string


@mcp.tool()
def find_class_instances(
    class_name: str,
    superclass: str = "",
) -> str:
    """Find all instances of a class in the entire scene using getclassinstances."""
    if superclass:
        safe_sc = safe_string(superclass)
        maxscript = f"""(
            local result = "{{\\\"superclass\\\": \\\"" + "{safe_sc}" + "\\\", \\\"classes\\\": ["
            local scls = execute "{safe_sc}"
            if scls == undefined then (
                "{{\\\"error\\\": \\\"Unknown superclass: {safe_sc}\\\"}}"
            ) else (
                local allClasses = scls.classes
                local entries = #()
                for c in allClasses do (
                    local insts = getclassinstances c
                    if insts.count > 0 do (
                        local entry = "{{\\\"class\\\": \\\"" + (c as string) + "\\\", \\\"count\\\": " + (insts.count as string)
                        -- Find which nodes reference first few instances
                        local nodeNames = #()
                        local maxCheck = amin #(insts.count, 5)
                        for i = 1 to maxCheck do (
                            local depNodes = refs.dependentnodes insts[i]
                            for n in depNodes do (
                                if (finditem nodeNames n.name) == 0 do append nodeNames n.name
                            )
                        )
                        entry += ", \\\"sampleNodes\\\": ["
                        local maxNames = amin #(nodeNames.count, 10)
                        for i = 1 to maxNames do (
                            if i > 1 do entry += ","
                            entry += "\\\"" + nodeNames[i] + "\\\""
                        )
                        entry += "]}}"
                        append entries entry
                    )
                )
                for i = 1 to entries.count do (
                    if i > 1 do result += ","
                    result += entries[i]
                )
                result += "]}}"
                result
            )
        )"""
    else:
        safe_cls = safe_string(class_name)
        maxscript = f"""(
            local cls = execute "{safe_cls}"
            if cls == undefined then (
                "{{\\\"error\\\": \\\"Unknown class: {safe_cls}\\\"}}"
            ) else (
                local insts = getclassinstances cls
                local result = "{{\\\"class\\\": \\\"" + (cls as string) + "\\\", \\\"count\\\": " + (insts.count as string) + ", \\\"instances\\\": ["
                local maxShow = amin #(insts.count, 50)
                for i = 1 to maxShow do (
                    if i > 1 do result += ","
                    local inst = insts[i]
                    local instName = ""
                    try (instName = inst.name) catch (try (instName = (exprForMAXObject inst)) catch (instName = (classof inst) as string))
                    local depNodes = refs.dependentnodes inst
                    local nodeArr = "["
                    local maxNodes = amin #(depNodes.count, 5)
                    for j = 1 to maxNodes do (
                        if j > 1 do nodeArr += ","
                        nodeArr += "\\\"" + depNodes[j].name + "\\\""
                    )
                    nodeArr += "]"
                    result += "{{\\\"name\\\": \\\"" + instName + "\\\", \\\"usedByNodes\\\": " + nodeArr + "}}"
                )
                result += "]}}"
                result
            )
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def get_instances(name: str) -> str:
    """Get all instances (copies sharing the same base object) of a scene object."""
    if client.native_available:
        try:
            payload = _json.dumps({"name": name})
            response = client.send_command(payload, cmd_type="native:get_instances")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj == undefined then (
            "{{\\\"error\\\": \\\"Object not found: {safe}\\\"}}"
        ) else (
            local canInst = InstanceMgr.CanMakeObjectsUnique obj
            if not canInst then (
                "{{\\\"name\\\": \\\"" + obj.name + "\\\", \\\"isInstanced\\\": false, \\\"instances\\\": []}}"
            ) else (
                local instArr = #()
                InstanceMgr.GetInstances obj &instArr
                local result = "{{\\\"name\\\": \\\"" + obj.name + "\\\", \\\"isInstanced\\\": true, \\\"instanceCount\\\": " + (instArr.count as string) + ", \\\"instances\\\": ["
                for i = 1 to instArr.count do (
                    if i > 1 do result += ","
                    result += "\\\"" + instArr[i].name + "\\\""
                )
                result += "]}}"
                result
            )
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def get_dependencies(
    name: str,
    direction: str = "dependents",
) -> str:
    """Trace the reference graph for an object using refs.dependents / refs.dependentnodes."""
    if client.native_available:
        try:
            payload = _json.dumps({"name": name, "direction": direction})
            response = client.send_command(payload, cmd_type="native:get_dependencies")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe = safe_string(name)
    normalized_direction = direction.lower().strip()
    if normalized_direction not in ("dependents", "dependentnodes"):
        return "direction must be 'dependents' or 'dependentnodes'"
    deps_expr = "refs.dependents obj"
    if normalized_direction == "dependentnodes":
        deps_expr = "refs.dependentnodes obj"
    maxscript = f"""(
        local obj = getNodeByName "{safe}"
        if obj == undefined then (
            "{{\\\"error\\\": \\\"Object not found: {safe}\\\"}}"
        ) else (
            local deps = {deps_expr}
            local classMap = #()
            local classNames = #()
            for d in deps do (
                local cn = (classof d) as string
                local idx = finditem classNames cn
                if idx == 0 then (
                    append classNames cn
                    append classMap 1
                ) else (
                    classMap[idx] += 1
                )
            )
            local result = "{{\\\"object\\\": \\\"" + obj.name + "\\\", \\\"direction\\\": \\\"{normalized_direction}\\\", \\\"totalDependents\\\": " + (deps.count as string) + ", \\\"byClass\\\": {{"
            for i = 1 to classNames.count do (
                if i > 1 do result += ","
                result += "\\\"" + classNames[i] + "\\\": " + (classMap[i] as string)
            )
            result += "}}}}"
            result
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def find_objects_by_property(
    property_name: str,
    property_value: str = "",
    class_filter: str = "",
) -> str:
    """Find scene objects that have a specific property, optionally matching a value."""
    if client.native_available:
        try:
            payload = {
                "property_name": property_name,
                "property_value": property_value,
                "class_filter": class_filter,
            }
            response = client.send_command(_json.dumps(payload), cmd_type="native:find_objects_by_property")
            return response.get("result", "[]")
        except RuntimeError:
            pass

    safe_prop = safe_string(property_name)
    safe_val = safe_string(property_value)
    class_cond = ""
    if class_filter:
        safe_class = safe_string(class_filter)
        class_cond = f'and (matchPattern ((classof obj) as string) pattern:"*{safe_class}*")'

    if property_value:
        maxscript = f"""(
            local matched = #()
            for obj in objects {class_cond} do (
                try (
                    local val = getproperty obj #{safe_prop}
                    if (val as string) == "{safe_val}" or (toLower (val as string)) == (toLower "{safe_val}") do
                        append matched obj
                ) catch ()
            )
            local result = "["
            for i = 1 to matched.count do (
                if i > 1 do result += ","
                result += "\\\"" + matched[i].name + "\\\""
            )
            result += "]"
            result
        )"""
    else:
        maxscript = f"""(
            local matched = #()
            for obj in objects {class_cond} do (
                try (
                    local val = getproperty obj #{safe_prop}
                    append matched #(obj.name, val as string)
                ) catch ()
            )
            local result = "["
            for i = 1 to matched.count do (
                if i > 1 do result += ","
                result += "{{\\\"name\\\": \\\"" + matched[i][1] + "\\\", \\\"value\\\": \\\"" + matched[i][2] + "\\\"}}"
            )
            result += "]"
            result
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "[]")
