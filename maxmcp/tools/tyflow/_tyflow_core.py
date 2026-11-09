"""Shared tyFlow graph-core helpers: scene graph, structural hash, graph read.

tyFlow has no getter for its wiring or operator types. The bridge reads them
from the flow's own save stream (native:tyflow_graph_stream, decoded by
_tyflow_stream.py), so edges and types are always the flow's current
state, including edits made in the tyFlow editor.

Bridges without that handler fall back to the shadow ledger: every
connect/disconnect made through MCP tools is recorded as JSON AppData on the
flow node, and staleness is detected by comparing a structural hash of what
MAXScript can read against the hash stored at the last MCP mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ...core.maxscript import node_by_name, safe_string
from ._tyflow_catalog import TYFLOW_OPERATOR_TYPE_IDS
from ._tyflow_stream import StreamError, decode_base64
from ...core.max_client import MaxBridgeError


def _client():
    """Lazy bridge-client accessor: keeps this module importable without
    triggering server tool registration (avoids circular imports)."""
    from ...server import client

    return client


LEDGER_APPDATA_ID = 1415075927
LEDGER_VERSION = 1
# The bridge returns an uncaught MAXScript exception as this prefixed string.
MAXSCRIPT_ERROR = "__MCP_MS_ERR__:"

# tyFlow 2.101 adds operators from MAXScript only to a flow whose editor is open. Each
# flow's editor is a top-level window titled "<flow name> : [tyFlow <edition> Editor]"
# (live, 2.101 on Max 2027); tyFlow has no query for it, so the title tells whether the
# user already had it open, and only an editor these tools opened is closed again.
EDITOR_HELPERS = """
fn mcpTyEditorIsOpen flowNode =
(
    local prefix = flowNode.name + " : ["
    local open = false
    for w in (windows.getChildrenHWND 0) where w[5] != undefined do (
        if not open and (findString w[5] prefix) == 1 and (findString w[5] "tyFlow") != undefined do open = true
    )
    open
)

fn mcpTyEditorOpen flowNode =
(
    -- Opens the flow's editor; returns whether it was open already.
    local wasOpen = mcpTyEditorIsOpen flowNode
    if not wasOpen do (try (flowNode.tyFlow.editor_open()) catch ())
    wasOpen
)

fn mcpTyEditorRestore flowNode wasOpen =
(
    if not wasOpen do (try (flowNode.tyFlow.editor_close()) catch ())
)
"""

# Shared MAXScript helpers (esc/jsonStringArray/find*SubAnim come from tyflow.HELPERS
# where needed; this block is standalone for core reads).
CORE_HELPERS = EDITOR_HELPERS + """
fn mcpTyParseSubNames targetObj =
(
    local names = #()
    local ss = stringstream ""
    try (showProperties targetObj to:ss) catch ()
    seek ss 0
    while not eof ss do (
        local line = trimRight (trimLeft (readline ss))
        if line.count > 1 and (substring line 1 1) == "." then (
            local rawName = trimRight (trimLeft (substring line 2 (line.count - 1)))
            if (findString rawName ":") == undefined and rawName != "" do append names rawName
        )
    )
    names
)

fn mcpTyClean s =
(
    local t = s as string
    t = substituteString t "|" "<pipe>"
    t = substituteString t "\\n" " "
    t = substituteString t "\\r" ""
    t
)

fn mcpTySubAnimByName parent childName =
(
    -- tyFlow's dynamic dispatch exposes spaced names underscored (Send Out -> .Send_Out):
    -- try the name as given, then the underscored form.
    local sub = undefined
    local sym = undefined
    try (sym = execute ("#'" + childName + "'")) catch ()
    if sym != undefined do (try (sub = parent[sym]) catch ())
    if sub == undefined do (
        local alt = substituteString childName " " "_"
        if alt != childName do (
            local sym2 = undefined
            try (sym2 = execute ("#'" + alt + "'")) catch ()
            if sym2 != undefined do (try (sub = parent[sym2]) catch ())
        )
    )
    sub
)
"""


def canon_name(name: Any) -> str:
    """Canonical tyFlow lookup name: spaces become underscores (matches the
    property-dispatch form that graph reads return)."""
    return str(name).replace(" ", "_")


# ── Scene graph (the flow's save stream) ──────────────────────────

class SceneGraphUnavailable(Exception):
    """The bridge cannot read the flow's graph stream (older bridge)."""


_harvested_types: tuple[float, dict[int, str]] = (0.0, {})


def _harvested_type_ids() -> dict[int, str]:
    """Type IDs harvest_tyflow_manifest recorded beyond the built-in table."""
    global _harvested_types
    root = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "3dsmax-mcp"
    try:
        newest = max(root.glob("tyflow_manifest_*.json"), key=lambda p: p.stat().st_mtime)
        mtime = newest.stat().st_mtime
    except (OSError, ValueError):
        return {}
    if mtime == _harvested_types[0]:
        return _harvested_types[1]
    found: dict[int, str] = {}
    try:
        data = json.loads(newest.read_text(encoding="utf-8"))
        for entry in data.get("operators") or []:
            if isinstance(entry, dict) and isinstance(entry.get("type_id"), int):
                found[entry["type_id"]] = str(entry.get("type"))
    except (OSError, ValueError, AttributeError):
        found = {}
    _harvested_types = (mtime, found)
    return found


def operator_type_name(type_id: int | None) -> str | None:
    """Operator type for a saved type ID; unknown IDs read as their hex value."""
    if type_id is None:
        return None
    return TYFLOW_OPERATOR_TYPE_IDS.get(type_id) or _harvested_type_ids().get(type_id) or f"0x{type_id:08X}"


def scene_graph(name: str) -> dict[str, Any]:
    """Events, operators (type, enabled, anim handle) and edges of the flow as saved.

    Raises SceneGraphUnavailable when the bridge has no stream handler; other
    bridge errors propagate.
    """
    try:
        response = _client().send_command(
            json.dumps({"name": name}), cmd_type="native:tyflow_graph_stream"
        )
    except MaxBridgeError as exc:
        if "unknown command type" in str(getattr(exc, "bridge_message", exc)).lower():
            raise SceneGraphUnavailable(
                "this 3ds Max bridge cannot read tyFlow wiring; update 3dsmax-mcp"
            ) from exc
        raise
    payload = json.loads(response.get("result") or "{}")
    if not isinstance(payload, dict) or "stream" not in payload:
        raise SceneGraphUnavailable("the bridge returned no tyFlow graph stream")
    try:
        decoded = decode_base64(str(payload["stream"]))
    except StreamError as exc:
        raise SceneGraphUnavailable(f"the tyFlow graph stream could not be decoded: {exc}") from exc
    return merge_scene(payload.get("events") or [], decoded)


def merge_scene(native_events: list[dict[str, Any]], decoded: dict[str, Any]) -> dict[str, Any]:
    """Join the bridge's sub-anim walk (handles, interface access) with the
    decoded stream (uids, types, wiring). Both list events and operators in
    editor order; names are checked so a mismatch is reported, not guessed."""
    warnings: list[str] = []
    saved = decoded.get("events") or []
    if len(saved) != len(native_events) or any(
        canon_name(s.get("name")) != canon_name(n.get("name")) for s, n in zip(saved, native_events)
    ):
        warnings.append("Saved events do not line up with the flow's events; handles were left out.")
        native_events = []
    events: list[dict[str, Any]] = []
    op_index: dict[int, tuple[str, str]] = {}
    event_names: dict[int, str] = {}
    for position, event in enumerate(saved):
        live = native_events[position] if native_events else {}
        live_ops = live.get("operators") or []
        ops_saved = event.get("operators") or []
        ops_match = len(live_ops) == len(ops_saved) and all(
            canon_name(s.get("name")) == canon_name(n.get("name")) for s, n in zip(ops_saved, live_ops)
        )
        if live and not ops_match:
            warnings.append(f"Operators of {event.get('name')} do not line up; their handles were left out.")
        ev_name = canon_name(event.get("name"))
        operators = []
        for i, op in enumerate(ops_saved):
            entry = {
                "name": canon_name(op.get("name")),
                "type": operator_type_name(op.get("type_id")),
                "type_id": op.get("type_id"),
                "enabled": op.get("enabled"),
                "uid": op.get("uid"),
            }
            if ops_match:
                entry["anim_handle"] = live_ops[i].get("anim_handle")
                entry["operator_interface"] = live_ops[i].get("operator_interface")
            operators.append(entry)
            if op.get("uid") is not None:
                op_index[op["uid"]] = (ev_name, entry["name"])
        if event.get("uid") is not None:
            event_names[event["uid"]] = ev_name
        events.append({
            "name": ev_name,
            "uid": event.get("uid"),
            "position": event.get("position"),
            "width": event.get("width"),
            "enabled": event.get("enabled"),
            "anim_handle": live.get("anim_handle"),
            "operators": operators,
        })
    edges = []
    for source, target in decoded.get("connections") or []:
        if source not in op_index or target not in event_names:
            warnings.append(f"A saved connection names an unknown operator or event ({source:#x} -> {target:#x}).")
            continue
        from_event, from_op = op_index[source]
        edges.append({"from_event": from_event, "from_op": from_op, "to_event": event_names[target]})
    return {"events": events, "edges": edges, "warnings": warnings}


def find_scene_operator(scene: dict[str, Any], event: str, operator: str) -> dict[str, Any]:
    """The one operator named `operator` in `event`; raises ValueError otherwise."""
    ev_name, op_name = canon_name(event), canon_name(operator)
    matches = [ev for ev in scene.get("events") or [] if ev["name"] == ev_name]
    if not matches:
        raise ValueError(f"Event not found: {event}")
    ops = [op for op in matches[0]["operators"] if op["name"] == op_name]
    if not ops:
        raise ValueError(f"Operator not found: {operator} in {event}")
    if len(ops) > 1:
        raise ValueError(f"{len(ops)} operators in {event} are named {operator}; rename one first")
    if not ops[0].get("anim_handle"):
        raise ValueError(f"Operator {operator} in {event} could not be resolved; read the graph again")
    return ops[0]


def operator_call(name: str, operator_handle: int, function: str, *,
                  value: Any = None, event_handle: int | None = None) -> Any:
    """Call one Operator interface function natively, including on operators
    whose own interface MAXScript cannot reach. Returns the function's result."""
    payload: dict[str, Any] = {"name": name, "operator_handle": int(operator_handle), "function": function}
    if value is not None:
        payload["value"] = value
    if event_handle is not None:
        payload["event_handle"] = int(event_handle)
    try:
        response = _client().send_command(json.dumps(payload), cmd_type="native:tyflow_operator")
    except MaxBridgeError as exc:
        if "unknown command type" in str(getattr(exc, "bridge_message", exc)).lower():
            raise SceneGraphUnavailable(
                "this 3ds Max bridge cannot reach this operator's interface; update 3dsmax-mcp"
            ) from exc
        raise
    result = json.loads(response.get("result") or "{}")
    return result.get("result") if isinstance(result, dict) else None


def parse_ledger(raw: Any) -> dict[str, Any]:
    """Defensive ledger parse; any garbage yields an empty valid ledger."""
    base: dict[str, Any] = {"v": LEDGER_VERSION, "hash": "", "edges": [], "ops": {}}
    if not raw or not isinstance(raw, str):
        return base
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return base
    if not isinstance(data, dict):
        return base
    base["hash"] = str(data.get("hash") or "")
    edges = data.get("edges")
    if isinstance(edges, list):
        base["edges"] = [
            {
                "from_event": canon_name(edge.get("from_event", "")),
                "from_op": canon_name(edge.get("from_op", "")),
                "to_event": canon_name(edge.get("to_event", "")),
            }
            for edge in edges
            if isinstance(edge, dict)
        ]
    ops = data.get("ops")
    if isinstance(ops, dict):
        base["ops"] = {canon_name(key): str(value) for key, value in ops.items()}
    return base


def ledger_to_mxs_literal(ledger: dict[str, Any]) -> str:
    """Compact ledger JSON as a double-quoted MAXScript string literal."""
    compact = json.dumps(ledger, separators=(",", ":"), sort_keys=True)
    escaped = compact.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def update_ledger_mxs(flow_var: str, ledger: dict[str, Any]) -> str:
    """MAXScript statements writing the ledger to appdata on node variable `flow_var`."""
    literal = ledger_to_mxs_literal(ledger)
    return (
        f"try (deleteAppData {flow_var} {LEDGER_APPDATA_ID}) catch ()\n"
        f"setAppData {flow_var} {LEDGER_APPDATA_ID} {literal}"
    )


def _hash_view(graph: dict[str, Any]) -> dict[str, Any]:
    events = []
    for event in graph.get("events", []):
        # Editor position and width stay out: tyFlow nudges events by itself
        # (live-observed), and layout is not structure.
        events.append(
            {
                "name": event.get("name"),
                "enabled": event.get("enabled"),
                "operators": [
                    {
                        "name": op.get("name"),
                        "enabled": op.get("enabled"),
                        "properties": [
                            [prop.get("name"), prop.get("value")]
                            for prop in op.get("properties", [])
                        ],
                    }
                    for op in event.get("operators", [])
                ],
            }
        )
    view: dict[str, Any] = {"events": events}
    scene = graph.get("scene")
    if scene:
        # Wiring is part of the structure once it can be read.
        view["edges"] = sorted(
            (e["from_event"], e["from_op"], e["to_event"]) for e in scene.get("edges") or []
        )
    return view


def structural_hash(graph: dict[str, Any]) -> str:
    """sha1 of the canonical structure-relevant subset of a full graph read."""
    canonical = json.dumps(_hash_view(graph), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _decode_token(value: str) -> str:
    return value.replace("<pipe>", "|")


def read_graph(
    name: str,
    *,
    max_operators_per_event: int = 200,
    max_properties_per_operator: int = 200,
) -> dict[str, Any]:
    """Full graph read in one MAXScript round trip.

    Properties are always read (they feed the structural hash); callers that do
    not want them in their payload strip them afterwards.
    """
    max_ops = max(1, int(max_operators_per_event))
    max_props = max(1, int(max_properties_per_operator))
    maxscript = f"""(
{CORE_HELPERS}
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    local bo = flow.baseobject
    local out = stringstream ""
    local particleCount = 0
    try (particleCount = flow.numParticles()) catch ()
    local tyVer = 0
    try (tyVer = tyFlow_version()) catch ()
    format "FLOW|%|%|%|%\\n" (mcpTyClean flow.name) (mcpTyClean ((classof bo) as string)) (particleCount as string) (tyVer as string) to:out
    local ledgerRaw = ""
    try (
        local ad = getAppData flow {LEDGER_APPDATA_ID}
        if ad != undefined do ledgerRaw = ad
    ) catch ()
    format "LEDGER|%\\n" (mcpTyClean ledgerRaw) to:out
    local eventNames = mcpTyParseSubNames bo
    for eventName in eventNames do (
        local ev = mcpTySubAnimByName bo eventName
        local posTok = "?"
        local widthTok = "?"
        local enabledTok = "?"
        if ev != undefined do (
            local realEv = undefined
            try (realEv = ev.object) catch ()
            if realEv != undefined do (
                try (
                    local p = realEv.Event.getPosition()
                    posTok = ((p.x as integer) as string) + "," + ((p.y as integer) as string)
                ) catch ()
                try (widthTok = (realEv.Event.getWidth()) as string) catch ()
                try (enabledTok = (realEv.Event.getEnabled()) as string) catch ()
            )
        )
        format "EV|%|%|%|%\\n" (mcpTyClean eventName) posTok widthTok enabledTok to:out
        if ev != undefined do (
            local opNames = mcpTyParseSubNames ev
            local opCount = opNames.count
            if opCount > {max_ops} do (
                format "WARN|OP_TRUNCATED|%|%|%\\n" (mcpTyClean eventName) (opNames.count as string) ({max_ops} as string) to:out
                opCount = {max_ops}
            )
            for oi = 1 to opCount do (
                local opName = opNames[oi]
                local op = mcpTySubAnimByName ev opName
                local opEnabledTok = "?"
                local pNames = #()
                if op != undefined do (
                    local realOp = undefined
                    try (realOp = op.object) catch ()
                    if realOp != undefined do (
                        try (opEnabledTok = (realOp.Operator.getEnabled()) as string) catch ()
                    )
                    try (pNames = getPropNames op) catch ()
                )
                format "OP|%|%|%|%\\n" (mcpTyClean eventName) (mcpTyClean opName) opEnabledTok (pNames.count as string) to:out
                if op != undefined do (
                    local pTake = pNames.count
                    if pTake > {max_props} do pTake = {max_props}
                    for pi = 1 to pTake do (
                        local pVal = "<unreadable>"
                        try (pVal = (getProperty op pNames[pi]) as string) catch ()
                        if pVal.count > 300 do pVal = (substring pVal 1 300) + "..."
                        format "PR|%|%|%|%\\n" (mcpTyClean eventName) (mcpTyClean opName) (mcpTyClean (pNames[pi] as string)) (mcpTyClean pVal) to:out
                    )
                    if pNames.count > pTake do (
                        format "WARN|PR_TRUNCATED|%|%|%|%\\n" (mcpTyClean eventName) (mcpTyClean opName) (pNames.count as string) (pTake as string) to:out
                    )
                )
            )
        )
    )
    out as string
)
)"""
    # Transport and call-state exceptions propagate with their code and hint.
    response = _client().send_command(maxscript)
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return {"error": raw.split("|", 1)[1]}
    if raw.startswith(MAXSCRIPT_ERROR):
        return {"error": "MAXScript error: " + raw[len(MAXSCRIPT_ERROR):].strip()}

    graph: dict[str, Any] = {
        "name": name,
        "class": "",
        "particleCount": 0,
        "tyflowVersion": 0,
        "events": [],
        "ledgerRaw": "",
        "warnings": [],
    }
    events: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        parts = line.split("|")
        tag = parts[0] if parts else ""
        if tag == "FLOW" and len(parts) >= 5:
            graph["name"] = _decode_token(parts[1])
            graph["class"] = _decode_token(parts[2])
            try:
                graph["particleCount"] = int(parts[3])
            except ValueError:
                pass
            try:
                graph["tyflowVersion"] = int(parts[4])
            except ValueError:
                pass
        elif tag == "LEDGER" and len(parts) >= 2:
            graph["ledgerRaw"] = _decode_token("|".join(parts[1:]))
        elif tag == "EV" and len(parts) >= 5:
            ev_name = _decode_token(parts[1])
            position = None
            if parts[2] != "?" and "," in parts[2]:
                try:
                    x_tok, y_tok = parts[2].split(",", 1)
                    position = [int(x_tok), int(y_tok)]
                except ValueError:
                    position = None
            width = None
            if parts[3] != "?":
                try:
                    width = int(parts[3])
                except ValueError:
                    width = None
            enabled = None if parts[4] == "?" else parts[4].lower() == "true"
            events[ev_name] = {
                "name": ev_name,
                "position": position,
                "width": width,
                "enabled": enabled,
                "operators": [],
            }
        elif tag == "OP" and len(parts) >= 5:
            ev_name = _decode_token(parts[1])
            entry = events.get(ev_name)
            if entry is None:
                continue
            enabled = None if parts[3] == "?" else parts[3].lower() == "true"
            try:
                prop_count = int(parts[4])
            except ValueError:
                prop_count = 0
            entry["operators"].append(
                {
                    "name": _decode_token(parts[2]),
                    "enabled": enabled,
                    "propertyCount": prop_count,
                    "properties": [],
                }
            )
        elif tag == "PR" and len(parts) >= 5:
            ev_name = _decode_token(parts[1])
            op_name = _decode_token(parts[2])
            entry = events.get(ev_name)
            if entry is None:
                continue
            op = next((item for item in entry["operators"] if item["name"] == op_name), None)
            if op is None:
                continue
            op["properties"].append(
                {"name": _decode_token(parts[3]), "value": _decode_token(parts[4])}
            )
        elif tag == "WARN":
            graph["warnings"].append([_decode_token(part) for part in parts[1:]])
    graph["events"] = list(events.values())
    graph["eventCount"] = len(graph["events"])
    _attach_scene(name, graph)
    return graph


def _attach_scene(name: str, graph: dict[str, Any]) -> None:
    """Add saved types, uids and enabled states to the MAXScript read, and keep
    the scene graph (with its edges) under graph["scene"]. Without the stream
    handler graph["scene"] stays None and the ledger supplies edges."""
    graph["scene"] = None
    try:
        scene = scene_graph(name)
    except SceneGraphUnavailable as exc:
        graph["sceneUnavailable"] = str(exc)
        return
    except Exception as exc:
        graph["sceneUnavailable"] = f"the tyFlow graph stream could not be read: {exc}"
        graph["warnings"].append(["SCENE_GRAPH", graph["sceneUnavailable"]])
        return
    graph["scene"] = scene
    by_name = {ev["name"]: ev for ev in scene["events"]}
    for event in graph["events"]:
        saved = by_name.get(canon_name(event["name"]))
        if saved is None:
            continue
        if event.get("enabled") is None:
            event["enabled"] = saved.get("enabled")
        # A truncated read lists the first operators, in the same order.
        for op, saved_op in zip(event["operators"], saved["operators"]):
            if canon_name(op["name"]) != saved_op["name"]:
                continue
            op["type"] = saved_op.get("type")
            if op.get("enabled") is None:
                op["enabled"] = saved_op.get("enabled")
    for warning in scene.get("warnings") or []:
        graph["warnings"].append(["SCENE_GRAPH", warning])


def graph_payload(
    name: str,
    *,
    include_properties: bool = False,
    max_operators_per_event: int = 200,
    max_properties_per_operator: int = 200,
) -> dict[str, Any]:
    """read_graph + hash + ledger merge — the agent-facing graph view."""
    graph = read_graph(
        name,
        max_operators_per_event=max_operators_per_event,
        max_properties_per_operator=max_properties_per_operator,
    )
    if "error" in graph:
        return graph
    graph_hash = structural_hash(graph)
    ledger = parse_ledger(graph.pop("ledgerRaw", ""))
    scene = graph.pop("scene", None)
    unavailable = graph.pop("sceneUnavailable", None)
    graph["graph_hash"] = graph_hash
    if scene is not None:
        graph["wiring_source"] = "scene"
        graph["edges"] = scene["edges"]
        graph["operator_types"] = {
            f"{event['name']}/{op['name']}": op["type"]
            for event in scene["events"]
            for op in event["operators"]
            if op.get("type")
        }
        graph["ledger_status"] = "scene"
    else:
        if not ledger["hash"]:
            status = "absent"
        elif ledger["hash"] == graph_hash:
            status = "fresh"
        else:
            status = "stale"
        graph["wiring_source"] = "ledger"
        if unavailable:
            graph["wiring_note"] = unavailable
        graph["edges"] = ledger["edges"]
        graph["operator_types"] = ledger["ops"]
        graph["ledger_status"] = status
        graph["ledger"] = ledger
    if not include_properties:
        for event in graph["events"]:
            for op in event["operators"]:
                op.pop("properties", None)
    return graph


def write_ledger_appdata(name: str, ledger: dict[str, Any]) -> dict[str, Any]:
    """Write the ledger dict verbatim to appdata on the flow node."""
    maxscript = f"""(
local flow = {node_by_name(name, missing_ok=True)}
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    {update_ledger_mxs("flow", ledger)}
    "OK"
)
)"""
    response = _client().send_command(maxscript)
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return {"error": raw.split("|", 1)[1]}
    if raw.strip() != "OK":
        return {"error": f"Ledger write failed: {raw[:200]}"}
    return {"hash": str(ledger.get("hash", ""))}


def store_ledger(
    name: str, ledger: dict[str, Any], graph: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Stamp the ledger with a current structural hash and persist it.

    Pass a fresh full `read_graph` result as `graph` to reuse it; otherwise one
    read round trip is performed here.
    """
    if graph is None or "error" in graph:
        graph = read_graph(name)
    if "error" in graph:
        return graph
    ledger = dict(ledger)
    scene = graph.get("scene")
    if scene:
        # The flow's own wiring wins; the ledger mirrors it for older bridges.
        ledger["edges"] = [dict(edge) for edge in scene["edges"]]
        ledger["ops"] = {
            f"{event['name']}/{op['name']}": op["type"]
            for event in scene["events"]
            for op in event["operators"]
            if op.get("type")
        }
    ledger["v"] = LEDGER_VERSION
    ledger["hash"] = structural_hash(graph)
    stored = write_ledger_appdata(name, ledger)
    if "error" not in stored:
        stored["edges"] = ledger["edges"]
    return stored


def refresh_ledger_after_mutation(name: str) -> dict[str, Any]:
    """Re-read the flow, keep existing edges/ops, restamp the structural hash."""
    graph = read_graph(name)
    if "error" in graph:
        return graph
    ledger = parse_ledger(graph.get("ledgerRaw", ""))
    return store_ledger(name, ledger, graph=graph)


def ledger_update(
    name: str,
    *,
    replace_edge: tuple[str, str, str | None] | None = None,
    remove_event: str | None = None,
    remove_operator: tuple[str, str] | None = None,
    record_ops: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Post-mutation ledger maintenance (one read + one write round trip).

    replace_edge: (from_event, from_op, to_event) — drops any existing edge for
    that (from_event, from_op) and, when to_event is not None, records the new one.
    remove_event: drops all edges touching the event and its ops entries.
    remove_operator: (event, op) — drops its outgoing edge and ops entry.
    record_ops: {"Event/OpName": "Operator Type"} entries recorded at creation.
    Returns {"ledgerHash": ...} on success or {"ledgerWarning": ...} — ledger
    failures never fail the mutation that preceded them.
    """
    try:
        return _ledger_update(
            name,
            replace_edge=replace_edge,
            remove_event=remove_event,
            remove_operator=remove_operator,
            record_ops=record_ops,
        )
    except Exception as exc:
        # The mutation already ran; a blocked or lost ledger round trip only
        # leaves the ledger stale.
        return {"ledgerWarning": f"Ledger not updated: {exc}"}


def merge_ledger(payload: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """Merge a ledger outcome into a tool reply; a stale ledger is also a warning."""
    payload.update(outcome)
    if outcome.get("ledgerWarning"):
        payload.setdefault("warnings", []).append(outcome["ledgerWarning"])
    return payload


def _ledger_update(
    name: str,
    *,
    replace_edge: tuple[str, str, str | None] | None,
    remove_event: str | None,
    remove_operator: tuple[str, str] | None,
    record_ops: dict[str, str] | None,
) -> dict[str, Any]:
    graph = read_graph(name)
    if "error" in graph:
        return {"ledgerWarning": f"Ledger not updated: {graph['error']}"}
    ledger = parse_ledger(graph.get("ledgerRaw", ""))
    if replace_edge is not None:
        from_event, from_op, to_event = (
            canon_name(replace_edge[0]),
            canon_name(replace_edge[1]),
            canon_name(replace_edge[2]) if replace_edge[2] is not None else None,
        )
        ledger["edges"] = [
            edge
            for edge in ledger["edges"]
            if not (edge["from_event"] == from_event and edge["from_op"] == from_op)
        ]
        if to_event is not None:
            ledger["edges"].append(
                {"from_event": from_event, "from_op": from_op, "to_event": to_event}
            )
    if remove_event is not None:
        remove_event = canon_name(remove_event)
        ledger["edges"] = [
            edge
            for edge in ledger["edges"]
            if edge["from_event"] != remove_event and edge["to_event"] != remove_event
        ]
        prefix = remove_event + "/"
        ledger["ops"] = {
            key: value for key, value in ledger["ops"].items() if not key.startswith(prefix)
        }
    if remove_operator is not None:
        event_name, op_name = canon_name(remove_operator[0]), canon_name(remove_operator[1])
        ledger["edges"] = [
            edge
            for edge in ledger["edges"]
            if not (edge["from_event"] == event_name and edge["from_op"] == op_name)
        ]
        ledger["ops"].pop(f"{event_name}/{op_name}", None)
    if record_ops:
        ledger["ops"].update({canon_name(k): str(v) for k, v in record_ops.items()})
    stored = store_ledger(name, ledger, graph=graph)
    if "error" in stored:
        return {"ledgerWarning": f"Ledger not updated: {stored['error']}"}
    return {"ledgerHash": stored["hash"], "edges": stored["edges"]}
