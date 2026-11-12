from __future__ import annotations

import json as _json
from typing import Optional
from ...server import mcp, client
from ...core.coerce import StrList
from ...core.maxscript import NODE_LOOKUP_FNS, batch_reply, safe_string


@mcp.tool()
def select_objects(
    names: Optional[StrList] = None,
    pattern: str = "",
    class_name: str = "",
    all: bool = False,
) -> str:
    """Select objects in the 3ds Max scene. An explicit names=[] clears selection."""
    if names == [] and not (pattern or class_name or all):
        return client.send_command('(clearSelection(); "Selected 0 objects")').get("result", "")
    if client.native_available:
        try:
            params: dict = {}
            if all:
                params["all"] = True
            if names:
                params["names"] = names
            if pattern:
                params["pattern"] = pattern
            if class_name:
                params["class_name"] = class_name
            response = client.send_command(_json.dumps(params), cmd_type="native:select_objects")
            return response.get("result", "")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    if all:
        maxscript = """(
            select objects
            local result = "Selected " + (selection.count as string) + " objects"
            result
        )"""
    elif names:
        name_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in names) + ")"
        # Names that match no object or several objects are skipped; shared
        # names are reported.
        maxscript = f"""(
            {NODE_LOOKUP_FNS}
            local nameList = {name_arr}
            local found = #()
            local missing = #()
            local ambiguous = #()
            for n in nameList do (
                local obj = mcpBatchNode n missing ambiguous
                if obj != undefined do append found obj
            )
            if found.count == 0 and ambiguous.count > 0 do mcpThrowUnresolved missing ambiguous "the selection was not changed"
            clearSelection()
            select found
            "Selected " + (found.count as string) + " of " + (nameList.count as string) + " objects" + (mcpAmbiguousReply ambiguous)
        )"""
    elif pattern:
        safe_pat = safe_string(pattern)
        maxscript = f"""(
            clearSelection()
            local matched = for obj in objects where matchPattern obj.name pattern:"{safe_pat}" collect obj
            select matched
            "Selected " + (matched.count as string) + " objects matching \\\"" + "{safe_pat}" + "\\\""
        )"""
    elif class_name:
        maxscript = f"""(
            clearSelection()
            local matched = for obj in objects where (classOf obj) as string == "{class_name}" collect obj
            select matched
            "Selected " + (matched.count as string) + " objects of class {class_name}"
        )"""
    else:
        return "At least one parameter (names, pattern, class_name, or all) must be provided."

    response = client.send_command(maxscript)
    return batch_reply(response.get("result", ""))
