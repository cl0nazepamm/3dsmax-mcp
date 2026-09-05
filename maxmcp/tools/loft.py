"""Quad loft construction with node-owned, versioned arithmetic definitions."""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from ..coerce import DictValue
from ..helpers.loft import build_loft, validate_parameters
from ..helpers.maxscript import safe_string
from ..helpers.mesh import MESH_FUNCTIONS, point, target_script, vector
from ..server import client, mcp


LOFT_APPDATA_ID = 1280263764  # "LOFT"; private MAXScript AppData slot.
SCHEMA = "3dsmax-mcp/loft"
VERSION = 1
HISTORY_LIMIT = 16
MAX_DEFINITION_BYTES = 4_000_000
_TOKEN_MARKER = "__MCP_LOFT_CAGE_TOKEN__"
_FINGERPRINT = re.compile(r"[0-9A-F]{2}(?:-[0-9A-F]{2}){31}")

# Identity/transform/selection are intentionally absent. Parameter edits follow
# the node after transforms, preserve stack/material edits, and detect changes
# to its local cage, topology or live/dead component slots.
LOFT_FUNCTIONS = r'''
fn lfToken obj = (
    if classof obj.baseobject != Editable_Poly do throw "Loft requires its original Editable Poly base"
    local mesh = obj.baseobject
    local ss = stringstream ""
    format "%|%|%|%|" (mcCounts mesh) (polyop.getDeadVerts mesh) (polyop.getDeadEdges mesh) (polyop.getDeadFaces mesh) to:ss
    for i = 1 to polyop.getNumVerts mesh do (
        local p = polyop.getVert mesh i
        for axis = 1 to 3 do format "%;" (formattedPrint p[axis] format:".9g") to:ss
    )
    for i = 1 to polyop.getNumEdges mesh do format "E%;" (polyop.getEdgeVerts mesh i) to:ss
    for i = 1 to polyop.getNumFaces mesh do format "F%;" (polyop.getFaceVerts mesh i) to:ss
    local sha = (dotNetClass "System.Security.Cryptography.SHA256").Create()
    local bytes = (dotNetClass "System.Text.Encoding").UTF8.GetBytes (ss as string)
    local digest = (dotNetClass "System.BitConverter").ToString (sha.ComputeHash bytes)
    sha.Dispose()
    digest as string
)
fn lfBase64 text = ((dotNetClass "System.Convert").ToBase64String ((dotNetClass "System.Text.Encoding").UTF8.GetBytes text))
'''


def _json(data: dict) -> str:
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    # The creation/update hook replaces this marker with a 95-character SHA256
    # before storage. Count that expansion so a near-limit valid definition
    # cannot commit successfully and then fail its own readback size check.
    stored_size = len(text.encode("ascii")) + text.count(_TOKEN_MARKER) * (95 - len(_TOKEN_MARKER))
    if stored_size > MAX_DEFINITION_BYTES:
        raise ValueError("Loft definition exceeds the 4 MB storage limit")
    return text


def _literal(text: str) -> str:
    # Base64 prevents coordinate expressions and node names from introducing
    # string escapes or MAXScript syntax through persisted data.
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f'((dotNetClass "System.Text.Encoding").UTF8.GetString ((dotNetClass "System.Convert").FromBase64String "{encoded}"))'


def _run(script: str) -> str:
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    if not raw:
        raise RuntimeError("Loft returned no readback; inspect before retrying")
    return raw


def _parse_definition(raw: str) -> dict:
    if len(raw.encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise ValueError("Stored loft definition exceeds the 4 MB storage limit")
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("Stored loft definition is invalid JSON") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("version") != VERSION:
        raise ValueError("Unsupported or missing loft definition version")
    if not isinstance(data.get("definition"), dict) or set(data["definition"]) != {
        "sections", "profile_closed", "close_path", "caps", "reverse"
    }:
        raise ValueError("Stored loft definition has an invalid construction shape")
    vector(data.get("origin"), "Stored loft origin")
    history = data.get("history")
    if not isinstance(history, list) or not 1 <= len(history) <= HISTORY_LIMIT:
        raise ValueError("Stored loft parameter history is invalid")
    parameter_names = None
    for row in history:
        if (not isinstance(row, dict) or set(row) != {"fingerprint", "parameters"}
                or not isinstance(row.get("fingerprint"), str)
                or not _FINGERPRINT.fullmatch(row["fingerprint"])
                or not isinstance(row["parameters"], dict)):
            raise ValueError("Stored loft cage fingerprint is invalid")
        params = validate_parameters(row["parameters"])
        if parameter_names is not None and set(params) != parameter_names:
            raise ValueError("Stored loft parameter names changed across revisions")
        parameter_names = set(params)
    return data


def _read(name: str, handle: int) -> tuple[dict, dict, str]:
    raw = _run(f'''(
        {MESH_FUNCTIONS}
        {LOFT_FUNCTIONS}
        try (
            {target_script(name, handle)}
            local data = getAppData obj {LOFT_APPDATA_ID}
            if data == undefined do throw "Node has no loft definition"
            local token = lfToken obj
            local instances = #()
            InstanceMgr.GetInstances obj &instances
            local out = stringstream ""
            format "NAME|%\\n" (lfBase64 obj.name) to:out
            format "META|%|%|%|%|%|%\\n" (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") (mcCounts obj.baseobject) obj.modifiers.count instances.count token (mcToken obj) to:out
            format "DATA|%\\n" (lfBase64 data) to:out
            out as string
        ) catch ("__ERROR__|" + (getCurrentException() as string))
    )''')
    rows = dict(line.split("|", 1) for line in raw.splitlines() if "|" in line)
    try:
        fields = rows["META"].split("|")
        if len(fields) != 6:
            raise ValueError("invalid metadata")
        definition_raw = base64.b64decode(rows["DATA"], validate=True).decode("utf-8")
        identity = {"name": base64.b64decode(rows["NAME"], validate=True).decode("utf-8"),
                    "handle": int(fields[0]),
                    "counts": dict(zip(("vertices", "edges", "faces"), map(int, fields[1].split(",")))),
                    "modifiers_above": int(fields[2]), "instance_count": int(fields[3]),
                    "cage_fingerprint": fields[4], "mesh_token": fields[5]}
    except (KeyError, ValueError, UnicodeError) as exc:
        raise RuntimeError("Invalid loft readback; no write attempted") from exc
    return identity, _parse_definition(definition_raw), definition_raw


def _active_revision(data: dict, fingerprint: str) -> int | None:
    matched = None
    for index in range(len(data["history"]) - 1, -1, -1):
        if data["history"][index]["fingerprint"] == fingerprint:
            if matched is not None and data["history"][index]["parameters"] != data["history"][matched]["parameters"]:
                return None  # Geometry alone cannot disambiguate these states.
            matched = index if matched is None else matched
    return matched


def _public(identity: dict, data: dict, *, include_definition: bool) -> dict:
    index = _active_revision(data, identity["cage_fingerprint"])
    result = {
        **identity, "definition_version": VERSION,
        "parameters": data["history"][index]["parameters"] if index is not None else None,
        "cage_matches_definition": index is not None,
        "retained_parameter_states": len(data["history"]),
        "matched_parameter_state": index + 1 if index is not None else None,
        "origin": data["origin"],
        "construction_space": "Initial world coordinates relative to the stored origin; follows the node transform thereafter",
        "notes": [
            "Parameter updates preserve the node transform and modifier stack; manual cage/topology changes block updates.",
            "AppData stores up to 16 parameter states. A geometry undo is recognized by its cage fingerprint; older/unmatched cages are rejected.",
        ],
    }
    if include_definition:
        result["definition"] = data["definition"]
    if index is None:
        result["reason"] = "Cage changed manually or is outside retained parameter history; no safe parameter update"
    return result


@mcp.tool()
def loft_mesh(
    action: str = "create",
    name: str = "",
    handle: int = 0,
    sections: list[list[list[Any]]] | None = None,
    parameters: DictValue | None = None,
    profile_closed: bool = True,
    close_path: bool = False,
    caps: bool = False,
    reverse: bool = False,
    include_definition: bool = False,
) -> dict[str, Any]:
    """Create/read/update a parameterized quad loft stored on its mesh node in the .max file.

    create: name + sections [[[x,y,z],...],...], with matched point counts and
    corresponding point order. Coordinates start in world space. profile_closed
    wraps each section; close_path joins last section to first. Never duplicate
    the first point/section. caps adds n-gon ends only for closed profiles/open
    paths (planar, simple end profiles recommended); reverse flips all winding.
    Side faces stay quads. No automatic resampling, smoothing or intersection repair.
    Coordinates may be numbers or arithmetic strings using numeric parameters:
    '-width/2', 'radius*cos(pi/4)'. Supports + - * / % **, sin/cos (radians),
    sqrt, abs, min/max, pi; no Python/MAXScript execution or external dependencies.
    read: name/handle (or one selected node) returns compact parameters/counts/state.
    include_definition=true adds the full source sections on any action; default
    false avoids returning large construction arrays just to query a dimension.
    update: name/handle + parameters to change existing named values. Keeps vertex
    IDs, node placement, materials and modifiers. Rejects manual cage/topology
    changes and instanced bases. Definition/options cannot change during update.
    Creation + definition share one hold; updates roll back geometry and AppData
    on failure. Up to 16 cage/parameter states are retained to recognize geometry
    undo without depending on AppData undo behavior. Older unmatched cages fail.
    """
    action = action.strip().lower()
    if action not in {"create", "read", "update"}:
        raise ValueError("action must be create, read or update")
    if not isinstance(include_definition, bool):
        raise ValueError("include_definition must be boolean")
    if action == "create":
        from .mesh_ops import _create_mesh
        if handle:
            raise ValueError("Creation requires a new name, not an existing handle")
        if not name.strip():
            raise ValueError("name is required for creation")
        values = validate_parameters(parameters)
        vertices, faces, definition = build_loft(
            sections, values, profile_closed=profile_closed, close_path=close_path,
            caps=caps, reverse=reverse)
        origin = [(min(p[a] for p in vertices) + max(p[a] for p in vertices)) * 0.5 for a in range(3)]
        data = {"schema": SCHEMA, "version": VERSION, "origin": origin,
                "definition": definition, "history": [{"parameters": values, "fingerprint": _TOKEN_MARKER}]}
        encoded = _literal(_json(data))
        hook = f'''
            {LOFT_FUNCTIONS}
            local loftData = substituteString {encoded} "{_TOKEN_MARKER}" (lfToken obj)
            setAppData obj {LOFT_APPDATA_ID} loftData
            if getAppData obj {LOFT_APPDATA_ID} != loftData do throw "Loft definition did not persist on the node"
        '''
        created = _create_mesh(name, vertices, faces, before_accept=hook)
        # Read back the node-owned definition, not the intended Python data.
        identity, stored, _ = _read(name, created["handle"])
        return {"action": action, **_public(identity, stored, include_definition=include_definition)}
    if sections is not None:
        raise ValueError("sections are creation-only; update changes named parameters without changing topology")
    if profile_closed is not True or close_path is not False or caps is not False or reverse is not False:
        raise ValueError("Loft closure/caps/winding options are creation-only")
    if action == "read" and parameters is not None:
        raise ValueError("read does not change parameters; use action=update")
    changes = validate_parameters(parameters)
    if action == "update" and not changes:
        raise ValueError("update requires at least one named parameter")
    identity, data, old_raw = _read(name, handle)
    if action == "read":
        return {"action": action, **_public(identity, data, include_definition=include_definition)}
    revision = _active_revision(data, identity["cage_fingerprint"])
    if revision is None:
        raise RuntimeError("Loft cage changed manually or is outside retained parameter history; inspect before rebuilding")
    if identity["instance_count"] > 1:
        raise RuntimeError("Loft base is instanced; make it unique before changing construction parameters")
    current = data["history"][revision]["parameters"]
    if set(changes) - set(current):
        raise ValueError(f"Unknown construction parameters: {sorted(set(changes) - set(current))}")
    updated = {**current, **changes}
    if updated == current:
        return {"action": action, **_public(identity, data, include_definition=include_definition),
                "changed_parameters": {}, "unchanged": True}
    vertices, faces, _ = build_loft(parameters=updated, **data["definition"])
    if len(vertices) != identity["counts"]["vertices"] or len(faces) != identity["counts"]["faces"]:
        raise RuntimeError("Stored loft topology no longer matches the cage")
    local_vertices = [[p[a] - data["origin"][a] for a in range(3)] for p in vertices]
    previous_states = data["history"]
    data["history"] = [*previous_states, {"parameters": updated, "fingerprint": _TOKEN_MARKER}][-HISTORY_LIMIT:]
    # AppData is not assumed undoable. Different parameter vectors that produce
    # the same float cage cannot be distinguished after geometry undo. Reject
    # those aliases before writing the next definition instead of guessing.
    alias_guards = "\n".join(
        f'if fingerprint == "{row["fingerprint"]}" do throw "Different parameter values produce an existing cage state; undo would be ambiguous"'
        for row in previous_states if row["parameters"] != updated
    )
    new_raw = _json(data)
    points = "#(" + ",".join(point(p) for p in local_vertices) + ")"
    raw = _run(f'''(
        {MESH_FUNCTIONS}
        {LOFT_FUNCTIONS}
        local holding = false
        local previousData = undefined
        local targetNode = undefined
        try (
            {target_script(identity['name'], identity['handle'])}
            targetNode = obj
            previousData = getAppData obj {LOFT_APPDATA_ID}
            if previousData != {_literal(old_raw)} do throw "Loft definition changed during parameter evaluation; read again"
            if lfToken obj != "{safe_string(identity['cage_fingerprint'])}" do throw "Loft cage changed during parameter evaluation; inspect again"
            local instances = #()
            InstanceMgr.GetInstances obj &instances
            if instances.count > 1 do throw "Loft base became instanced; make unique first"
            if theHold.Holding() do throw "An undo transaction is active"
            theHold.Begin(); holding = true
            local mesh = obj.baseobject
            local points = {points}
            for i = 1 to points.count do polyop.setVert mesh i points[i]
            update obj
            local fingerprint = lfToken obj
            {alias_guards or 'true'}
            local newData = substituteString {_literal(new_raw)} "{_TOKEN_MARKER}" fingerprint
            setAppData obj {LOFT_APPDATA_ID} newData
            if getAppData obj {LOFT_APPDATA_ID} != newData do throw "Loft definition update was not stored"
            local result = lfToken obj
            theHold.Accept "MCP loft parameters"; holding = false
            try (completeredraw()) catch ()
            result
        ) catch (
            local detail = getCurrentException() as string
            if holding do (
                theHold.Cancel(); holding = false
                try (if isValidNode targetNode and previousData != undefined do setAppData targetNode {LOFT_APPDATA_ID} previousData) catch (detail += "; AppData rollback failed")
            )
            "__ERROR__|" + detail
        )
    )''')
    if not _FINGERPRINT.fullmatch(raw):
        raise RuntimeError("Loft update returned unexpected readback; inspect before retrying")
    identity, stored, _ = _read(identity["name"], identity["handle"])
    if identity["cage_fingerprint"] != raw:
        raise RuntimeError("Loft update committed, but its cage changed before verification; inspect before retrying")
    result = {"action": action, **_public(identity, stored, include_definition=include_definition)}
    if result["parameters"] != updated:
        raise RuntimeError("Loft update committed, but stored parameters could not be verified; read before retrying")
    result["changed_parameters"] = {key: {"before": current[key], "after": updated[key]} for key in changes}
    return result
