import json
import os
import re
import tempfile
from uuid import uuid4

from ...core.maxscript import NODE_LOOKUP_FNS, batch_reply, safe_string
from ...core.native_compat import is_missing_native_route_error
from ...core.max_client import MaxBridgeError

from ...server import mcp, client


COMMS_DIR = os.path.join(tempfile.gettempdir(), "3dsmax-mcp")


def _sanitize_filename(name: str) -> str:
    """Sanitize an object name for use as a filename."""
    return re.sub(r'[<>:"/\\|?*]', "_", name)


@mcp.tool()
def isolate_and_capture_selected() -> str:
    """Capture isolated viewport screenshots of each selected object (resolves to top-level parents)."""
    capture_dir = os.path.join(COMMS_DIR, "identify", uuid4().hex).replace("\\", "/")

    if client.native_available:
        payload = json.dumps({"capture_dir": capture_dir})
        try:
            response = client.send_command(
                payload,
                cmd_type="native:isolate_and_capture_selected",
            )
            return response.get("result", "[]")
        except (MaxBridgeError, RuntimeError) as exc:
            if not is_missing_native_route_error(exc):
                raise

    maxscript = f"""(
        makeDir "{capture_dir}" all:true

        selObjs = getCurrentSelection()
        if selObjs.count == 0 then (
            "[]"
        ) else (
            fn getRootParent obj = (
                p = obj
                while p.parent != undefined do p = p.parent
                p
            )

            roots = #()
            for obj in selObjs do (
                r = getRootParent obj
                idx = findItem roots r
                if idx == 0 do append roots r
            )

            fn getDescendants obj = (
                res = #(obj)
                for c in obj.children do join res (getDescendants c)
                res
            )

            -- Group roots by baseObject identity (instances share the same baseObject)
            instanceGroups = #()   -- array of arrays of root nodes
            groupBaseObjs = #()    -- parallel array of baseObjects for matching
            for r in roots do (
                found = false
                for g = 1 to groupBaseObjs.count while not found do (
                    if r.baseObject == groupBaseObjs[g] then (
                        append instanceGroups[g] r
                        found = true
                    )
                )
                if not found do (
                    append groupBaseObjs r.baseObject
                    append instanceGroups #(r)
                )
            )

            -- Remember which objects were already hidden
            hiddenBefore = for obj in objects where obj.isHidden collect obj

            -- Capture one representative per instance group
            resultParts = #()
            for grp in instanceGroups do (
                rep = grp[1]
                descendants = getDescendants rep

                -- Hide everything except this object's hierarchy
                for obj in objects do obj.isHidden = true
                for d in descendants do d.isHidden = false

                select descendants
                max zoomext sel
                completeredraw()

                safeName = rep.name
                for badChar in #("<", ">", ":", "/", "\\\\", "|", "?", "*") do (
                    safeName = substituteString safeName badChar "_"
                )
                capturePath = "{capture_dir}/" + safeName + ".png"

                vp = gw.getViewportDib()
                vp.filename = capturePath
                save vp

                -- Build instances array: ["Name1","Name2",...]
                instStr = "["
                for i = 1 to grp.count do (
                    if i > 1 do instStr += ","
                    instStr += "\\\"" + (substituteString grp[i].name "\\\\" "\\\\\\\\") + "\\\""
                )
                instStr += "]"

                entry = "{{\\\"name\\\":\\\"" + (substituteString rep.name "\\\\" "\\\\\\\\") + "\\\",\\\"image_path\\\":\\\"" + (substituteString capturePath "\\\\" "/") + "\\\",\\\"instances\\\":" + instStr + "}}"
                append resultParts entry
            )

            -- Restore visibility
            for obj in objects do obj.isHidden = false
            for obj in hiddenBefore do (
                if isValidNode obj do obj.isHidden = true
            )

            select selObjs

            outStr = "["
            for i = 1 to resultParts.count do (
                if i > 1 do outStr += ","
                outStr += resultParts[i]
            )
            outStr += "]"
            outStr
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "[]")


@mcp.tool()
def batch_rename_objects(renames_json: str) -> str:
    """Rename multiple objects in a single batch operation."""
    renames = json.loads(renames_json)

    if client.native_available:
        payload = json.dumps({"renames": renames})
        response = client.send_command(payload, cmd_type="native:batch_rename_objects")
        return response.get("result", "")

    # Entries apply in order, each old name looked up when its entry applies;
    # a name several objects share renames none of them.
    parts = []
    for r in renames:
        old = safe_string(r["old_name"])
        new = safe_string(r["new_name"])
        parts.append(
            f'obj = mcpBatchNode "{old}" notFound ambiguous\n'
            f'if obj != undefined do (\n'
            f'    obj.name = "{new}"\n'
            f'    append renamed "{old} -> {new}"\n'
            f')'
        )

    rename_block = "\n".join(parts)
    maxscript = f"""(
        {NODE_LOOKUP_FNS}
        local obj = undefined
        local renamed = #()
        local notFound = #()
        local ambiguous = #()
        {rename_block}
        if renamed.count == 0 and ambiguous.count > 0 do mcpThrowUnresolved notFound ambiguous "nothing was renamed"
        local msg = "Renamed: " + (renamed.count as string)
        if notFound.count > 0 do msg += " | Not found: " + (notFound as string)
        msg + (mcpAmbiguousReply ambiguous)
    )"""

    response = client.send_command(maxscript)
    return batch_reply(response.get("result", ""))
