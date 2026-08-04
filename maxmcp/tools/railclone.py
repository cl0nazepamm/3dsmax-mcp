from __future__ import annotations

import json
from typing import Any

from ..helpers.maxscript import safe_string

from ..server import client, mcp


def _decode(value: str) -> str:
    return value.replace("<pipe>", "|")


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_bool(value: str, default: bool = False) -> bool:
    lower = (value or "").strip().lower()
    if lower in {"true", "1", "yes", "on"}:
        return True
    if lower in {"false", "0", "no", "off"}:
        return False
    return default


def _parse_style_graph_lines(raw: str, fallback_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": fallback_name,
        "class": "",
        "style": "",
        "styleLength": 0,
        "styleDescLength": 0,
        "styleDesc": "",
        "baseCount": 0,
        "segmentCount": 0,
        "parameterCount": 0,
        "bases": [],
        "segments": [],
        "parameters": [],
        "graph": {"nodes": [], "edges": []},
        "warnings": [],
    }

    for line in raw.splitlines():
        parts = line.split("|")
        if not parts:
            continue
        tag = parts[0]

        if tag == "HDR" and len(parts) >= 6:
            result["name"] = _decode(parts[1])
            result["class"] = _decode(parts[2])
            result["style"] = _decode(parts[3])
            result["styleLength"] = _to_int(parts[4])
            result["styleDescLength"] = _to_int(parts[5])
        elif tag == "DESC" and len(parts) >= 2:
            result["styleDesc"] = _decode(parts[1])
        elif tag == "META" and len(parts) >= 3:
            key = parts[1]
            val = _to_int(parts[2])
            if key == "baseCount":
                result["baseCount"] = val
            elif key == "segmentCount":
                result["segmentCount"] = val
            elif key == "parameterCount":
                result["parameterCount"] = val
        elif tag == "BA" and len(parts) >= 10:
            result["bases"].append(
                {
                    "index": _to_int(parts[1]),
                    "id": _decode(parts[2]),
                    "type": _to_int(parts[3]),
                    "name": _decode(parts[4]),
                    "node": _decode(parts[5]),
                    "full": _to_bool(parts[6]),
                    "start": _to_float(parts[7]),
                    "length": _to_float(parts[8]),
                    "description": _decode(parts[9]),
                }
            )
        elif tag == "SG" and len(parts) >= 16:
            result["segments"].append(
                {
                    "index": _to_int(parts[1]),
                    "id": _decode(parts[2]),
                    "name": _decode(parts[3]),
                    "node": _decode(parts[4]),
                    "material": _to_int(parts[5]),
                    "materialRange": _to_int(parts[6]),
                    "renderable": _to_bool(parts[7]),
                    "bend": _to_bool(parts[8]),
                    "slice": _to_bool(parts[9]),
                    "nesting": _to_bool(parts[10]),
                    "sliceSourceIndex": _to_int(parts[11]),
                    "position": _decode(parts[12]),
                    "rotation": _decode(parts[13]),
                    "scale": _decode(parts[14]),
                    "mappingChannels": _decode(parts[15]),
                }
            )
        elif tag == "PA" and len(parts) >= 14:
            result["parameters"].append(
                {
                    "index": _to_int(parts[1]),
                    "id": _decode(parts[2]),
                    "name": _decode(parts[3]),
                    "type": _to_int(parts[4]),
                    "typeLabel": _decode(parts[5]),
                    "limited": _to_bool(parts[6]),
                    "value": _decode(parts[7]),
                    "min": _decode(parts[8]),
                    "max": _decode(parts[9]),
                    "selector": _decode(parts[10]),
                    "description": _decode(parts[11]),
                    "modified": _to_bool(parts[12]),
                    "retain": _to_int(parts[13]),
                }
            )
        elif tag == "WARN" and len(parts) >= 2:
            result["warnings"].append([_decode(p) for p in parts[1:]])

    if result["baseCount"] <= 0:
        result["baseCount"] = len(result["bases"])
    if result["segmentCount"] <= 0:
        result["segmentCount"] = len(result["segments"])
    if result["parameterCount"] <= 0:
        result["parameterCount"] = len(result["parameters"])

    root_id = f"railclone:{result['name']}"
    nodes = [{"id": root_id, "type": "railclone", "name": result["name"]}]
    edges = []

    base_id_by_index: dict[int, str] = {}
    for base in result["bases"]:
        node_id = f"base:{base.get('id') or base.get('index')}"
        base_id_by_index[int(base.get("index", 0))] = node_id
        nodes.append({"id": node_id, "type": "base", "name": base.get("name", ""), "node": base.get("node", "")})
        edges.append({"from": root_id, "to": node_id, "type": "has_base"})

    for segment in result["segments"]:
        node_id = f"segment:{segment.get('id') or segment.get('index')}"
        nodes.append({"id": node_id, "type": "segment", "name": segment.get("name", ""), "node": segment.get("node", "")})
        edges.append({"from": root_id, "to": node_id, "type": "has_segment"})
        src_index = int(segment.get("sliceSourceIndex", 0))
        if src_index > 0 and src_index in base_id_by_index:
            edges.append({"from": base_id_by_index[src_index], "to": node_id, "type": "base_to_segment", "via": "slicesrc"})

    for parameter in result["parameters"]:
        node_id = f"param:{parameter.get('id') or parameter.get('index')}"
        nodes.append({"id": node_id, "type": "parameter", "name": parameter.get("name", ""), "paramType": parameter.get("typeLabel", "")})
        edges.append({"from": root_id, "to": node_id, "type": "has_parameter"})

    result["graph"] = {"nodes": nodes, "edges": edges}
    has_style_desc_warning = any((item and item[0] == "STYLE_DESC_EMPTY") for item in result["warnings"])
    if result["styleDescLength"] == 0 and not has_style_desc_warning:
        result["warnings"].append(
            [
                "STYLE_DESC_EMPTY",
                "RailClone getStyleDesc() returned empty; graph is reconstructed from exposed arrays only.",
            ]
        )
    return result


@mcp.tool()
def get_railclone_style_graph(
    name: str,
    include_bases: bool = True,
    include_segments: bool = True,
    include_parameters: bool = True,
    include_raw_style_desc: bool = False,
    max_bases: int = 300,
    max_segments: int = 1000,
    max_parameters: int = 500,
    max_style_desc_chars: int = 4000,
) -> str:
    """Read RailClone style-editor graph data from exposed arrays/interfaces."""
    max_b = max(1, int(max_bases))
    max_s = max(1, int(max_segments))
    max_p = max(1, int(max_parameters))
    max_desc = max(1, int(max_style_desc_chars))

    maxscript = f"""(
fn clean s =
(
    local t = s as string
    t = substituteString t "|" "<pipe>"
    t = substituteString t "\\n" " "
    t = substituteString t "\\r" ""
    t
)

fn arrVal arr idx defaultVal =
(
    local v = defaultVal
    try (if arr != undefined and idx >= 1 and arr.count >= idx do v = arr[idx]) catch ()
    v
)

fn maxArrCount arr cur =
(
    local out = cur
    try (if arr != undefined and arr.count > out do out = arr.count) catch ()
    out
)

fn pTypeLabel t =
(
    case t of (
        0: "int"
        1: "float"
        2: "bool"
        3: "worldUnits"
        4: "string"
        default: ("type_" + (t as string))
    )
)

local n = getNodeByName "{safe_string(name)}"
if n == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    local cls = (classof n) as string
    if (findString (toLower cls) "railclone") == undefined then (
        "__ERROR__|Object is not RailClone: " + n.name + " (" + cls + ")"
    ) else (
        local style = ""
        try (style = n.style as string) catch ()
        local styleDesc = ""
        try (styleDesc = n.railclone.getStyleDesc()) catch ()
        local out = "HDR|" + (clean n.name) + "|" + (clean cls) + "|" + (clean style) + "|" + (style.count as string) + "|" + (styleDesc.count as string) + "\\n"

        if {str(bool(include_raw_style_desc)).lower()} then (
            local d = styleDesc
            if d.count > {max_desc} do d = (substring d 1 {max_desc})
            out += "DESC|" + (clean d) + "\\n"
            if styleDesc.count > d.count do out += "WARN|DESC_TRUNCATED|" + (styleDesc.count as string) + "|" + (d.count as string) + "\\n"
        )

        local baseCount = 0
        baseCount = maxArrCount n.baid baseCount
        baseCount = maxArrCount n.batype baseCount
        baseCount = maxArrCount n.baname baseCount
        baseCount = maxArrCount n.banode baseCount
        baseCount = maxArrCount n.bafull baseCount
        baseCount = maxArrCount n.bastart baseCount
        baseCount = maxArrCount n.balength baseCount
        baseCount = maxArrCount n.badesc baseCount
        out += "META|baseCount|" + (baseCount as string) + "\\n"

        if {str(bool(include_bases)).lower()} then (
            local bTake = baseCount
            if bTake > {max_b} then (
                out += "WARN|BASE_TRUNCATED|" + (baseCount as string) + "|" + ({max_b} as string) + "\\n"
                bTake = {max_b}
            )
            for i = 1 to bTake do (
                local bId = arrVal n.baid i ""
                local bType = arrVal n.batype i 0
                local bName = arrVal n.baname i ""
                local bNode = arrVal n.banode i undefined
                local bNodeName = if bNode != undefined then bNode.name else ""
                local bFull = arrVal n.bafull i false
                local bStart = arrVal n.bastart i 0.0
                local bLength = arrVal n.balength i 0.0
                local bDesc = arrVal n.badesc i ""
                out += "BA|" + (i as string) + "|" + (clean bId) + "|" + (bType as string) + "|" + (clean bName) + "|" + (clean bNodeName) + "|" + (bFull as string) + "|" + (bStart as string) + "|" + (bLength as string) + "|" + (clean bDesc) + "\\n"
            )
        )

        local segCount = 0
        segCount = maxArrCount n.sid segCount
        segCount = maxArrCount n.sname segCount
        segCount = maxArrCount n.sobjnode segCount
        segCount = maxArrCount n.smaterial segCount
        segCount = maxArrCount n.smatrange segCount
        segCount = maxArrCount n.srenderable segCount
        segCount = maxArrCount n.sbend segCount
        segCount = maxArrCount n.sslice segCount
        segCount = maxArrCount n.snesting segCount
        segCount = maxArrCount n.slicesrc segCount
        segCount = maxArrCount n.spos segCount
        segCount = maxArrCount n.srot segCount
        segCount = maxArrCount n.ssca segCount
        segCount = maxArrCount n.smapchans segCount
        out += "META|segmentCount|" + (segCount as string) + "\\n"

        if {str(bool(include_segments)).lower()} then (
            local sTake = segCount
            if sTake > {max_s} then (
                out += "WARN|SEGMENT_TRUNCATED|" + (segCount as string) + "|" + ({max_s} as string) + "\\n"
                sTake = {max_s}
            )
            for i = 1 to sTake do (
                local sId = arrVal n.sid i ""
                local sName = arrVal n.sname i ""
                local sNode = arrVal n.sobjnode i undefined
                local sNodeName = if sNode != undefined then sNode.name else ""
                local sMaterial = arrVal n.smaterial i 0
                local sMatRange = arrVal n.smatrange i 1
                local sRenderable = arrVal n.srenderable i true
                local sBend = arrVal n.sbend i false
                local sSlice = arrVal n.sslice i false
                local sNesting = arrVal n.snesting i false
                local sSliceSrc = arrVal n.slicesrc i 0
                local sPos = arrVal n.spos i [0,0,0]
                local sRot = arrVal n.srot i [0,0,0]
                local sScale = arrVal n.ssca i [100,100,100]
                local sMapChans = arrVal n.smapchans i ""
                out += "SG|" + (i as string) + "|" + (clean sId) + "|" + (clean sName) + "|" + (clean sNodeName) + "|" + (sMaterial as string) + "|" + (sMatRange as string) + "|" + (sRenderable as string) + "|" + (sBend as string) + "|" + (sSlice as string) + "|" + (sNesting as string) + "|" + (sSliceSrc as string) + "|" + (clean (sPos as string)) + "|" + (clean (sRot as string)) + "|" + (clean (sScale as string)) + "|" + (clean sMapChans) + "\\n"
            )
        )

        local pCount = 0
        pCount = maxArrCount n.paid pCount
        pCount = maxArrCount n.patype pCount
        pCount = maxArrCount n.paname pCount
        pCount = maxArrCount n.palimit pCount
        pCount = maxArrCount n.paintval pCount
        pCount = maxArrCount n.paintmin pCount
        pCount = maxArrCount n.paintmax pCount
        pCount = maxArrCount n.pafloatval pCount
        pCount = maxArrCount n.pafloatmin pCount
        pCount = maxArrCount n.pafloatmax pCount
        pCount = maxArrCount n.paunitval pCount
        pCount = maxArrCount n.paunitmin pCount
        pCount = maxArrCount n.paunitmax pCount
        pCount = maxArrCount n.paboolval pCount
        pCount = maxArrCount n.pastrval pCount
        pCount = maxArrCount n.paselector pCount
        pCount = maxArrCount n.padesc pCount
        pCount = maxArrCount n.pamodified pCount
        pCount = maxArrCount n.paretain pCount
        out += "META|parameterCount|" + (pCount as string) + "\\n"

        if {str(bool(include_parameters)).lower()} then (
            local pTake = pCount
            if pTake > {max_p} then (
                out += "WARN|PARAM_TRUNCATED|" + (pCount as string) + "|" + ({max_p} as string) + "\\n"
                pTake = {max_p}
            )
            for i = 1 to pTake do (
                local pId = arrVal n.paid i ""
                local pName = arrVal n.paname i ""
                local pType = arrVal n.patype i -1
                local pTypeName = pTypeLabel pType
                local pLimit = arrVal n.palimit i false
                local pSel = arrVal n.paselector i ""
                local pDesc = arrVal n.padesc i ""
                local pModified = arrVal n.pamodified i false
                local pRetain = arrVal n.paretain i 0

                local pVal = ""
                local pMin = ""
                local pMax = ""
                case pType of (
                    0: (
                        pVal = (arrVal n.paintval i 0) as string
                        pMin = (arrVal n.paintmin i 0) as string
                        pMax = (arrVal n.paintmax i 0) as string
                    )
                    1: (
                        pVal = (arrVal n.pafloatval i 0.0) as string
                        pMin = (arrVal n.pafloatmin i 0.0) as string
                        pMax = (arrVal n.pafloatmax i 0.0) as string
                    )
                    3: (
                        pVal = (arrVal n.paunitval i 0.0) as string
                        pMin = (arrVal n.paunitmin i 0.0) as string
                        pMax = (arrVal n.paunitmax i 0.0) as string
                    )
                    default: (
                        local bVal = arrVal n.paboolval i undefined
                        if bVal != undefined then (
                            pVal = bVal as string
                        ) else (
                            local sVal = arrVal n.pastrval i undefined
                            if sVal != undefined then pVal = sVal as string
                        )
                    )
                )

                out += "PA|" + (i as string) + "|" + (clean pId) + "|" + (clean pName) + "|" + (pType as string) + "|" + (clean pTypeName) + "|" + (pLimit as string) + "|" + (clean pVal) + "|" + (clean pMin) + "|" + (clean pMax) + "|" + (clean pSel) + "|" + (clean pDesc) + "|" + (pModified as string) + "|" + (pRetain as string) + "\\n"
            )
        )

        out
    )
)
)"""

    try:
        response = client.send_command(maxscript)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return json.dumps({"error": raw.split("|", 1)[1]})
    return json.dumps(_parse_style_graph_lines(raw, fallback_name=name))
