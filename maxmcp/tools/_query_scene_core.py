"""Shared scene query implementations for query_scene and legacy tool wrappers."""

from __future__ import annotations

import json

from ..helpers.maxscript import safe_string
from ..helpers.scene_reads import (
    fetch_class_instances_native,
    fetch_scene_snapshot,
    scene_info_summary_from_snapshot,
)
from ..max_client import MaxClient

_previous_snapshot: dict | None = None

VALID_ACTIONS = frozenset({"overview", "filter", "class", "property", "selection", "delta"})


def normalize_action(action: str) -> str:
    return (action or "").strip().lower()


def dispatch_query_scene(client: MaxClient, action: str, **params: object) -> str:
    act = normalize_action(action)
    if act not in VALID_ACTIONS:
        return json.dumps({
            "error": f"Unknown action: {action}. Use overview, filter, class, property, selection, or delta.",
        })

    if act == "overview":
        return run_overview(client, int(params.get("max_roots", 50)))
    if act == "filter":
        return run_filter(
            client,
            class_name=str(params.get("class_name", "") or ""),
            pattern=str(params.get("pattern", "") or ""),
            layer=str(params.get("layer", "") or ""),
            limit=int(params.get("limit", 100)),
            offset=int(params.get("offset", 0)),
            roots_only=bool(params.get("roots_only", False)),
        )
    if act == "class":
        return run_class_instances(
            client,
            class_name=str(params.get("class_name", "") or ""),
            superclass=str(params.get("superclass", "") or ""),
            scope=str(params.get("scope", "auto") or "auto"),
            limit=int(params.get("limit", 100)),
        )
    if act == "property":
        return run_find_by_property(
            client,
            property_name=str(params.get("property_name", "") or ""),
            property_value=str(params.get("property_value", "") or ""),
            class_filter=str(params.get("class_filter", "") or ""),
        )
    if act == "selection":
        return run_selection(
            client,
            detail=str(params.get("detail", "full") or "full"),
            max_items=int(params.get("max_items", 50)),
        )
    return run_delta(
        client,
        capture=bool(params.get("capture", False)),
        unchanged_since=int(params.get("unchanged_since", 0) or 0),
    )


def run_overview(client: MaxClient, max_roots: int = 50) -> str:
    if client.native_available:
        try:
            payload: dict[str, object] = {}
            if max_roots != 50:
                payload["max_roots"] = max_roots
            response = client.send_command(
                json.dumps(payload) if payload else "",
                cmd_type="native:scene_snapshot",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    maxscript = _overview_maxscript(max_roots)
    response = client.send_command(maxscript)
    return response.get("result", "{}")


def run_filter(
    client: MaxClient,
    class_name: str = "",
    pattern: str = "",
    layer: str = "",
    limit: int = 100,
    offset: int = 0,
    roots_only: bool = False,
) -> str:
    if client.native_available:
        try:
            has_filter = class_name or pattern or layer or roots_only or offset
            if not has_filter:
                snapshot = fetch_scene_snapshot(client)
                return json.dumps(scene_info_summary_from_snapshot(snapshot))

            params: dict[str, object] = {}
            if class_name:
                params["class_name"] = class_name
            if pattern:
                params["pattern"] = pattern
            if layer:
                params["layer"] = layer
            if roots_only:
                params["roots_only"] = True
            if limit != 100:
                params["limit"] = limit
            if offset:
                params["offset"] = offset
            response = client.send_command(json.dumps(params), cmd_type="native:scene_info")
            return response.get("result", "{}")
        except RuntimeError:
            pass

    has_filter = class_name or pattern or layer or roots_only
    if not has_filter and offset == 0:
        response = client.send_command(_filter_summary_maxscript())
        return response.get("result", "{}")

    response = client.send_command(
        _filter_list_maxscript(class_name, pattern, layer, roots_only, limit, offset)
    )
    return response.get("result", '{"totalMatched":0,"objects":[]}')


def run_selection(client: MaxClient, detail: str = "full", max_items: int = 50) -> str:
    normalized = (detail or "full").strip().lower()
    if normalized in {"compact", "lite", "basic"}:
        if client.native_available:
            try:
                response = client.send_command("", cmd_type="native:selection")
                return response.get("result", "[]")
            except RuntimeError:
                pass
        response = client.send_command(_selection_compact_maxscript())
        return response.get("result", "[]")

    if client.native_available:
        try:
            payload: dict[str, object] = {}
            if max_items != 50:
                payload["max_items"] = max_items
            response = client.send_command(
                json.dumps(payload) if payload else "",
                cmd_type="native:selection_snapshot",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    response = client.send_command(_selection_full_maxscript(max_items))
    return response.get("result", "{}")


def run_delta(client: MaxClient, capture: bool = False, unchanged_since: int = 0) -> str:
    if client.native_available:
        try:
            payload: dict[str, object] = {}
            if capture:
                payload["capture"] = True
            if unchanged_since > 0:
                payload["unchanged_since"] = unchanged_since
            response = client.send_command(
                json.dumps(payload) if payload else "",
                cmd_type="native:scene_delta",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    global _previous_snapshot
    current = _capture_scene_state(client)

    if _previous_snapshot is None or capture:
        _previous_snapshot = current
        return json.dumps({"baseline": True, "objectCount": len(current)})

    prev_handles = set(_previous_snapshot.keys())
    curr_handles = set(current.keys())

    added = sorted(
        ({"name": current[h]["name"], "class": current[h]["c"]} for h in curr_handles - prev_handles),
        key=lambda o: o["name"],
    )
    removed = sorted(
        ({"name": _previous_snapshot[h]["name"], "class": _previous_snapshot[h]["c"]} for h in prev_handles - curr_handles),
        key=lambda o: o["name"],
    )

    modified = []
    for h in curr_handles & prev_handles:
        changes = _diff_objects(_previous_snapshot[h], current[h])
        if changes:
            modified.append({"name": current[h]["name"], **changes})
    modified.sort(key=lambda o: o["name"])

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


def run_class_instances(
    client: MaxClient,
    class_name: str,
    superclass: str = "",
    scope: str = "auto",
    limit: int = 100,
) -> str:
    if not class_name and not superclass:
        return json.dumps({"error": "class_name is required unless superclass is set"})

    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope not in {"auto", "nodes", "refs"}:
        return json.dumps({"error": "scope must be auto, nodes, or refs"})

    if superclass:
        normalized_scope = "refs"

    if normalized_scope in {"auto", "nodes"} and client.native_available and not superclass:
        try:
            native = fetch_class_instances_native(client, class_name, limit=limit)
            total = int(native.get("totalFound", 0))
            if normalized_scope == "nodes" or total > 0:
                return json.dumps(native)
        except RuntimeError:
            if normalized_scope == "nodes":
                raise

    if normalized_scope == "nodes":
        return json.dumps({
            "className": class_name,
            "totalFound": 0,
            "instances": [],
            "hint": "No scene nodes matched. Retry with scope='refs' for materials/maps/modifiers.",
        })

    response = client.send_command(
        _class_instances_refs_maxscript(class_name, superclass, limit)
    )
    return response.get("result", "")


def run_find_by_property(
    client: MaxClient,
    property_name: str,
    property_value: str = "",
    class_filter: str = "",
) -> str:
    if not property_name:
        return json.dumps({"error": "property_name is required"})

    if client.native_available:
        try:
            payload = {
                "property_name": property_name,
                "property_value": property_value,
                "class_filter": class_filter,
            }
            response = client.send_command(
                json.dumps(payload),
                cmd_type="native:find_objects_by_property",
            )
            return response.get("result", "[]")
        except RuntimeError:
            pass

    response = client.send_command(
        _find_by_property_maxscript(property_name, property_value, class_filter)
    )
    return response.get("result", "[]")


def _overview_maxscript(max_roots: int) -> str:
    return r"""(
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

        local classPairs = ""
        for i = 1 to classNames.count do (
            if i > 1 do classPairs += ","
            classPairs += "\"" + (esc classNames[i]) + "\":" + (classCounts[i] as string)
        )

        local matPairs = ""
        for i = 1 to matNames.count do (
            if i > 1 do matPairs += ","
            matPairs += "\"" + (esc matNames[i]) + "\":" + (matCounts[i] as string)
        )

        local modPairs = ""
        for i = 1 to modNames.count do (
            if i > 1 do modPairs += ","
            modPairs += "\"" + (esc modNames[i]) + "\":" + (modCounts[i] as string)
        )

        local rootArr = ""
        local rootCap = amin #(rootNames.count, """ + str(max_roots) + r""")
        for i = 1 to rootCap do (
            if i > 1 do rootArr += ","
            rootArr += "\"" + (esc rootNames[i]) + "\""
        )

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


def _filter_summary_maxscript() -> str:
    return r"""(
        local esc = MCP_Server.escapeJsonString
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
            classPairs += "\"" + (esc classNames[i]) + "\":" + (classMap[i] as string)
        )
        local layerList = ""
        for i = 1 to layerNames.count do (
            if i > 1 do layerList += ","
            layerList += "\"" + (esc layerNames[i]) + "\""
        )
        "{\"totalObjects\":" + (totalCount as string) + \
        ",\"classCounts\":{" + classPairs + "}" + \
        ",\"layers\":[" + layerList + "]" + \
        ",\"hiddenCount\":" + (hiddenCount as string) + \
        ",\"frozenCount\":" + (frozenCount as string) + "}"
    )"""


def _filter_list_maxscript(
    class_name: str,
    pattern: str,
    layer: str,
    roots_only: bool,
    limit: int,
    offset: int,
) -> str:
    conditions = []
    if class_name:
        conditions.append(f'((classOf obj) as string) == "{class_name.replace(chr(34), "")}"')
    if pattern:
        safe_pattern = pattern.replace('"', "").replace("\\", "\\\\")
        conditions.append(f'matchPattern obj.name pattern:"{safe_pattern}"')
    if layer:
        conditions.append(f'obj.layer.name == "{layer.replace(chr(34), "")}"')
    if roots_only:
        conditions.append("obj.parent == undefined")
    filter_expr = " and ".join(conditions) if conditions else "true"

    return (
        "(\n"
        "    local esc = MCP_Server.escapeJsonString\n"
        "    local matched = #()\n"
        f"    for obj in objects where ({filter_expr}) do append matched obj\n"
        "    local totalMatched = matched.count\n"
        f"    local startIdx = {offset + 1}\n"
        f"    local endIdx = amin #(matched.count, {offset + limit})\n"
        "    local arr = #()\n"
        "    for i = startIdx to endIdx do (\n"
        "        local obj = matched[i]\n"
        "        local posStr = \"[\" + (obj.pos.x as string) + \",\" + \\\n"
        "                       (obj.pos.y as string) + \",\" + \\\n"
        "                       (obj.pos.z as string) + \"]\"\n"
        '        local parentName = if obj.parent != undefined then obj.parent.name else ""\n'
        '        local parentField = if parentName == "" then "null" else ("\\"" + (esc parentName) + "\\"") \n'
        "        local entry = \"{\" + \\\n"
        '            "\\"name\\":\\"" + (esc obj.name) + "\\"," + \\\n'
        '            "\\"class\\":\\"" + (esc ((classOf obj) as string)) + "\\"," + \\\n'
        '            "\\"position\\":" + posStr + "," + \\\n'
        '            "\\"parent\\":" + parentField + "," + \\\n'
        '            "\\"numChildren\\":" + (obj.children.count as string) + "," + \\\n'
        '            "\\"isHidden\\":" + (if obj.isHidden then "true" else "false") + "," + \\\n'
        '            "\\"isFrozen\\":" + (if obj.isFrozen then "true" else "false") + "," + \\\n'
        '            "\\"layer\\":\\"" + (esc obj.layer.name) + "\\"" + \\\n'
        '        "}"\n'
        "        append arr entry\n"
        "    )\n"
        '    local result = "{\\"totalMatched\\":" + (totalMatched as string) + ",\\"objects\\":["\n'
        "    for i = 1 to arr.count do (\n"
        "        if i > 1 do result += \",\"\n"
        "        result += arr[i]\n"
        "    )\n"
        '    result += "]}"\n'
        "    result\n"
        ")\n"
    )


def _selection_compact_maxscript() -> str:
    return r"""(
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


def _selection_full_maxscript(max_items: int) -> str:
    return (
        r"""(
        local esc = MCP_Server.escapeJsonString
        local arr = ""
        local count = 0
        local cap = """
        + str(max_items)
        + r"""
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
    )


def _capture_scene_state(client: MaxClient) -> dict:
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
            -- Key by node handle, not name: Max names aren't unique, and a name key
            -- would collapse duplicate-named nodes into one entry (last wins).
            local hkey = (getHandleByAnim obj) as string
            result += "\"" + hkey + "\":{" + \
                "\"name\":\"" + (esc obj.name) + "\"," + \
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


def _class_instances_refs_maxscript(class_name: str, superclass: str, limit: int) -> str:
    max_show = min(limit, 50)
    if superclass:
        safe_sc = safe_string(superclass)
        return f"""(
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

    safe_cls = safe_string(class_name)
    return f"""(
        local cls = execute "{safe_cls}"
        if cls == undefined then (
            "{{\\\"error\\\": \\\"Unknown class: {safe_cls}\\\"}}"
        ) else (
            local insts = getclassinstances cls
            local result = "{{\\\"class\\\": \\\"" + (cls as string) + "\\\", \\\"count\\\": " + (insts.count as string) + ", \\\"instances\\\": ["
            local maxShow = amin #(insts.count, {max_show})
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


def _find_by_property_maxscript(
    property_name: str,
    property_value: str,
    class_filter: str,
) -> str:
    safe_prop = safe_string(property_name)
    safe_val = safe_string(property_value)
    class_cond = ""
    if class_filter:
        safe_class = safe_string(class_filter)
        class_cond = f'and (matchPattern ((classof obj) as string) pattern:"*{safe_class}*")'

    if property_value:
        return f"""(
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

    return f"""(
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
