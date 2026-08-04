"""tyFlow patch transaction: hash gate, checkpoint, batched ops, verify, rollback.

The tyFlow equivalent of `mcg_apply_patch`. There is no compile step — a patch
is verified empirically by resetting the simulation and asserting particle
counts at probe frames. The wiring ledger (see `_tyflow_core`) is maintained
incrementally from the operations that succeeded.

Rollback caveat: the checkpoint is a hidden clone of the flow node; rolling back
deletes the patched node and renames the clone back, so OTHER scene objects that
referenced the flow node (e.g. a tyMesher) lose their reference. Pass
checkpoint=False for trivial edits where that trade-off is not worth it.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from ..helpers.maxscript import safe_string

from ..coerce import DictList, IntList
from ..server import client, mcp
from ._tyflow_core import (
    CORE_HELPERS,
    canon_name,
    graph_payload,
    ledger_update,
    parse_ledger,
    read_graph,
    store_ledger,
    write_ledger_appdata,
)

_TRANSACTION_LOCK = threading.RLock()

PATCH_HELPERS = """
fn mcpTyRequireEvent flow evName = (
    local ev = findEventSubAnim flow evName
    if ev == undefined do throw ("Event not found: " + evName)
    ev
)
fn mcpTyRequireOperator ev opName = (
    local op = findOperatorSubAnim ev opName
    if op == undefined do throw ("Operator not found: " + opName)
    op
)
fn mcpTyReal sub = (
    local r = undefined
    try (r = sub.object) catch ()
    if r == undefined do r = sub
    r
)
"""

# Operations whose effects can be rolled back by synthesized inverse operations
# (no checkpoint clone needed — clone/delete churn destabilizes tyFlow's Qt UI).
_ADDITIVE_OPS = frozenset({"add_event", "add_operator", "connect", "set_event_position"})

_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "add_event": ("event",),
    "remove_event": ("event",),
    "add_operator": ("event", "type"),
    "remove_operator": ("event", "operator"),
    "set_properties": ("event", "operator", "properties"),
    "connect": ("from_event", "to_event"),
    "disconnect": ("from_event", "from_operator"),
    "rename_event": ("event", "new_name"),
    "rename_operator": ("event", "operator", "new_name"),
    "set_event_position": ("event", "position"),
    "set_enabled": ("event", "enabled"),
}


def _op_is_executable(operation: dict[str, Any]) -> bool:
    kind = str(operation.get("op", ""))
    if kind == "add_operator" and "script" in str(operation.get("type", "")).casefold():
        return True
    properties = operation.get("properties")
    if isinstance(properties, dict) and any(
        str(key).casefold() in {"script", "expression"} for key in properties
    ):
        return True
    return False


def _authorize_executable(allow_executable: bool) -> str | None:
    if not allow_executable:
        return (
            "Executable tyFlow operators are blocked by default; "
            "pass allow_executable=true explicitly"
        )
    probe = client.send_command("true")
    meta = probe.get("meta") if isinstance(probe.get("meta"), dict) else {}
    if meta.get("safeMode", meta.get("safe_mode", True)) is not False:
        return "Executable tyFlow operators are blocked while MCP safe_mode is enabled"
    return None


def _validate_position(value: Any, index: int, field: str) -> str | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return f"Operation {index}: {field} must be [x, y]"
    try:
        int(value[0])
        int(value[1])
    except (TypeError, ValueError):
        return f"Operation {index}: {field} must contain integers"
    return None


def _validate_operations(operations: list[dict[str, Any]]) -> tuple[str | None, bool]:
    """Full Python-side validation BEFORE any MAXScript runs."""
    if not operations:
        return "operations cannot be empty", False
    needs_executable = False
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            return f"Operation {index} is not an object", False
        kind = str(operation.get("op", ""))
        if kind not in _REQUIRED_FIELDS:
            return (
                f"Operation {index}: unknown op '{kind}'. "
                f"Valid: {sorted(_REQUIRED_FIELDS)}",
                False,
            )
        for field in _REQUIRED_FIELDS[kind]:
            if field not in operation or operation[field] in ("", None):
                return f"Operation {index} ({kind}): missing field '{field}'", False
        if kind == "set_properties" and not isinstance(operation.get("properties"), dict):
            return f"Operation {index}: properties must be an object", False
        if kind == "add_operator" and "properties" in operation and not isinstance(
            operation["properties"], dict
        ):
            return f"Operation {index}: properties must be an object", False
        if kind in {"add_event", "set_event_position"} and "position" in operation:
            problem = _validate_position(operation["position"], index, "position")
            if problem and kind == "set_event_position":
                return problem, False
            if problem and operation["position"] is not None:
                return problem, False
        if kind == "set_enabled" and not isinstance(operation.get("enabled"), bool):
            return f"Operation {index}: enabled must be a boolean", False
        if _op_is_executable(operation):
            needs_executable = True
    return None, needs_executable


def _op_body(operation: dict[str, Any], index: int) -> str:
    """MAXScript body for one operation; failure paths throw."""
    from .tyflow import _assignment_lines  # lazy: avoids import cycle

    kind = str(operation["op"])
    if kind == "add_event":
        lines = [
            "local eh = flow.tyFlow.addEvent()",
            f'eh.Event.setName "{safe_string(str(operation["event"]))}"',
        ]
        position = operation.get("position")
        if isinstance(position, (list, tuple)) and len(position) == 2:
            lines.append(f"eh.Event.setPosition [{int(position[0])},{int(position[1])}]")
        return "\n".join(lines)
    if kind == "remove_event":
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"\n'
            "local realEv = mcpTyReal ev\n"
            "realEv.Event.remove()"
        )
    if kind == "add_operator":
        op_type = str(operation["type"])
        op_name = str(operation.get("name") or op_type)
        position = int(operation.get("position", -1))
        lines = [
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"',
            "local realEv = mcpTyReal ev",
            f'local newOp = realEv.Event.addOperator "{safe_string(op_type)}" {position}',
            f'if newOp == undefined do throw ("Could not add operator: " + "{safe_string(op_type)}")',
            f'newOp.Operator.setName "{safe_string(op_name)}"',
        ]
        properties = operation.get("properties")
        if isinstance(properties, dict) and properties:
            assignments, _ = _assignment_lines(properties, "newOp")
            lines.append(assignments)
        return "\n".join(lines)
    if kind == "remove_operator":
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"\n'
            f'local op = mcpTyRequireOperator ev "{safe_string(str(operation["operator"]))}"\n'
            "local realOp = mcpTyReal op\n"
            "realOp.remove()"
        )
    if kind == "set_properties":
        assignments, _ = _assignment_lines(
            operation["properties"], "op", raw_strings=bool(operation.get("raw_values"))
        )
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"\n'
            f'local op = mcpTyRequireOperator ev "{safe_string(str(operation["operator"]))}"\n'
            f"{assignments}\n"
            'if applied.count == 0 do throw "No properties applied"'
        )
    if kind == "connect":
        from_operator = str(operation.get("from_operator") or "Send Out")
        ensure = bool(operation.get("ensure_send_out", "from_operator" not in operation))
        if ensure:
            missing_branch = (
                "local realEv = mcpTyReal ev\n"
                '        op = realEv.Event.addOperator "Send Out" -1\n'
                '        if op == undefined do throw "Could not create Send Out"\n'
                f'        op.Operator.setName "{safe_string(from_operator)}"\n'
                '        detail = "created"'
            )
        else:
            missing_branch = (
                f'throw ("Operator not found: " + "{safe_string(from_operator)}")'
            )
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["from_event"]))}"\n'
            f'local dst = mcpTyRequireEvent flow "{safe_string(str(operation["to_event"]))}"\n'
            f'local op = findOperatorSubAnim ev "{safe_string(from_operator)}"\n'
            "if op == undefined do (\n"
            f"        {missing_branch}\n"
            ")\n"
            "local realOp = mcpTyReal op\n"
            "local realTarget = mcpTyReal dst\n"
            "realOp.connect realTarget"
        )
    if kind == "disconnect":
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["from_event"]))}"\n'
            f'local op = mcpTyRequireOperator ev "{safe_string(str(operation["from_operator"]))}"\n'
            "local realOp = mcpTyReal op\n"
            "realOp.disconnect()"
        )
    if kind == "rename_event":
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"\n'
            "local realEv = mcpTyReal ev\n"
            f'realEv.Event.setName "{safe_string(str(operation["new_name"]))}"'
        )
    if kind == "rename_operator":
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"\n'
            f'local op = mcpTyRequireOperator ev "{safe_string(str(operation["operator"]))}"\n'
            "local realOp = mcpTyReal op\n"
            f'realOp.Operator.setName "{safe_string(str(operation["new_name"]))}"'
        )
    if kind == "set_event_position":
        position = operation["position"]
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"\n'
            "local realEv = mcpTyReal ev\n"
            f"realEv.Event.setPosition [{int(position[0])},{int(position[1])}]"
        )
    if kind == "set_enabled":
        enabled = "true" if operation["enabled"] else "false"
        operator = operation.get("operator")
        if operator:
            return (
                f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"\n'
                f'local op = mcpTyRequireOperator ev "{safe_string(str(operator))}"\n'
                "local realOp = mcpTyReal op\n"
                f"realOp.Operator.setEnabled {enabled}"
            )
        return (
            f'local ev = mcpTyRequireEvent flow "{safe_string(str(operation["event"]))}"\n'
            "local realEv = mcpTyReal ev\n"
            f"realEv.Event.setEnabled {enabled}"
        )
    raise ValueError(f"Unhandled op kind: {kind}")


def _ledger_mutations(
    operations: list[dict[str, Any]], op_results: list[dict[str, Any]]
) -> list[tuple[Any, ...]]:
    """Ledger mutations for the operations that actually succeeded."""
    mutations: list[tuple[Any, ...]] = []
    for operation, result in zip(operations, op_results):
        if result.get("status") != "ok":
            continue
        kind = str(operation["op"])
        if kind == "add_operator":
            op_name = canon_name(operation.get("name") or operation["type"])
            mutations.append(
                ("record_op", f"{canon_name(operation['event'])}/{op_name}", str(operation["type"]))
            )
        elif kind == "connect":
            from_operator = canon_name(operation.get("from_operator") or "Send Out")
            if result.get("detail") == "created":
                mutations.append(
                    ("record_op", f"{canon_name(operation['from_event'])}/{from_operator}", "Send Out")
                )
            mutations.append(
                (
                    "edge",
                    canon_name(operation["from_event"]),
                    from_operator,
                    canon_name(operation["to_event"]),
                )
            )
        elif kind == "disconnect":
            mutations.append(
                ("unedge", canon_name(operation["from_event"]), canon_name(operation["from_operator"]))
            )
        elif kind == "remove_event":
            mutations.append(("remove_event", canon_name(operation["event"])))
        elif kind == "remove_operator":
            mutations.append(
                ("remove_op", canon_name(operation["event"]), canon_name(operation["operator"]))
            )
        elif kind == "rename_event":
            mutations.append(
                ("rename_event", canon_name(operation["event"]), canon_name(operation["new_name"]))
            )
        elif kind == "rename_operator":
            mutations.append(
                (
                    "rename_op",
                    canon_name(operation["event"]),
                    canon_name(operation["operator"]),
                    canon_name(operation["new_name"]),
                )
            )
    return mutations


def _apply_ledger_mutations(name: str, mutations: list[tuple[Any, ...]]) -> dict[str, Any]:
    graph = read_graph(name)
    if "error" in graph:
        return {"ledgerWarning": f"Ledger not updated: {graph['error']}"}
    ledger = parse_ledger(graph.get("ledgerRaw", ""))
    edges = ledger["edges"]
    ops = ledger["ops"]
    for mutation in mutations:
        kind = mutation[0]
        if kind == "record_op":
            ops[mutation[1]] = mutation[2]
        elif kind == "edge":
            _, from_event, from_op, to_event = mutation
            edges[:] = [
                e for e in edges
                if not (e["from_event"] == from_event and e["from_op"] == from_op)
            ]
            edges.append(
                {"from_event": from_event, "from_op": from_op, "to_event": to_event}
            )
        elif kind == "unedge":
            _, from_event, from_op = mutation
            edges[:] = [
                e for e in edges
                if not (e["from_event"] == from_event and e["from_op"] == from_op)
            ]
        elif kind == "remove_event":
            event = mutation[1]
            edges[:] = [
                e for e in edges
                if e["from_event"] != event and e["to_event"] != event
            ]
            prefix = event + "/"
            for key in [k for k in ops if k.startswith(prefix)]:
                del ops[key]
        elif kind == "remove_op":
            _, event, op_name = mutation
            edges[:] = [
                e for e in edges
                if not (e["from_event"] == event and e["from_op"] == op_name)
            ]
            ops.pop(f"{event}/{op_name}", None)
        elif kind == "rename_event":
            _, old, new = mutation
            for edge in edges:
                if edge["from_event"] == old:
                    edge["from_event"] = new
                if edge["to_event"] == old:
                    edge["to_event"] = new
            old_prefix = old + "/"
            for key in [k for k in ops if k.startswith(old_prefix)]:
                ops[new + "/" + key[len(old_prefix):]] = ops.pop(key)
        elif kind == "rename_op":
            _, event, old, new = mutation
            for edge in edges:
                if edge["from_event"] == event and edge["from_op"] == old:
                    edge["from_op"] = new
            if f"{event}/{old}" in ops:
                ops[f"{event}/{new}"] = ops.pop(f"{event}/{old}")
    stored = store_ledger(name, ledger, graph=graph)
    if "error" in stored:
        return {"ledgerWarning": f"Ledger not updated: {stored['error']}"}
    return {"ledgerHash": stored["hash"], "edges": edges}


def _run_operations(name: str, operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one MAXScript program for the operation batch, run it, parse OPRES lines."""
    from .tyflow import HELPERS  # lazy: avoids import cycle

    op_blocks: list[str] = []
    for index, operation in enumerate(operations, start=1):
        body = _op_body(operation, index)
        indented = "\n        ".join(body.splitlines())
        op_blocks.append(
            f"""
    (
        local opok = true
        local operr = ""
        local detail = ""
        local applied = #()
        local errors = #()
        try (
            {indented}
        ) catch (opok = false; operr = (getCurrentException()) as string)
        local errJoin = ""
        for e in errors do (if errJoin != "" do errJoin += ";"; errJoin += e)
        format "OPRES|%|%|%|%\\n" {index} (if opok then "ok" else "err") (mcpTyClean (if opok then detail else operr)) (mcpTyClean errJoin) to:out
    )
"""
        )
    apply_script = f"""(
{HELPERS}
{CORE_HELPERS}
{PATCH_HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    local out = stringstream ""
    {"".join(op_blocks)}
    try (windows.processPostedMessages()) catch ()
    out as string
)
)"""
    raw, apply_error = _run_simple(apply_script)
    op_results: list[dict[str, Any]] = [
        {"index": i + 1, "op": operations[i].get("op"), "status": "unknown"}
        for i in range(len(operations))
    ]
    if apply_error:
        for result in op_results:
            result["status"] = "err"
            result["error"] = apply_error
        return op_results
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) >= 5 and parts[0] == "OPRES":
            try:
                slot = int(parts[1]) - 1
            except ValueError:
                continue
            if not 0 <= slot < len(op_results):
                continue
            op_results[slot]["status"] = parts[2]
            if parts[2] == "ok":
                if parts[3]:
                    op_results[slot]["detail"] = parts[3].replace("<pipe>", "|")
            else:
                op_results[slot]["error"] = parts[3].replace("<pipe>", "|")
            if parts[4]:
                op_results[slot]["propertyErrors"] = [
                    item.replace("<pipe>", "|") for item in parts[4].split(";") if item
                ]
    return op_results


def _inverse_operations(
    operations: list[dict[str, Any]],
    op_results: list[dict[str, Any]],
    pre_patch: dict[str, Any],
) -> list[dict[str, Any]]:
    """Synthesize inverse ops for the additive ops that succeeded, newest first."""
    pre_edges = {
        (edge["from_event"], edge["from_op"]): edge["to_event"]
        for edge in pre_patch.get("edges", [])
    }
    pre_positions = {
        canon_name(event["name"]): event.get("position")
        for event in pre_patch.get("events", [])
    }
    inverses: list[dict[str, Any]] = []
    for operation, result in zip(operations, op_results):
        if result.get("status") != "ok":
            continue
        kind = str(operation["op"])
        if kind == "add_event":
            inverses.append({"op": "remove_event", "event": str(operation["event"])})
        elif kind == "add_operator":
            inverses.append(
                {
                    "op": "remove_operator",
                    "event": str(operation["event"]),
                    "operator": str(operation.get("name") or operation["type"]),
                }
            )
        elif kind == "connect":
            from_event = str(operation["from_event"])
            from_operator = str(operation.get("from_operator") or "Send Out")
            if result.get("detail") == "created":
                inverses.append(
                    {"op": "remove_operator", "event": from_event, "operator": from_operator}
                )
                continue
            old_target = pre_edges.get((canon_name(from_event), canon_name(from_operator)))
            if old_target:
                inverses.append(
                    {
                        "op": "connect",
                        "from_event": from_event,
                        "from_operator": from_operator,
                        "to_event": old_target,
                        "ensure_send_out": False,
                    }
                )
            else:
                inverses.append(
                    {
                        "op": "disconnect",
                        "from_event": from_event,
                        "from_operator": from_operator,
                    }
                )
        elif kind == "set_event_position":
            old_position = pre_positions.get(canon_name(str(operation["event"])))
            if isinstance(old_position, (list, tuple)) and len(old_position) == 2:
                inverses.append(
                    {
                        "op": "set_event_position",
                        "event": str(operation["event"]),
                        "position": list(old_position),
                    }
                )
    inverses.reverse()
    return inverses


def _run_simple(maxscript: str) -> tuple[str, str | None]:
    """Run MAXScript, return (result, error)."""
    try:
        response = client.send_command(maxscript)
    except Exception as exc:
        return "", str(exc)
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return "", raw.split("|", 1)[1]
    return raw, None


def _create_checkpoint(name: str) -> tuple[str, str | None]:
    maxscript = f"""(
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    local ckptName = "zzz_mcp_ckpt_" + flow.name
    local stale = getNodeByName ckptName
    if stale != undefined do (try (delete stale) catch ())
    local nn = #()
    local okClone = false
    try (okClone = maxOps.cloneNodes flow cloneType:#copy newNodes:&nn) catch ()
    if okClone and nn.count > 0 then (
        nn[1].name = ckptName
        hide nn[1]
        try (windows.processPostedMessages()) catch ()
        "OK|" + ckptName
    ) else (
        "__ERROR__|Checkpoint clone failed"
    )
)
)"""
    raw, error = _run_simple(maxscript)
    if error:
        return "", error
    if raw.startswith("OK|"):
        return raw.split("|", 1)[1], None
    return "", f"Unexpected checkpoint response: {raw[:200]}"


def _rollback_checkpoint(name: str, ckpt_name: str) -> str | None:
    maxscript = f"""(
local ckpt = getNodeByName "{safe_string(ckpt_name)}"
if ckpt == undefined then (
    "__ERROR__|Checkpoint missing: {safe_string(ckpt_name)}"
) else (
    local broken = getNodeByName "{safe_string(name)}"
    if broken != undefined do (try (delete broken) catch ())
    ckpt.name = "{safe_string(name)}"
    unhide ckpt
    try (windows.processPostedMessages()) catch ()
    "OK"
)
)"""
    _, error = _run_simple(maxscript)
    return error


def _drop_checkpoint(ckpt_name: str) -> None:
    maxscript = f"""(
local ckpt = getNodeByName "{safe_string(ckpt_name)}"
if ckpt != undefined do (try (delete ckpt) catch ())
try (windows.processPostedMessages()) catch ()
"OK"
)"""
    _run_simple(maxscript)


def _run_verification(
    name: str, frames: list[int], min_particles: int, max_particles: int
) -> dict[str, Any]:
    frame_list = ", ".join(str(int(frame)) for frame in frames)
    maxscript = f"""(
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    local out = stringstream ""
    try (flow.tyFlow.reset_simulation()) catch ()
    for f in #({frame_list}) do (
        try (flow.tyFlow.updateParticles f) catch ()
        local c = -1
        try (c = flow.tyFlow.numParticles()) catch ()
        format "CNT|%|%\\n" (f as string) (c as string) to:out
    )
    out as string
)
)"""
    raw, error = _run_simple(maxscript)
    if error:
        return {"passed": False, "error": error, "counts": {}}
    counts: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) >= 3 and parts[0] == "CNT":
            try:
                counts[parts[1]] = int(parts[2])
            except ValueError:
                counts[parts[1]] = -1
    problems: list[str] = []
    for frame, count in counts.items():
        if count < 0:
            problems.append(f"Frame {frame}: particle count unreadable")
        if min_particles >= 0 and 0 <= count < min_particles:
            problems.append(f"Frame {frame}: {count} particles < min {min_particles}")
        if max_particles >= 0 and count > max_particles:
            problems.append(f"Frame {frame}: {count} particles > max {max_particles}")
    return {"passed": not problems, "counts": counts, "problems": problems}


@mcp.tool()
def tyflow_apply_patch(
    name: str,
    operations: DictList,
    expected_hash: str = "",
    checkpoint: bool = True,
    verify_frames: IntList | None = None,
    min_particles: int = -1,
    max_particles: int = -1,
    rollback_on_failure: bool = True,
    allow_executable: bool = False,
) -> str:
    """Apply a batch of tyFlow graph operations as one transaction.

    Operations (dicts with "op"): add_event, remove_event, add_operator,
    remove_operator, set_properties, connect, disconnect, rename_event,
    rename_operator, set_event_position, set_enabled. See _REQUIRED_FIELDS.

    `expected_hash` should be the `graph_hash` from a prior get_tyflow_graph —
    a mismatch means the flow changed since (user edit) and the patch is refused.
    With `verify_frames`, the sim is reset and particle counts asserted at those
    frames (min_particles/max_particles, -1 disables). On failure with
    `rollback_on_failure`: additive-only patches (add_event/add_operator/connect/
    set_event_position) roll back via synthesized inverse operations — no clone
    churn; patches containing other ops use a checkpoint clone that replaces the
    patched flow (caveat: external references to the flow node do not survive
    the swap). Script operators require safe mode off plus allow_executable=true.
    """
    operations = [
        dict(operation) if isinstance(operation, dict) else operation
        for operation in operations
    ]
    problem, needs_executable = _validate_operations(operations)
    if problem:
        return json.dumps({"error": problem})
    if needs_executable:
        denied = _authorize_executable(allow_executable)
        if denied:
            return json.dumps({"error": denied})

    with _TRANSACTION_LOCK:
        current = graph_payload(name)
        if "error" in current:
            return json.dumps(current)
        if expected_hash and expected_hash != current["graph_hash"]:
            return json.dumps(
                {
                    "error": "Graph hash conflict: the flow changed since it was last read.",
                    "expected_hash": expected_hash,
                    "current_hash": current["graph_hash"],
                    "hint": {"suggested_tools": ["get_tyflow_graph"]},
                }
            )

        # Clone checkpoints destabilize tyFlow when churned; purely additive
        # patches roll back via synthesized inverse operations instead.
        needs_clone = any(
            str(operation.get("op")) not in _ADDITIVE_OPS for operation in operations
        )
        ckpt_name = ""
        if checkpoint and needs_clone:
            ckpt_name, ckpt_error = _create_checkpoint(name)
            if ckpt_error:
                return json.dumps({"error": f"Checkpoint failed: {ckpt_error}"})

        op_results = _run_operations(name, operations)
        any_error = any(result["status"] != "ok" for result in op_results)

        verification: dict[str, Any] | None = None
        if not any_error and verify_frames:
            verification = _run_verification(
                name, list(verify_frames), int(min_particles), int(max_particles)
            )

        failed = any_error or bool(verification and not verification["passed"])
        payload: dict[str, Any] = {
            "name": name,
            "operations": op_results,
            "failed": failed,
            "rolled_back": False,
        }
        if verification is not None:
            payload["verification"] = verification

        if failed:
            if rollback_on_failure and ckpt_name:
                payload["rollback_mode"] = "checkpoint"
                rollback_error = _rollback_checkpoint(name, ckpt_name)
                if rollback_error:
                    payload["rollback_error"] = rollback_error
                    payload["checkpoint"] = ckpt_name
                else:
                    payload["rolled_back"] = True
                    # maxOps.cloneNodes does not carry AppData (live-verified),
                    # so the restored node needs the pre-patch ledger rewritten.
                    restored = write_ledger_appdata(name, current["ledger"])
                    payload["ledger_restored"] = "error" not in restored
                    if "error" in restored:
                        payload["ledgerWarning"] = restored["error"]
            elif rollback_on_failure and not needs_clone:
                payload["rollback_mode"] = "inverse"
                inverse_ops = _inverse_operations(operations, op_results, current)
                if inverse_ops:
                    inverse_results = _run_operations(name, inverse_ops)
                    payload["rollback_operations"] = inverse_results
                    payload["rolled_back"] = all(
                        result["status"] == "ok" for result in inverse_results
                    )
                else:
                    payload["rolled_back"] = True
                # Ledger appdata was never touched on this path; the restored
                # structure matches the pre-patch hash, so it stays fresh.
            elif ckpt_name:
                payload["checkpoint"] = ckpt_name
                payload["ledger"] = ledger_update(name)
            else:
                payload["ledger"] = ledger_update(name)
            return json.dumps(payload)

        if ckpt_name:
            _drop_checkpoint(ckpt_name)
        ledger_outcome = _apply_ledger_mutations(
            name, _ledger_mutations(operations, op_results)
        )
        payload.update(ledger_outcome)
        if "ledgerHash" in ledger_outcome:
            payload["graph_hash"] = ledger_outcome["ledgerHash"]
        return json.dumps(payload)
