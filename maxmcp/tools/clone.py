import json as _json
from typing import Optional

from ..coerce import FloatList, StrList
from ..server import mcp, client
from ..helpers.maxscript import safe_string
from ..helpers.spatial import build_clone_spatial_maxscript, enrich_spatial_payload


@mcp.tool()
def clone_objects(
    names: StrList,
    mode: str = "copy",
    offset: Optional[FloatList] = None,
) -> str:
    """Clone (copy/instance/reference) objects in the scene.

    Returns cloned names plus a spatial snapshot (bbox, pivot, groundContact) for each clone.
    """
    if client.native_available:
        try:
            params: dict = {"names": names, "mode": mode}
            if offset:
                params["offset"] = offset
            response = client.send_command(_json.dumps(params), cmd_type="native:clone_objects")
            raw = response.get("result", "")
            if raw:
                payload = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(payload, dict):
                    for node in payload.get("nodes", []):
                        if isinstance(node, dict):
                            enrich_spatial_payload(node, str(node.get("class", "")))
                    return _json.dumps(payload)
            return raw
        except RuntimeError:
            pass

    if offset is None:
        offset = [0.0, 0.0, 0.0]

    mode_map = {"copy": "#copy", "instance": "#instance", "reference": "#reference"}
    ms_mode = mode_map.get(mode, "#copy")
    name_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in names) + ")"

    maxscript = f"""(
        local nameList = {name_arr}
        local srcNodes = #()
        local notFound = #()
        for n in nameList do (
            local obj = getNodeByName n
            if obj != undefined then
                append srcNodes obj
            else
                append notFound n
        )
        if srcNodes.count == 0 then (
            "{{\\\"error\\\":\\\"No valid objects found to clone\\\"}}"
        ) else (
            local newNodes = #()
            maxOps.cloneNodes srcNodes cloneType:{ms_mode} newNodes:&newNodes
            local offsetVec = [{offset[0]},{offset[1]},{offset[2]}]
            for n in newNodes do move n offsetVec
            local cloneNames = for n in newNodes collect n.name
            local namesJson = "["
            for i = 1 to cloneNames.count do (
                if i > 1 do namesJson += ","
                namesJson += ("\\\"" + cloneNames[i] + "\\\"")
            )
            namesJson += "]"
            local notFoundJson = "["
            for i = 1 to notFound.count do (
                if i > 1 do notFoundJson += ","
                notFoundJson += ("\\\"" + notFound[i] + "\\\"")
            )
            notFoundJson += "]"
            "{{\\\"cloned\\\":" + namesJson + ",\\\"notFound\\\":" + notFoundJson + "}}"
        )
    )"""
    response = client.send_command(maxscript)
    raw = response.get("result", "")
    if not raw:
        return raw

    try:
        payload = _json.loads(raw)
    except (_json.JSONDecodeError, TypeError):
        return raw

    if payload.get("error"):
        return raw

    cloned = payload.get("cloned", [])
    if cloned:
        spatial_response = client.send_command(build_clone_spatial_maxscript(cloned))
        spatial_raw = spatial_response.get("result", "")
        if spatial_raw:
            try:
                spatial_data = _json.loads(spatial_raw)
                payload["nodes"] = spatial_data.get("nodes", [])
                payload["space"] = spatial_data.get("space", {})
                for node in payload.get("nodes", []):
                    if isinstance(node, dict):
                        enrich_spatial_payload(node, str(node.get("class", "")))
            except (_json.JSONDecodeError, TypeError):
                pass

    return _json.dumps(payload)
