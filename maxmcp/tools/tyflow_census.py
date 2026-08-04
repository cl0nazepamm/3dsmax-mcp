"""Per-event particle census and editor capture for tyFlow agentic verification.

tyFlow exposes no per-event particle counts, and a particle's current event is
not queryable. The census instruments the flow instead: a Mapping (or group
flag) operator tagged `zzz_mcp_census` is appended to every event, stamping the
event's ordinal into a UVW channel (or an export-group bit) on every particle in
that event; the sim is then evaluated at probe frames and per-particle reads are
bucketed per event. Instrumentation mutates the flow — it is removed afterwards
(also on error), but treat this as an opt-in diagnostic, not a free read.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..helpers.maxscript import safe_string

from ..coerce import IntList
from ..server import client, mcp
from ._tyflow_core import CORE_HELPERS, graph_payload

_CENSUS_OP_NAME = "zzz_mcp_census"

# tyFlow Mapping operator: `mappingMode` index of "Mapping from value" — the mode
# that stamps `mappingValue` onto `UVWAxis` of `mapChannel` (`mappingOperation`
# stays 0 = Set). Live-verified on tyFlow 200500; override per-call for other versions.
MAPPING_VALUE_MODE = 7


@mcp.tool()
def tyflow_event_census(
    name: str,
    frames: IntList | None = None,
    method: str = "mapping",
    channel: int = 99,
    max_particles_sampled: int = 20000,
    cleanup: bool = True,
    mapping_mode: int = -1,
) -> str:
    """Per-event particle counts at probe frames via temporary instrumentation.

    method=mapping stamps event ordinals into UVW `channel` (unlimited events);
    method=groups uses export-group bits (16-event limit). The flow is mutated
    for the duration of the call (operators named zzz_mcp_census) and cleaned up
    unless cleanup=false. Response includes which properties the instrumentation
    resolved, so failures are debuggable.
    """
    method_key = method.strip().lower()
    if method_key not in {"mapping", "groups"}:
        return json.dumps({"error": f"Unknown method: {method}. Use mapping or groups."})
    frame_values = [int(frame) for frame in (frames or [0])]
    frame_list = ", ".join(str(frame) for frame in frame_values)
    cap = max(1, int(max_particles_sampled))
    op_mode = MAPPING_VALUE_MODE if int(mapping_mode) < 0 else int(mapping_mode)

    if method_key == "mapping":
        instrument_block = f"""
        chanProp = "mapChannel"
        valProp = "mappingValue"
        try (op.mappingMode = {op_mode}) catch (append instErrs ("set mappingMode failed: " + evNames[ei]))
        try (op.mappingOperation = 0) catch (append instErrs ("set mappingOperation failed: " + evNames[ei]))
        try (op.mappingValue = ei as float) catch (append instErrs ("set mappingValue failed: " + evNames[ei]))
        try (op.UVWAxis = 0) catch (append instErrs ("set UVWAxis failed: " + evNames[ei]))
        try (op.mapChannel = {int(channel)}) catch (append instErrs ("set mapChannel failed: " + evNames[ei]))
"""
        operator_type = "Mapping"
        read_expr = f"""
                local uvw = undefined
                try (uvw = flow.tyFlow.getParticleUVW i {int(channel)}) catch ()
                local ordinal = 0
                if uvw != undefined do ordinal = (uvw.x + 0.5) as integer
"""
    else:
        instrument_block = """
        local pNames = #()
        try (pNames = getPropNames op) catch ()
        local flagBase = ""
        for p in pNames do (
            local pn = toLower (p as string)
            if flagBase == "" and (findString pn "exportgroup") != undefined do (
                local base = p as string
                local underscore = findString base "_"
                if underscore != undefined do flagBase = substring base 1 underscore
            )
        )
        if flagBase == "" then append instErrs ("Export-group props unresolved: " + evNames[ei])
        else (
            local letters = #("A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P")
            local flagName = flagBase + letters[ei]
            try (setProperty op (flagName as name) true) catch (append instErrs ("set " + flagName + " failed: " + evNames[ei]))
        )
"""
        operator_type = "Particle Groups"
        read_expr = """
                local mask = 0
                try (mask = flow.tyFlow.getParticleExportGroups i) catch ()
                local ordinal = 0
                local probe = mask
                while probe > 0 and ordinal < 17 do (ordinal += 1; if (mod probe 2) >= 1 then probe = 0 else probe = probe / 2)
"""

    maxscript = f"""(
{CORE_HELPERS}
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    local out = stringstream ""
    local bo = flow.baseobject
    local evNames = mcpTyParseSubNames bo
    local instErrs = #()
    local chanProp = ""
    local valProp = ""
    local mainErr = ""
    try (
        if evNames.count == 0 do throw "Flow has no events"
        {"if evNames.count > 16 do throw \"groups method supports at most 16 events\"" if method_key == "groups" else ""}
        for ei = 1 to evNames.count do (
            local ev = mcpTySubAnimByName bo evNames[ei]
            local realEv = undefined
            try (realEv = ev.object) catch ()
            if realEv == undefined do realEv = ev
            local op = undefined
            try (op = realEv.Event.addOperator "{operator_type}" -1) catch ()
            if op == undefined then (
                append instErrs ("addOperator {operator_type} failed: " + evNames[ei])
            ) else (
                op.Operator.setName "{_CENSUS_OP_NAME}"
                {instrument_block}
            )
        )
        try (flow.tyFlow.reset_simulation()) catch ()
        for f in #({frame_list}) do (
            try (flow.tyFlow.updateParticles f) catch ()
            local total = 0
            try (total = flow.tyFlow.numParticles()) catch ()
            local take = total
            if take > {cap} do take = {cap}
            local counts = #()
            for ci = 1 to evNames.count do append counts 0
            local unknown = 0
            for i = 1 to take do (
                {read_expr}
                if ordinal >= 1 and ordinal <= evNames.count then counts[ordinal] += 1 else unknown += 1
            )
            for ci = 1 to evNames.count do (
                format "CEN|%|%|%\\n" (f as string) (mcpTyClean evNames[ci]) (counts[ci] as string) to:out
            )
            format "SAMPLED|%|%|%|%\\n" (f as string) (take as string) (total as string) (unknown as string) to:out
        )
    ) catch (mainErr = (getCurrentException()) as string)
    {"" if not cleanup else '''
    for ei = 1 to evNames.count do (
        local ev = mcpTySubAnimByName bo evNames[ei]
        local guard = 0
        while guard < 8 do (
            guard += 1
            local censusOp = mcpTySubAnimByName ev "''' + _CENSUS_OP_NAME + '''"
            if censusOp == undefined then guard = 99
            else (
                local realOp = undefined
                try (realOp = censusOp.object) catch ()
                if realOp == undefined do realOp = censusOp
                try (realOp.remove()) catch (guard = 99)
            )
        )
    )
    '''}
    if mainErr != "" do format "MAINERR|%\\n" (mcpTyClean mainErr) to:out
    for e in instErrs do format "INSTERR|%\\n" (mcpTyClean e) to:out
    format "RESOLVED|%|%\\n" (mcpTyClean chanProp) (mcpTyClean valProp) to:out
    out as string
)
)"""
    try:
        response = client.send_command(maxscript, timeout=600)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return json.dumps({"error": raw.split("|", 1)[1]})

    def _decode(token: str) -> str:
        return token.replace("<pipe>", "|")

    frames_out: dict[str, dict[str, int]] = {}
    sampled: dict[str, dict[str, int]] = {}
    instrumentation: dict[str, Any] = {"method": method_key, "errors": []}
    if method_key == "mapping":
        instrumentation["mappingMode"] = op_mode
    main_error = ""
    for line in raw.splitlines():
        parts = line.split("|")
        tag = parts[0] if parts else ""
        if tag == "CEN" and len(parts) >= 4:
            frame_bucket = frames_out.setdefault(parts[1], {})
            try:
                frame_bucket[_decode(parts[2])] = int(parts[3])
            except ValueError:
                pass
        elif tag == "SAMPLED" and len(parts) >= 5:
            try:
                sampled[parts[1]] = {
                    "sampled": int(parts[2]),
                    "total": int(parts[3]),
                    "unbucketed": int(parts[4]),
                }
            except ValueError:
                pass
        elif tag == "INSTERR" and len(parts) >= 2:
            instrumentation["errors"].append(_decode("|".join(parts[1:])))
        elif tag == "MAINERR" and len(parts) >= 2:
            main_error = _decode("|".join(parts[1:]))
        elif tag == "RESOLVED" and len(parts) >= 3:
            instrumentation["channelProperty"] = _decode(parts[1])
            instrumentation["valueProperty"] = _decode(parts[2])
    payload: dict[str, Any] = {
        "name": name,
        "frames": frames_out,
        "sampling": sampled,
        "instrumentation": instrumentation,
        "cleanup": bool(cleanup),
    }
    if main_error:
        payload["error"] = main_error
    return json.dumps(payload)


@mcp.tool()
def capture_tyflow_editor(
    name: str,
    enabled: bool = False,
    output_path: str = "",
    reset_view: bool = True,
    max_width: int = 1920,
) -> str:
    """Open the tyFlow editor and capture the screen for visual wire inspection.

    Wiring of flows not built through MCP cannot be read structurally; this
    capture plus the returned event rectangles (position/width per event) lets
    an agent map wires visually and reconcile them via set_tyflow_wiring_ledger.
    Fullscreen capture is disabled by default — pass enabled=true. The editor is
    left open afterwards.
    """
    if not enabled:
        return json.dumps(
            {
                "error": "capture_tyflow_editor is disabled by default; "
                "set enabled=true to allow fullscreen capture"
            }
        )
    from .viewport import _capture_fullscreen_to_file

    reset_lines = ""
    if reset_view:
        reset_lines = (
            "try (flow.tyFlow.editor_resetPan()) catch ()\n"
            "    try (flow.tyFlow.editor_resetZoom()) catch ()"
        )
    maxscript = f"""(
local flow = getNodeByName "{safe_string(name)}"
if flow == undefined then (
    "__ERROR__|Object not found: {safe_string(name)}"
) else (
    try (flow.tyFlow.editor_open()) catch ()
    {reset_lines}
    try (windows.processPostedMessages()) catch ()
    sleep 0.4
    try (windows.processPostedMessages()) catch ()
    "OK"
)
)"""
    try:
        response = client.send_command(maxscript)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return json.dumps({"error": raw.split("|", 1)[1]})

    if output_path:
        capture_path = output_path.replace("\\", "/")
    else:
        root = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        target_dir = Path(root) / "3dsmax-mcp"
        target_dir.mkdir(parents=True, exist_ok=True)
        capture_path = str(target_dir / "tyflow_editor_capture.jpg").replace("\\", "/")
    try:
        _capture_fullscreen_to_file(capture_path, max_width=int(max_width))
    except Exception as exc:
        return json.dumps({"error": f"Screen capture failed: {exc}"})

    graph = graph_payload(name)
    events = [
        {"name": event["name"], "position": event.get("position"), "width": event.get("width")}
        for event in graph.get("events", [])
    ] if "error" not in graph else []
    return json.dumps(
        {
            "name": name,
            "imagePath": capture_path,
            "events": events,
            "ledger_status": graph.get("ledger_status"),
            "note": "tyFlow editor left open; wires must be read visually from the image.",
        }
    )
