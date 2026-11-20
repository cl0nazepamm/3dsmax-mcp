from __future__ import annotations

import json as _json

from ...server import mcp, client
from ...core.coerce import StrList
from ...core.maxscript import NODE_LOOKUP_FNS, batch_reply, node_by_name, safe_string


@mcp.tool()
def set_parent(children: StrList, parent: str = "") -> str:
    """Parent or unparent objects in the 3ds Max scene."""
    if client.native_available:
        payload = _json.dumps({"children": children, "parent": parent})
        response = client.send_command(payload, cmd_type="native:set_parent")
        return response.get("result", "")

    child_names = "#(" + ", ".join(f'"{safe_string(n)}"' for n in children) + ")"

    if parent:
        safe_parent = safe_string(parent)
        maxscript = f"""(
            {NODE_LOOKUP_FNS}
            local parentObj = {node_by_name(parent, missing_ok=True)}
            if parentObj == undefined then (
                "Parent not found: {safe_parent}"
            ) else (
                local childNames = {child_names}
                local done = #()
                local notFound = #()
                local ambiguous = #()
                for n in childNames do (
                    local c = mcpBatchNode n notFound ambiguous
                    if c != undefined do (
                        c.parent = parentObj
                        append done n
                    )
                )
                if done.count == 0 and ambiguous.count > 0 do mcpThrowUnresolved notFound ambiguous "nothing was parented"
                local result = "Parented " + (done.count as string) + " objects under " + parentObj.name
                if notFound.count > 0 do result += " | Not found: " + (notFound as string)
                result + (mcpAmbiguousReply ambiguous)
            )
        )"""
    else:
        maxscript = f"""(
            {NODE_LOOKUP_FNS}
            local childNames = {child_names}
            local done = #()
            local notFound = #()
            local ambiguous = #()
            for n in childNames do (
                local c = mcpBatchNode n notFound ambiguous
                if c != undefined do (
                    c.parent = undefined
                    append done n
                )
            )
            if done.count == 0 and ambiguous.count > 0 do mcpThrowUnresolved notFound ambiguous "nothing was unparented"
            local result = "Unparented " + (done.count as string) + " objects"
            if notFound.count > 0 do result += " | Not found: " + (notFound as string)
            result + (mcpAmbiguousReply ambiguous)
        )"""
    response = client.send_command(maxscript)
    return batch_reply(response.get("result", ""))


@mcp.tool()
def get_hierarchy(name: str) -> str:
    """Get the hierarchy tree of an object (recursive children)."""
    if client.native_available:
        try:
            params = _json.dumps({"name": name})
            response = client.send_command(params, cmd_type="native:get_hierarchy")
            return response.get("result", "")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    safe = safe_string(name)
    maxscript = f"""(
        fn buildTree obj = (
            local childArr = #()
            for c in obj.children do (
                append childArr (buildTree c)
            )
            local childStr = "["
            for i = 1 to childArr.count do (
                if i > 1 do childStr += ","
                childStr += childArr[i]
            )
            childStr += "]"
            "{{" + \
                "\\\"name\\\":\\\"" + obj.name + "\\\"," + \
                "\\\"class\\\":\\\"" + ((classOf obj) as string) + "\\\"," + \
                "\\\"children\\\":" + childStr + \
            "}}"
        )
        local rootObj = {node_by_name(name, missing_ok=True)}
        if rootObj != undefined then (
            buildTree rootObj
        ) else (
            "Object not found: {safe}"
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
