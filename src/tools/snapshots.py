"""Layer 1 compact snapshot tools — low-token scene understanding."""

import json
from ..server import mcp, client

# Module-level state for delta tracking
_previous_snapshot: dict | None = None


@mcp.tool()
def get_scene_snapshot(max_roots: int = 50) -> str:
    """Compact scene overview in one call: object counts by class, materials summary,"""
    if client.native_available:
        try:
            params = {}
            if max_roots != 50:
                params["max_roots"] = max_roots
            response = client.send_command(
                json.dumps(params) if params else "",
                cmd_type="native:scene_snapshot",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    maxscript = r"""(
        local esc = MCP_Server.escapeJsonString
        local totalCount = objects.count
        local hiddenCount = 0
        local frozenCount = 0
        local classNames = #()
        local classCounts = #()
        local matNames = #()
        local matCounts = #()
        local modNames = #()
        local modCounts = #()
        local rootNames = #()
        local layerNames = #()

        for obj in objects do (
            if obj.isHidden do hiddenCount += 1
            if obj.isFrozen do frozenCount += 1

            local cn = (classOf obj) as string
            local cidx = findItem classNames cn
            if cidx == 0 then (append classNames cn; append classCounts 1)
            else classCounts[cidx] += 1

            if obj.material != undefined do (
                local mn = obj.material.name
                local midx = findItem matNames mn
                if midx == 0 then (append matNames mn; append matCounts 1)
                else matCounts[midx] += 1
            )

            for m = 1 to obj.modifiers.count do (
                local modCls = (classOf obj.modifiers[m]) as string
                local modIdx = findItem modNames modCls
                if modIdx == 0 then (append modNames modCls; append modCounts 1)
                else modCounts[modIdx] += 1
            )

            if obj.parent == undefined do append rootNames obj.name

            local ln = obj.layer.name
            if (findItem layerNames ln) == 0 do append layerNames ln
        )

        -- Build class counts
        local classPairs = ""
        for i = 1 to classNames.count do (
            if i > 1 do classPairs += ","
            classPairs += "\"" + (esc classNames[i]) + "\":" + (classCounts[i] as string)
        )

        -- Build material summary
        local matPairs = ""
        for i = 1 to matNames.count do (
            if i > 1 do matPairs += ","
            matPairs += "\"" + (esc matNames[i]) + "\":" + (matCounts[i] as string)
        )

        -- Build modifier summary
        local modPairs = ""
        for i = 1 to modNames.count do (
            if i > 1 do modPairs += ","
            modPairs += "\"" + (esc modNames[i]) + "\":" + (modCounts[i] as string)
        )

        -- Build roots (capped)
        local rootArr = ""
        local rootCap = amin #(rootNames.count, """ + str(max_roots) + r""")
        for i = 1 to rootCap do (
            if i > 1 do rootArr += ","
            rootArr += "\"" + (esc rootNames[i]) + "\""
        )

        -- Build layers
        local layerArr = ""
        for i = 1 to layerNames.count do (
            if i > 1 do layerArr += ","
            layerArr += "\"" + (esc layerNames[i]) + "\""
        )

        "{\"objectCount\":" + (totalCount as string) + \
        ",\"classCounts\":{" + classPairs + "}" + \
        ",\"materials\":{" + matPairs + "}" + \
        ",\"modifiers\":{" + modPairs + "}" + \
        ",\"layers\":[" + layerArr + "]" + \
        ",\"hiddenCount\":" + (hiddenCount as string) + \
        ",\"frozenCount\":" + (frozenCount as string) + \
        ",\"roots\":[" + rootArr + "]" + \
        ",\"rootCount\":" + (rootNames.count as string) + \
        "}"
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "{}")


@mcp.tool()
def get_selection_snapshot(max_items: int = 50) -> str:
    """Compact info for each selected object: name, class, parent, material,"""
    if client.native_available:
        try:
            params = {}
            if max_items != 50:
                params["max_items"] = max_items
            response = client.send_command(
                json.dumps(params) if params else "",
                cmd_type="native:selection_snapshot",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    maxscript = r"""(
        local esc = MCP_Server.escapeJsonString
        local arr = ""
        local count = 0
        local cap = """ + str(max_items) + r"""
        local total = selection.count

        for obj in selection while count < cap do (
            if count > 0 do arr += ","
            count += 1

            local posStr = "[" + (obj.pos.x as string) + "," + \
                           (obj.pos.y as string) + "," + \
                           (obj.pos.z as string) + "]"

            local matField = if obj.material != undefined \
                then ("\"" + (esc obj.material.name) + "\"") else "null"

            local parentField = if obj.parent != undefined \
                then ("\"" + (esc obj.parent.name) + "\"") else "null"

            local modArr = ""
            for m = 1 to obj.modifiers.count do (
                if m > 1 do modArr += ","
                modArr += "\"" + (esc ((classOf obj.modifiers[m]) as string)) + "\""
            )

            local bboxStr = "null"
            try (
                local bb = nodeGetBoundingBox obj (matrix3 1)
                local bbMin = bb[1]
                local bbMax = bb[2]
                bboxStr = "[[" + (bbMin.x as string) + "," + (bbMin.y as string) + "," + \
                           (bbMin.z as string) + "],[" + (bbMax.x as string) + "," + \
                           (bbMax.y as string) + "," + (bbMax.z as string) + "]]"
            ) catch ()

            arr += "{\"name\":\"" + (esc obj.name) + "\"" + \
                   ",\"class\":\"" + (esc ((classOf obj) as string)) + "\"" + \
                   ",\"parent\":" + parentField + \
                   ",\"material\":" + matField + \
                   ",\"modifiers\":[" + modArr + "]" + \
                   ",\"pos\":" + posStr + \
                   ",\"bbox\":" + bboxStr + "}"
        )
        "{\"selected\":" + (total as string) + ",\"objects\":[" + arr + "]}"
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "{}")


def _capture_scene_state() -> dict:
    """Capture compact per-object state for delta comparison."""
    maxscript = r"""(
        local esc = MCP_Server.escapeJsonString
        local result = ""
        local count = 0
        for obj in objects do (
            if count > 0 do result += ","
            count += 1
            local posStr = "[" + (obj.pos.x as string) + "," + \
                           (obj.pos.y as string) + "," + \
                           (obj.pos.z as string) + "]"
            local matName = if obj.material != undefined then (esc obj.material.name) else ""
            local cn = esc ((classOf obj) as string)
            local hidden = if obj.isHidden then "true" else "false"
            result += "\"" + (esc obj.name) + "\":{" + \
                "\"c\":\"" + cn + "\"," + \
                "\"p\":" + posStr + "," + \
                "\"m\":\"" + matName + "\"," + \
                "\"n\":" + (obj.modifiers.count as string) + "," + \
                "\"h\":" + hidden + "}"
        )
        "{" + result + "}"
    )"""
    response = client.send_command(maxscript)
    return json.loads(response.get("result", "{}"))


def _round_pos(pos: list) -> list:
    return [round(v, 1) for v in pos]


def _diff_objects(prev: dict, curr: dict) -> dict:
    changes = {}
    if prev["c"] != curr["c"]:
        changes["class"] = {"from": prev["c"], "to": curr["c"]}
    if _round_pos(prev["p"]) != _round_pos(curr["p"]):
        changes["position"] = {"from": prev["p"], "to": curr["p"]}
    if prev["m"] != curr["m"]:
        changes["material"] = {"from": prev["m"] or None, "to": curr["m"] or None}
    if prev["n"] != curr["n"]:
        changes["modifierCount"] = {"from": prev["n"], "to": curr["n"]}
    if prev["h"] != curr["h"]:
        changes["hidden"] = {"from": prev["h"], "to": curr["h"]}
    return changes


@mcp.tool()
def get_scene_delta(capture: bool = False) -> str:
    """Track what changed in the scene since the last snapshot."""
    if client.native_available:
        try:
            params = {}
            if capture:
                params["capture"] = True
            response = client.send_command(
                json.dumps(params) if params else "",
                cmd_type="native:scene_delta",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    global _previous_snapshot
    current = _capture_scene_state()

    if _previous_snapshot is None or capture:
        _previous_snapshot = current
        return json.dumps({"baseline": True, "objectCount": len(current)})

    prev_names = set(_previous_snapshot.keys())
    curr_names = set(current.keys())

    added = [{"name": n, "class": current[n]["c"]} for n in sorted(curr_names - prev_names)]
    removed = [{"name": n, "class": _previous_snapshot[n]["c"]} for n in sorted(prev_names - curr_names)]

    modified = []
    for name in sorted(curr_names & prev_names):
        changes = _diff_objects(_previous_snapshot[name], current[name])
        if changes:
            modified.append({"name": name, **changes})

    _previous_snapshot = current

    return json.dumps({
        "added": added,
        "removed": removed,
        "modified": modified,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "total": len(current),
        },
    })
