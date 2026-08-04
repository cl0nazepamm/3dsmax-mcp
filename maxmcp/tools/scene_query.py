"""Node-level scene query tools (ref graph and mesh instancing)."""

from __future__ import annotations

import json as _json

from ..server import mcp, client
from ..helpers.maxscript import safe_string


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
