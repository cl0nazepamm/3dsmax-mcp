"""tyFlow operator manifest: probe-harvested catalog of operator types and params.

PB2 param names are non-obvious (`shape_type_tab`, `type_3d_ID_tab`, ...). The
manifest gives agents exact type names, availability on this installation,
saved type IDs, and per-type property names/defaults. Harvest probes a scratch
flow (one operator of each type added and introspected, then confirmed against
the flow's saved graph) and caches per tyFlow version.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from ..helpers.maxscript import NODE_LOOKUP_FNS, safe_string
from ..helpers.tyflow_catalog import EXECUTABLE_OPERATOR_NAMES, TYFLOW_OPERATOR_NAMES

from ..coerce import StrList
from ..max_client import CALL_STATE_ERRORS
from ..server import client, mcp
from ._tyflow_core import CORE_HELPERS, MAXSCRIPT_ERROR, SceneGraphUnavailable, canon_name, scene_graph

_PROBE_FLOW = "zzz_mcp_manifest_probe"
_BATCH_SIZE = 40


def _cache_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    path = Path(root) / "3dsmax-mcp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(version: int) -> Path:
    return _cache_dir() / f"tyflow_manifest_{version}.json"


def _latest_cache() -> Path | None:
    candidates = sorted(
        _cache_dir().glob("tyflow_manifest_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("operators"), list) else None


def _tyflow_version() -> int | dict[str, Any]:
    maxscript = """(
if tyFlow == undefined then "__ERROR__|tyFlow plugin is not available"
else ((tyFlow_version()) as string)
)"""
    # Transport and call-state exceptions propagate with their code and hint.
    response = client.send_command(maxscript)
    raw = str(response.get("result", "")).strip()
    if raw.startswith("__ERROR__|"):
        return {"error": raw.split("|", 1)[1]}
    try:
        return int(raw)
    except ValueError:
        return {"error": f"Could not read tyFlow version: {raw[:100]}"}


def _saved_event(event_name: str) -> dict[str, Any]:
    """The probe event as saved: {canonical operator name: saved operator}."""
    scene = scene_graph(_PROBE_FLOW)
    event = next((ev for ev in scene["events"] if ev["name"] == canon_name(event_name)), None)
    if event is None:
        raise RuntimeError(f"probe event {event_name} was not in the saved graph")
    return {op["name"]: op for op in event["operators"]}


def _confirm_in_scene(event_name: str, entries: list[dict[str, Any]], family: str) -> str | None:
    """Record family and type ID for the types that joined the probe event.

    An event holds operators of one family, set by its first operator; tyFlow
    answers addOperator for another family with an object that never joins.
    Types that did not join are marked `pending` for _group_families. Returns
    why the saved graph could not be checked.
    """
    try:
        saved = _saved_event(event_name)
    except SceneGraphUnavailable as exc:
        return str(exc)
    except CALL_STATE_ERRORS:
        raise
    except Exception as exc:
        return f"the probe flow's graph could not be read: {exc}"
    for entry in entries:
        op = saved.get(canon_name(entry["type"]))
        if op is not None:
            entry["type_id"] = op.get("type_id")
            entry["family"] = family
        elif entry.get("available"):
            entry["pending"] = True
    return None


def _family_label(types: list[str]) -> str:
    for word, label in (("Terrain", "terrain"), ("VDB", "vdb"), ("Inferno", "inferno")):
        if sum(word in item for item in types) * 2 > len(types):
            return label
    return canon_name(types[0]).lower()


def _group_families(entries: list[dict[str, Any]]) -> str | None:
    """Sort types that would not join a particle event into their families: each
    round adds the remaining types to one new event, whose first operator sets
    its family. Returns why grouping stopped early."""
    pending = [entry for entry in entries if entry.pop("pending", False)]
    for round_index in range(1, len(pending) + 1):
        if not pending:
            return None
        event_name = f"zzz_family_{round_index}"
        array_literal = "#(" + ", ".join(f'"{safe_string(e["type"])}"' for e in pending) + ")"
        maxscript = f"""(
{NODE_LOOKUP_FNS}
local flows = mcpNamedNodes "{_PROBE_FLOW}"
if flows.count != 1 then "__ERROR__|The probe flow is missing" else (
    local eh = flows[1].tyFlow.addEvent()
    eh.Event.setName "{event_name}"
    for opType in {array_literal} do (try (eh.Event.addOperator opType -1) catch ())
    "OK"
)
)"""
        raw = str(client.send_command(maxscript, timeout=300).get("result", ""))
        if raw.startswith("__ERROR__|"):
            return raw.split("|", 1)[1]
        try:
            saved = _saved_event(event_name)
        except CALL_STATE_ERRORS:
            raise
        except Exception as exc:
            return f"the probe flow's graph could not be read: {exc}"
        joined = [entry for entry in pending if canon_name(entry["type"]) in saved]
        if not joined:
            # Not even an empty event takes the first type.
            first = pending.pop(0)
            first["available"] = False
            first["note"] = "addOperator returned an object, but the operator never joined an event"
            continue
        family = _family_label([entry["type"] for entry in joined])
        for entry in joined:
            entry["type_id"] = saved[canon_name(entry["type"])].get("type_id")
            entry["family"] = family
        pending = [entry for entry in pending if entry not in joined]
    return None if not pending else f"{len(pending)} type(s) were not grouped"


def _probe_batch(names: list[str], event_name: str) -> dict[str, Any] | list[dict[str, Any]]:
    array_literal = "#(" + ", ".join(f'"{safe_string(item)}"' for item in names) + ")"
    maxscript = f"""(
{CORE_HELPERS}
{NODE_LOOKUP_FNS}
if tyFlow == undefined then (
    "__ERROR__|tyFlow plugin is not available"
) else (
    local out = stringstream ""
    -- Batches reuse the scratch flow; several left by an interrupted run are replaced.
    local flow = undefined
    local probeFlows = mcpNamedNodes "{_PROBE_FLOW}"
    if probeFlows.count == 1 then flow = probeFlows[1] else (for f in probeFlows do (try (delete f) catch ()))
    if flow == undefined do flow = tyFlow name:"{_PROBE_FLOW}" pos:[0,0,-100000]
    local eh = flow.tyFlow.addEvent()
    local ev = eh.Event
    ev.setName "{safe_string(event_name)}"
    -- Birth first makes this a particle event. Operators stay in it until the
    -- flow is deleted, so the saved graph can confirm each type and its ID.
    try (ev.addOperator "Birth" -1) catch ()
    for opName in {array_literal} do (
        local op = undefined
        try (op = ev.addOperator opName -1) catch ()
        if op == undefined then (
            format "OPX|%|unavailable|0\\n" (mcpTyClean opName) to:out
        ) else (
            local pNames = #()
            try (pNames = getPropNames op) catch ()
            format "OPX|%|available|%\\n" (mcpTyClean opName) (pNames.count as string) to:out
            for p in pNames do (
                local v = "<unreadable>"
                local cls = "?"
                try (
                    local pv = getProperty op p
                    v = pv as string
                    cls = (classof pv) as string
                ) catch ()
                if v.count > 120 do v = (substring v 1 120) + "..."
                format "PP|%|%|%|%\\n" (mcpTyClean opName) (mcpTyClean (p as string)) (mcpTyClean v) (mcpTyClean cls) to:out
            )
        )
    )
    out as string
)
)"""
    try:
        response = client.send_command(maxscript, timeout=300)
    except CALL_STATE_ERRORS:
        raise  # the probe may still run; never report it as an ordinary failure
    except Exception as exc:
        return {"error": str(exc)}
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return {"error": raw.split("|", 1)[1]}
    if raw.startswith(MAXSCRIPT_ERROR):
        return {"error": "MAXScript error: " + raw[len(MAXSCRIPT_ERROR):].strip()}

    def _decode(token: str) -> str:
        return token.replace("<pipe>", "|")

    entries: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) >= 4 and parts[0] == "OPX":
            type_name = _decode(parts[1])
            entries[type_name] = {
                "type": type_name,
                "available": parts[2] == "available",
                "executable": type_name in EXECUTABLE_OPERATOR_NAMES,
                "propertyCount": int(parts[3]) if parts[3].isdigit() else 0,
                "properties": [],
            }
        elif len(parts) >= 5 and parts[0] == "PP":
            entry = entries.get(_decode(parts[1]))
            if entry is not None:
                entry["properties"].append(
                    {
                        "name": _decode(parts[2]),
                        "default": _decode(parts[3]),
                        "valueClass": _decode(parts[4]),
                    }
                )
    return list(entries.values())


def _cleanup_probe_flow() -> str | None:
    """Delete the scratch flow; return why it may still be in the scene."""
    maxscript = f"""(
{NODE_LOOKUP_FNS}
local flows = mcpNamedNodes "{_PROBE_FLOW}"
for flow in flows do (try (delete flow) catch ())
if (for flow in flows where isValidNode flow collect flow).count > 0 then "LEFT" else "OK"
)"""
    try:
        raw = str(client.send_command(maxscript).get("result", "")).strip()
    except Exception as exc:
        return str(exc)
    return None if raw == "OK" else f"delete was refused ({raw[:120]})"


@mcp.tool()
def harvest_tyflow_manifest(
    refresh: bool = False,
    operator_names: StrList | None = None,
    batch_size: int = _BATCH_SIZE,
) -> str:
    """Probe-harvest the tyFlow operator manifest and cache it per tyFlow version.

    Without `refresh`, an existing cache is returned untouched (no Max traffic).
    `operator_names` limits the probe to specific types and merges results into
    the existing cache. Probing adds each operator (inert) to a hidden scratch
    flow and reads its property names/defaults; the saved graph then confirms
    which types joined the flow (`available`) and records their type IDs. The
    scratch flow is deleted afterwards.
    """
    version = _tyflow_version()
    if isinstance(version, dict):
        return json.dumps(version)
    cache = _cache_path(version)
    existing = _read_manifest(cache)
    if existing and not refresh and not operator_names:
        return json.dumps(
            {
                "cached": True,
                "tyflowVersion": version,
                "operatorCount": len(existing["operators"]),
                "availableCount": sum(1 for op in existing["operators"] if op.get("available")),
                "cachePath": str(cache),
            }
        )

    names = [str(item) for item in (operator_names or TYFLOW_OPERATOR_NAMES)]
    harvested: list[dict[str, Any]] = []
    errors: list[str] = []
    step = max(1, int(batch_size))
    cleanup_error: str | None = None
    unconfirmed: str | None = None
    try:
        for start in range(0, len(names), step):
            batch = names[start : start + step]
            event_name = f"zzz_probe_{start // step + 1}"
            outcome = _probe_batch(batch, event_name)
            if isinstance(outcome, dict):
                errors.append(str(outcome.get("error", "probe failed")))
                break
            unconfirmed = _confirm_in_scene(event_name, outcome, "particle") or unconfirmed
            harvested.extend(outcome)
        if not unconfirmed:
            grouping = _group_families(harvested)
            if grouping:
                unconfirmed = f"family grouping stopped: {grouping}"
    finally:
        cleanup_error = _cleanup_probe_flow()
        for entry in harvested:
            entry.pop("pending", None)

    if not harvested:
        return json.dumps({"error": "Manifest probe produced no entries", "details": errors})

    probed = {op["type"] for op in harvested}
    unprobed = [item for item in names if item not in probed]
    # A probe that stopped early merges into the cache instead of replacing it,
    # so operators it never reached keep their earlier entries.
    if (operator_names or errors) and existing:
        merged = {op["type"]: op for op in existing["operators"]}
        for op in harvested:
            merged[op["type"]] = op
        operators = list(merged.values())
    else:
        operators = harvested
    manifest = {
        "tyflowVersion": version,
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "operators": operators,
    }
    cache.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    payload: dict[str, Any] = {
        "cached": False,
        "tyflowVersion": version,
        "operatorCount": len(operators),
        "availableCount": sum(1 for op in operators if op.get("available")),
        "unavailable": [op["type"] for op in operators if not op.get("available")],
        "errors": errors,
        "cachePath": str(cache),
    }
    warnings: list[str] = []
    if errors:
        payload["unprobed"] = unprobed
        warnings.append(
            f"The probe stopped early; {len(unprobed)} operator type(s) were not probed: "
            + "; ".join(errors)
        )
    if unconfirmed:
        warnings.append(f"Availability, family or type ID may be missing for some types: {unconfirmed}")
    if cleanup_error:
        warnings.append(f"Scratch flow {_PROBE_FLOW} may remain in the scene: {cleanup_error}")
    if warnings:
        payload["status"] = "partial"
        payload["warnings"] = warnings
    return json.dumps(payload)


@mcp.tool()
def list_tyflow_operators(
    query: str = "",
    include_properties: bool = False,
    executable_only: bool = False,
    limit: int = 40,
) -> str:
    """Query the cached tyFlow operator manifest (no Max traffic).

    Matches query tokens against operator type names and property names
    (case-insensitive substrings). Each entry's `available` reports whether this
    installation could add that type when probed, and `family` which events
    take it: an event holds operators of one family (particle, terrain, vdb,
    inferno), set by its first operator. Run harvest_tyflow_manifest first if no
    cache exists.
    """
    path = _latest_cache()
    manifest = _read_manifest(path) if path else None
    if not manifest:
        return json.dumps(
            {
                "error": "No tyFlow operator manifest cached yet.",
                "hint": {"suggested_tools": ["harvest_tyflow_manifest"]},
            }
        )
    tokens = [token.casefold() for token in query.split() if token.strip()]
    query_key = query.strip().casefold()
    exact_matches: list[dict[str, Any]] = []
    name_matches: list[dict[str, Any]] = []
    prop_matches: list[dict[str, Any]] = []
    for op in manifest["operators"]:
        if executable_only and not op.get("executable"):
            continue
        bucket = name_matches
        if tokens:
            type_key = op["type"].casefold()
            if query_key == type_key:
                bucket = exact_matches
            elif query_key in type_key or all(token in type_key for token in tokens):
                bucket = name_matches
            else:
                prop_names = " ".join(p["name"].casefold() for p in op.get("properties", []))
                if all(token in type_key or token in prop_names for token in tokens):
                    bucket = prop_matches
                else:
                    continue
        entry = {
            "type": op["type"],
            "available": op.get("available", False),
            "family": op.get("family"),
            "executable": op.get("executable", False),
            "propertyCount": op.get("propertyCount", 0),
        }
        if include_properties:
            entry["properties"] = op.get("properties", [])
        bucket.append(entry)
    matches = exact_matches + name_matches + prop_matches
    truncated = len(matches) > max(1, int(limit))
    return json.dumps(
        {
            "tyflowVersion": manifest.get("tyflowVersion"),
            "matchCount": len(matches),
            "truncated": truncated,
            "operators": matches[: max(1, int(limit))],
        }
    )
