import json as _json
import math
from typing import Optional

from ...core.coerce import FloatList, StrList
from ...server import mcp, client
from ...core.maxscript import node_by_name_expr, safe_string
from ._spatial import build_clone_spatial_maxscript, enrich_spatial_payload

# The bridge returns an uncaught MAXScript exception as this prefixed string.
_MAXSCRIPT_ERROR = "__MCP_MS_ERR__:"

# Max clones the outermost closed group around a member instead of the member,
# stacked on the original. Mirrors ClosedGroupAround in the native handler.
_CLOSED_GROUP_FUNCTIONS = r'''
fn mcpClosedGroup n = (
    local outermost = undefined
    local member = n
    while isGroupMember member and isValidNode member.parent do (
        member = member.parent
        if isGroupHead member and not isOpenGroupHead member do outermost = member
    )
    outermost
)
fn mcpClosedGroupError nodes = (
    local msg = ""
    for n in nodes while msg == "" do (
        local g = mcpClosedGroup n
        if g != undefined do msg = "Clone source '" + n.name + "' is in closed group '" + g.name + "'; Max would clone the whole group. Clone the group, or open it with manage_groups to clone the member alone."
    )
    msg
)
'''


@mcp.tool()
def clone_objects(
    names: StrList,
    mode: str = "copy",
    offset: Optional[FloatList] = None,
    count: int = 1,
) -> str:
    """Clone (copy/instance/reference) objects in the scene.

    count is the number of NEW copies per source (1-200), excluding the original.
    Each repetition uses offset * (1..count) from the original in world units.
    Example: count=14, offset=[60,0,0], mode="instance" builds a 15-beam row.
    Repeated arrays preserve hierarchies and run in one undo step.
    Returns actual cloned names and spatial snapshots; duplicate sources are ignored.
    """
    names = list(dict.fromkeys(names))
    if not names:
        raise ValueError("names is required")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 200:
        raise ValueError("count must be an integer from 1 to 200 (new copies per source)")
    mode = mode.strip().lower()
    if mode not in {"copy", "instance", "reference"}:
        raise ValueError("mode must be copy, instance, or reference")
    if offset is not None:
        if len(offset) != 3 or any(isinstance(v, bool) or not math.isfinite(float(v)) for v in offset):
            raise ValueError("offset must contain three finite numbers")
    if count > 1:
        return _clone_array(names, mode, list(offset or [0.0, 0.0, 0.0]), count)
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
        {_CLOSED_GROUP_FUNCTIONS}
        local nameList = {name_arr}
        local srcNodes = #()
        local notFound = #()
        -- A source name several objects share fails the call before anything is cloned.
        for n in nameList do (
            local obj = {node_by_name_expr("n", missing_ok=True)}
            if obj != undefined then
                append srcNodes obj
            else
                append notFound n
        )
        local groupError = mcpClosedGroupError srcNodes
        if srcNodes.count == 0 then (
            "{{\\\"error\\\":\\\"No valid objects found to clone\\\"}}"
        ) else if groupError != "" then (
            "{{\\\"error\\\":\\\"" + groupError + "\\\"}}"
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
            local handlesJson = "["
            for i = 1 to newNodes.count do (
                if i > 1 do handlesJson += ","
                handlesJson += formattedPrint ((getHandleByAnim newNodes[i]) as integer64) format:"d"
            )
            handlesJson += "]"
            "{{\\\"cloned\\\":" + namesJson + ",\\\"notFound\\\":" + notFoundJson + ",\\\"handles\\\":" + handlesJson + "}}"
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
        # Clones are read back by handle; a clone's name may also be carried
        # by another object.
        handles = [int(h) for h in payload.get("handles") or []]
        spatial_response = client.send_command(build_clone_spatial_maxscript(cloned, node_handles=handles or None))
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


def _clone_array(names: list[str], mode: str, offset: list[float], count: int) -> str:
    """One bridge call for the mutation, compatible with already installed bridges."""
    name_arr = "#(" + ",".join(f'"{safe_string(n)}"' for n in names) + ")"
    vec = "[" + ",".join(format(float(v), ".9g") for v in offset) + "]"
    # Keep the clone API's dependency/hierarchy mapping intact. Moving individual
    # nodes afterward would move parented descendants twice.
    script = f'''(
        {_CLOSED_GROUP_FUNCTIONS}
        local src = #()
        local missing = #()
        for nm in {name_arr} do (
            local hit = {node_by_name_expr("nm", missing_ok=True)}
            if hit == undefined then append missing nm else append src hit
        )
        local groupError = mcpClosedGroupError src
        if missing.count > 0 then ("__ERROR__|Sources must resolve uniquely: " + (missing as string))
        else if groupError != "" then ("__ERROR__|" + groupError)
        else (
            local made = #()
            local failure = ""
            undo "Clone array" on (
                try (
                    for step = 1 to {count} do (
                        local batch = #()
                        local ok = maxOps.cloneNodes src offset:({vec} * step) expandHierarchy:true cloneType:#{mode} newNodes:&batch
                        join made batch
                        if not ok do throw "CloneNodes failed"
                    )
                ) catch (
                    failure = getCurrentException() as string
                    for n in made where isValidNode n do delete n
                )
            )
            if failure != "" then ("__ERROR__|" + failure)
            else (
                local handles = for n in made collect (formattedPrint ((getHandleByAnim n) as integer64) format:"d")
                local out = ""
                for h in handles do out += h + ","
                out
            )
        )
    )'''
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    if raw.startswith(_MAXSCRIPT_ERROR):
        raise RuntimeError(raw[len(_MAXSCRIPT_ERROR):].strip())
    handles = [int(h) for h in raw.split(",") if h.strip()]
    if not handles:
        raise RuntimeError("Clone array returned no node handles")
    # Resolve by stable handles, not generated names. The helper then supplies
    # the same detailed spatial contract used by single clones.
    spatial_script = build_clone_spatial_maxscript([], node_handles=handles)
    response = client.send_command(spatial_script)
    payload = _json.loads(response.get("result", "{}"))
    nodes = payload.get("nodes", [])
    if len(nodes) != len(handles):
        raise RuntimeError("Clone array spatial readback is incomplete; inspect scene before retrying")
    for node in nodes:
        enrich_spatial_payload(node, str(node.get("class", "")))
    payload.update(cloned=[n["name"] for n in nodes], notFound=[], count=count, offset=offset)
    return _json.dumps(payload)
