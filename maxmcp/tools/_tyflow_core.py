"""Shared tyFlow graph-core helpers: shadow wiring ledger, structural hash, graph read.

tyFlow keeps its event graph in private structures — wiring cannot be read back
through refs/sub-anims/PB2 at any layer (live-probed on tyFlow 2.05 / Max 2027).
The MCP layer therefore owns wiring knowledge: every connect/disconnect made
through MCP tools is recorded in a JSON ledger stored as AppData on the flow
node, so it travels with the scene file.  Staleness is detected by comparing a
structural hash of everything that IS readable (events, operators, properties)
against the hash stored at the last MCP mutation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..helpers.maxscript import safe_string


def _client():
    """Lazy bridge-client accessor: keeps this module importable without
    triggering server tool registration (avoids circular imports)."""
    from ..server import client

    return client


LEDGER_APPDATA_ID = 1415075927
LEDGER_VERSION = 1

# Shared MAXScript helpers (esc/jsonStringArray/find*SubAnim come from tyflow.HELPERS
# where needed; this block is standalone for core reads).
CORE_HELPERS = """
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
        events.append(
            {
                "name": event.get("name"),
                "position": event.get("position"),
                "width": event.get("width"),
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
    return {"events": events}


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
local flow = getNodeByName "{safe_string(name)}"
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
    try:
        response = _client().send_command(maxscript)
    except Exception as exc:
        return {"error": str(exc)}
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return {"error": raw.split("|", 1)[1]}

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
    return graph


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
    if not ledger["hash"]:
        status = "absent"
    elif ledger["hash"] == graph_hash:
        status = "fresh"
    else:
        status = "stale"
    graph["graph_hash"] = graph_hash
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
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    {update_ledger_mxs("flow", ledger)}
    "OK"
)
)"""
    try:
        response = _client().send_command(maxscript)
    except Exception as exc:
        return {"error": str(exc)}
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
    ledger["v"] = LEDGER_VERSION
    ledger["hash"] = structural_hash(graph)
    return write_ledger_appdata(name, ledger)


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
    return {"ledgerHash": stored["hash"], "edges": ledger["edges"]}
