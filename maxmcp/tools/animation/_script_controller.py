"""Guarded script-controller authoring over the existing main-thread bridge.

One generated MAXScript call owns resolution, staging, evaluation and the undo
hold. Native and TCP clients execute the same program. Source is compiled as a
function first so syntax, runtime and output-type errors retain useful details;
the real controller is then evaluated too. This is not a MAXScript sandbox.
"""
from __future__ import annotations

import json
import math
import re

from ...assets import ASSETS
from ...core.maxscript import NODE_LOOKUP_FNS


def literal(value):
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (float, int)):
        if not math.isfinite(value):
            raise ValueError("Numbers must be finite")
        return str(value)
    if isinstance(value, str):
        if "\0" in value:
            raise ValueError("Strings cannot contain NUL")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'
    if isinstance(value, list):
        if len(value) > 256:
            raise ValueError("Constant arrays are limited to 256 elements")
        return "#(" + ",".join(literal(v) for v in value) + ")"
    raise ValueError("Unsupported constant; use numbers, strings, booleans or arrays")


def node_ref(ref):
    if not isinstance(ref, dict) or set(ref) - {"name", "handle"}:
        raise ValueError("node accepts name and/or animatable handle")
    name, handle = ref.get("name", ""), ref.get("handle", 0)
    if not isinstance(name, str) or isinstance(handle, bool) or not isinstance(handle, int) or not 0 <= handle < 2**63:
        raise ValueError("Invalid node name or handle")
    if not name and not handle:
        raise ValueError("A node name or handle is required")
    return [name, handle]


def track_steps(track):
    aliases = {"position": "[#transform][#position]", "rotation": "[#transform][#rotation]",
               "scale": "[#transform][#scale]", "transform": "[#transform]"}
    if isinstance(track, list):
        if not track or len(track) > 32:
            raise ValueError("Track requires 1..32 sub-anim indices or names")
        steps = []
        for item in track:
            if isinstance(item, bool) or not isinstance(item, (str, int)) or (isinstance(item, int) and item < 1):
                raise ValueError("Track indices are positive, 1-based integers")
            if isinstance(item, str) and not re.fullmatch(r"[\w ()]+", item):
                raise ValueError("Invalid sub-anim name")
            steps.append(["sub", item.replace(" ", "_").lower() if isinstance(item, str) else item])
        return steps
    if not isinstance(track, str):
        raise ValueError("track must be a discovered path, alias, or sub-anim array")
    path = aliases.get(track, track).lstrip(".")
    steps = []
    # Parse data, never execute a user-supplied track expression.
    while path:
        prop = re.match(r"^(baseObject|modifiers)(?=\[|\.|$)", path, re.I)
        sub = re.match(r"^\[(?:#([\w ()]+)|([1-9][0-9]*))\]", path)
        if prop:
            steps.append(["prop", prop[1].lower()]); path = path[prop.end():]
        elif sub:
            steps.append(["sub", sub[1].replace(" ", "_").lower() if sub[1] else int(sub[2])]); path = path[sub.end():]
        else:
            raise ValueError("Unsupported track path; use inspect_track_view paths or numeric sub-anim arrays")
        path = path.lstrip(".")
    if not steps or len(steps) > 32:
        raise ValueError("Track requires 1..32 path components")
    return steps


def target_ref(target):
    if not isinstance(target, dict) or set(target) != {"node", "track"}:
        raise ValueError("target requires exactly node and track")
    return [node_ref(target["node"]), track_steps(target["track"])]


def binding_specs(bindings):
    if bindings is None:
        return None
    if not isinstance(bindings, dict) or len(bindings) > 64:
        raise ValueError("bindings must contain at most 64 named inputs")
    result, seen = [], set()
    for name, spec in bindings.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name.lower() in {"t", "s", "f", "nt", "this"} or name.lower().startswith("__mcp"):
            raise ValueError(f"Invalid or reserved binding name: {name}")
        if name.lower() in seen:
            raise ValueError(f"Duplicate case-insensitive binding: {name}")
        seen.add(name.lower())
        if not isinstance(spec, dict):
            raise ValueError(f"Binding {name} requires a typed specification")
        kind = spec.get("kind")
        allowed = {"constant": {"kind", "value", "value_type"}, "node": {"kind", "node"},
                   "track": {"kind", "target", "offset_frames"}, "controller": {"kind", "target"}}
        if kind not in allowed or set(spec) - allowed[kind]:
            raise ValueError(f"Invalid binding kind or fields: {name}")
        offset = spec.get("offset_frames", 0)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset):
            raise ValueError("offset_frames must be finite")
        if kind == "constant":
            value = spec["value"]
            value_type = spec.get("value_type", "value")
            if value_type not in {"value", "point3", "quat"}:
                raise ValueError("constant value_type must be value, point3 or quat")
            literal(value)
            if value_type != "value":
                size = 3 if value_type == "point3" else 4
                if not isinstance(value, list) or len(value) != size or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
                    raise ValueError(f"{value_type} needs {size} numeric components")
            data = [value_type, value]
        elif kind == "node":
            data = node_ref(spec["node"])
        else:
            data = target_ref(spec["target"])
        result.append([name, kind, data, offset])
    return result


def build_program(action, target, script=None, bindings=None, expected_controller=None, sample_frames=None):
    if action not in {"inspect", "validate", "apply"}:
        raise ValueError("action must be inspect, validate or apply")
    if action == "inspect" and (script is not None or bindings is not None):
        raise ValueError("inspect does not accept a proposed script or bindings")
    if action != "inspect" and (not isinstance(script, str) or not script.strip() or len(script) > 65536):
        raise ValueError("validate/apply requires a nonempty script of at most 65536 characters")
    if action == "apply" and not expected_controller:
        raise ValueError("apply requires expected_controller from inspect or validate")
    if expected_controller is not None and not re.fullmatch(r"[0-9A-F]{2}(?:-[0-9A-F]{2}){31}", expected_controller):
        raise ValueError("Invalid controller token")
    frames = sample_frames
    if frames is not None:
        if not isinstance(frames, list) or not 1 <= len(frames) <= 64 or any(isinstance(f, bool) or not isinstance(f, (int, float)) or not math.isfinite(f) or abs(f) > 1000000 for f in frames):
            raise ValueError("sample_frames requires 1..64 finite frames in +/-1000000")
    args = [action, target_ref(target), script, binding_specs(bindings), expected_controller, frames]
    runtime = (ASSETS / "script_controller.ms").read_text(encoding="utf-8")
    return "(\n" + NODE_LOOKUP_FNS + runtime + "\nscRun " + " ".join(literal(a) for a in args) + "\n)"


def run(action, target, script=None, bindings=None, expected_controller=None, sample_frames=None):
    from ...server import client
    try:
        program = build_program(action, target, script, bindings, expected_controller, sample_frames)
    except (ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error": {"code": "BAD_PARAM", "stage": "request", "message": str(exc), "retryable": False}}
    # Compile the fixed runtime inside an error boundary as well. A syntax error
    # in bridge-generated code must not escape to Max's compiler error dialog.
    response = client.send_command("(try (execute " + literal(program) + ") catch (\"__MCP_MS_ERR__:\" + getCurrentException()))")
    raw = response.get("result", "")
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or "ok" not in result:
            raise ValueError("Missing result envelope")
        if result["ok"]:
            data = result["result"]
            if "bindings" in data:
                data["bindings"] = [dict(zip(("name", "kind", "value", "offset_ticks", "reference_handle"), row))
                                    for row in data["bindings"]]
            if "samples" in data:
                data["samples"] = [{"frame": row[0], "value": row[1]} for row in data["samples"]]
        return result
    except (ValueError, TypeError):
        return {"ok": False, "error": {"code": "SCRIPT_CONTROLLER_INTERNAL", "stage": "bridge",
                "message": str(raw) or "No controller readback; inspect before retrying", "retryable": False}}
