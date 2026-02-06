import json
import os
import re
import tempfile

from ..server import mcp, client


COMMS_DIR = os.path.join(tempfile.gettempdir(), "3dsmax-mcp")


def _sanitize_filename(name: str) -> str:
    """Sanitize an object name for use as a filename."""
    return re.sub(r'[<>:"/\\|?*]', "_", name)


@mcp.tool()
def isolate_and_capture_selected() -> str:
    """Capture isolated viewport screenshots of each selected object (resolves to top-level parents).

    Detects instances (objects sharing the same baseObject) and only captures
    one representative per instance group — avoiding redundant identification.

    For each unique top-level parent among the selection:
      - Hides all other objects
      - Selects it and all its descendants
      - Zooms extents on the selection
      - Captures the viewport to a PNG file
      - Restores visibility

    Returns JSON array:
      [{"name": "Box001", "image_path": "C:/temp/...", "instances": ["Box001", "Box002", "Box003"]}, ...]

    The "instances" array lists ALL object names that share the same base object.
    The orchestrator should identify once and rename all instances from that result.
    """
    capture_dir = os.path.join(COMMS_DIR, "identify").replace("\\", "/")

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
    """Rename multiple objects in a single batch operation.

    Args:
        renames_json: JSON string array of renames, e.g.
            [{"old_name": "Box001", "new_name": "House"}, ...]

    Returns confirmation summary of renamed objects.
    """
    renames = json.loads(renames_json)

    parts = []
    for r in renames:
        old = r["old_name"].replace("\\", "\\\\").replace('"', '\\"')
        new = r["new_name"].replace("\\", "\\\\").replace('"', '\\"')
        parts.append(
            f'obj = getNodeByName "{old}"\n'
            f'if obj != undefined then (\n'
            f'    obj.name = "{new}"\n'
            f'    append renamed "{old} -> {new}"\n'
            f') else (\n'
            f'    append notFound "{old}"\n'
            f')'
        )

    rename_block = "\n".join(parts)
    maxscript = f"""(
        renamed = #()
        notFound = #()
        {rename_block}
        msg = "Renamed: " + (renamed.count as string)
        if notFound.count > 0 do msg += " | Not found: " + (notFound as string)
        msg
    )"""

    response = client.send_command(maxscript)
    return response.get("result", "")
