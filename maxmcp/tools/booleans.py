"""Boolean modeling via the modern Boolean modifier (BooleanMod, Max 2022+).

One tool, action-dispatched: apply operands (union/subtract/intersect/...),
list the operand stack, retune/rename/disable an operand, remove or extract
one.  Non-live appends CONSUME the operand node (its geometry is captured into
the modifier and the scene node is deleted) — the operand keeps its node name
inside the modifier, so name cutters after the feature they cut.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from ..coerce import DictList, DictValue, StrList
from ..helpers.maxscript import safe_string
from ..server import client, mcp

# Agent-friendly tokens -> MAXScript operationType enums.
OPERATIONS = {
    "union": "#union",
    "add": "#union",
    "subtract": "#subtraction",
    "subtraction": "#subtraction",
    "difference": "#subtraction",
    "intersect": "#intersection",
    "intersection": "#intersection",
    "merge": "#merge",
    "attach": "#attach",
    "insert": "#insert",
    "split": "#split",
}
OPTIONS = {"": "#none", "none": "#none", "imprint": "#imprint", "cookie": "#cookie"}
METHODS = {"": None, "mesh": 0, "openvdb": 1}

# Inline cutter shapes -> script enum (1=box, 2=cylinder, 3=sphere).
CUTTER_SHAPES = {"box": 1, "cylinder": 2, "sphere": 3}
AXES = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
    "-x": (-1.0, 0.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "-z": (0.0, 0.0, -1.0),
}
MAX_CUTTER_INSTANCES = 200


def _op_enum(token: str) -> str | None:
    return OPERATIONS.get(token.strip().lower())


def _triple(v: Any, what: str) -> tuple[list[float] | None, str]:
    """[x,y,z] (or a single number -> uniform) as floats."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if math.isfinite(v):
            return [float(v)] * 3, ""
    if isinstance(v, (list, tuple)) and len(v) == 3:
        try:
            values = [float(x) for x in v]
            if all(not isinstance(x, bool) for x in v) and all(math.isfinite(x) for x in values):
                return values, ""
        except (TypeError, ValueError):
            pass
    return None, f"{what} must be a finite number or [x, y, z]"


def _fmt3(v: list[float]) -> str:
    return "[" + ", ".join(f"{x:.6g}" for x in v) + "]"


def _normalize_cutters(
    cutters: list[Any], repeat: dict[str, Any], default_enum: str
) -> tuple[list[dict[str, Any]] | None, str]:
    """Expand cutter defs (+ optional repeat) into flat, validated instances."""
    reps, axis, spacing = 1, AXES["x"], 0.0
    if repeat:
        reps = repeat.get("count", 0)
        if isinstance(reps, bool) or not isinstance(reps, int):
            return None, "repeat.count must be an integer"
        if reps < 1:
            return None, "repeat.count must be >= 1"
        ax = str(repeat.get("axis", "x")).strip().lower()
        if ax not in AXES:
            return None, f"repeat.axis must be one of {sorted(AXES)}"
        axis = AXES[ax]
        try:
            spacing = float(repeat.get("spacing", 0.0))
        except (TypeError, ValueError):
            return None, "repeat.spacing must be a number"
        if not math.isfinite(spacing) or (reps > 1 and spacing <= 0):
            return None, "repeat.spacing must be > 0 (flip direction with a signed axis, e.g. '-x')"
    if reps * len(cutters) > MAX_CUTTER_INSTANCES:
        return None, f"cutter expansion exceeds cap {MAX_CUTTER_INSTANCES}"
    instances: list[dict[str, Any]] = []
    for idx, c in enumerate(cutters):
        if not isinstance(c, dict):
            return None, f"cutters[{idx}] must be an object"
        nm = str(c.get("name", "")).strip()
        if not nm:
            return None, f"cutters[{idx}]: name is required (it becomes the operand name)"
        shape = CUTTER_SHAPES.get(str(c.get("shape", "box")).strip().lower())
        if shape is None:
            return None, f"cutters[{idx}]: shape must be box, cylinder, or sphere"
        segments = c.get("segments", 64 if shape == 2 else 32)
        if isinstance(segments, bool) or not isinstance(segments, int) or not 4 <= segments <= 256:
            return None, f"cutters[{idx}].segments must be an integer from 4 to 256"
        size, err = _triple(c.get("size"), f"cutters[{idx}].size")
        if err:
            return None, err
        if min(size) <= 0:
            return None, f"cutters[{idx}].size values must be > 0"
        pos, err = _triple(c.get("pos", [0.0, 0.0, 0.0]), f"cutters[{idx}].pos")
        if err:
            return None, err
        rot, err = _triple(c.get("rot", [0.0, 0.0, 0.0]), f"cutters[{idx}].rot")
        if err:
            return None, err
        op_token = str(c.get("operation", "")).strip()
        enum = _op_enum(op_token) if op_token else default_enum
        if enum is None:
            return None, f"cutters[{idx}]: unknown operation '{op_token}'"
        for i in range(reps):
            instances.append(
                {
                    "name": nm if reps == 1 else f"{nm}_{i + 1}",
                    "shape": shape,
                    "segments": segments,
                    "size": size,
                    "pos": [pos[k] + axis[k] * spacing * i for k in range(3)],
                    "rot": rot,
                    "enum": enum,
                }
            )
    if len(instances) > MAX_CUTTER_INSTANCES:
        return None, f"cutter expansion produced {len(instances)} instances (cap {MAX_CUTTER_INSTANCES})"
    return instances, ""


# Cutters are created inside the same undo block, appended alongside named
# operands, and any cutter whose append FAILS is deleted on the spot — no
# scene litter on any path.  d = #(name, shapeEnum, size, posCenter, rotEuler, opEnum)
_CUTTER_SCRIPT = """local madeCutters = #()
            for d in cutDefs do (
                local c
                if d[2] == 1 then c = Box width:d[3].x length:d[3].y height:d[3].z
                else if d[2] == 2 then c = Cylinder radius:((amin #(d[3].x, d[3].y)) / 2.0) height:d[3].z sides:d[7] heightsegs:1 capsegs:1
                else c = Sphere radius:((amin #(d[3].x, d[3].y, d[3].z)) / 2.0) segments:d[7]
                c.name = d[1]
                c.material = obj.material
                if d[5] != [0,0,0] do c.rotation = eulerAngles d[5].x d[5].y d[5].z
                c.pos += d[4] - c.center
                append madeCutters c
                append opNodes c
                append opEnums d[6]
                append opNames d[1]
            )"""


def _err(message: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "error", "error": message}
    if extra:
        out["details"] = extra
    return out


def _run(script: str) -> str:
    return str(client.send_command(script).get("result", ""))


def _base_guard(safe: str) -> str:
    """Shared script prologue: resolve base node into `obj` or emit __ERROR__."""
    return f'local obj = getNodeByName "{safe}"\nif obj == undefined then "__ERROR__|base object not found: {safe}" else '


def _find_boolmod() -> str:
    """Script fragment: topmost BooleanMod on `obj` into `bm` (undefined if none)."""
    return (
        "local bm = undefined\n"
        "for i = 1 to obj.modifiers.count while bm == undefined do "
        "(if classof obj.modifiers[i] == BooleanMod do bm = obj.modifiers[i])\n"
    )


@mcp.tool()
def boolean_operation(
    action: str = "apply",
    name: str = "",
    operands: Optional[StrList] = None,
    cutters: Optional[DictList] = None,
    repeat: Optional[DictValue] = None,
    operation: str = "",
    operations: Optional[StrList] = None,
    operation_option: str = "",
    method: str = "",
    voxel_size: float = 0.0,
    live: bool = False,
    use_operand_material: bool = True,
    new_modifier: bool = False,
    modifier_name: str = "",
    operand_index: int = 0,
    rename: str = "",
    disable: Optional[bool] = None,
) -> Any:
    """Boolean modeling on a base object via the Boolean modifier (BooleanMod).

    Actions:
    - apply: append `operands` (node names) and/or `cutters` (inline defs) with
      `operation` — union | subtract | intersect | merge | attach | insert | split.
      Reuses the topmost BooleanMod unless new_modifier=true. Non-live operands are
      CONSUMED (scene node deleted, geometry captured; the operand keeps its name
      inside the modifier). live=true keeps operand nodes in the scene (hidden).
      `operations` optionally overrides per operand (parallel to `operands`).
      operation_option: imprint (edges only) | cookie (cut without adding faces).
      method: mesh (default) | openvdb (+ voxel_size for VDB resolution).
      `cutters`: scratch primitives built and applied in the same call — never
      litter the scene. Each: {name (required), shape: box|cylinder|sphere (default
      box), size: [x,y,z] or number, pos: [x,y,z] bbox CENTER, rot: [deg,deg,deg],
      operation?, segments? (4-256; cylinder default 64, sphere 32)}.
      Cylinder size is [diameterX,diameterY,height], using the smaller diameter.
      Cylinder is Z-axis (rot to orient); size a through-cut past both
      faces. `repeat` {count, axis: x|y|z|-x|-y|-z, spacing} arrays every cutter
      along the axis, naming instances <name>_1..N.
    - list: enumerate operand stack — flat index, name, operation, option, disabled.
    - set_operand: retune operand at operand_index — operation / operation_option /
      rename (anchor-friendly) / disable.
    - remove_operand: delete operand at operand_index from the stack.
    - extract_operand: copy operand at operand_index back out as a scene object.

    Use when: cutting holes, insets, panel lines, or fusing parts on geometry.
    Not when: collapsing the result (collapse_modifier_stack) or spline booleans.
    """
    action = action.strip().lower()
    if not name:
        return _err("name (base object) is required")
    safe = safe_string(name)

    if action == "apply":
        ops = [str(o) for o in (operands or []) if str(o).strip()]
        cutter_list = list(cutters or [])
        if repeat and not cutter_list:
            return _err("repeat requires cutters")
        if not ops and not cutter_list:
            return _err("operands (node names) and/or cutters (inline defs) required")
        if any(o.lower() == name.lower() for o in ops):
            return _err("an object cannot be a boolean operand of itself")
        default_token = operation.strip() or "subtract"
        if _op_enum(default_token) is None:
            return _err(f"unknown operation '{operation}'", valid=sorted(set(OPERATIONS)))
        per_op: list[str] = []
        overrides = list(operations or [])
        for i, _ in enumerate(ops):
            token = overrides[i] if i < len(overrides) and str(overrides[i]).strip() else default_token
            enum = _op_enum(str(token))
            if enum is None:
                return _err(f"unknown operation '{token}'", valid=sorted(set(OPERATIONS)))
            per_op.append(enum)
        opt = OPTIONS.get(operation_option.strip().lower())
        if opt is None:
            return _err("operation_option must be imprint, cookie, or empty")
        meth = METHODS.get(method.strip().lower(), "bad")
        if meth == "bad":
            return _err("method must be 'mesh' or 'openvdb'")
        cut_instances: list[dict[str, Any]] = []
        if cutter_list:
            cut_instances, cut_err = _normalize_cutters(cutter_list, repeat or {}, _op_enum(default_token))
            if cut_err:
                return _err(cut_err)
            if any(ci["name"].lower() == name.lower() for ci in cut_instances):
                return _err("a cutter cannot be named after the base object")
            combined = [o.lower() for o in ops] + [ci["name"].lower() for ci in cut_instances]
            if len(set(combined)) != len(combined):
                return _err("operand/cutter names must be unique within one call")
        cutter_block = ""
        fail_cleanup = ""
        if cut_instances:
            cut_defs = "#(" + ", ".join(
                '#("%s", %d, %s, %s, %s, %s, %d)'
                % (
                    safe_string(ci["name"]),
                    ci["shape"],
                    _fmt3(ci["size"]),
                    _fmt3(ci["pos"]),
                    _fmt3(ci["rot"]),
                    ci["enum"],
                    ci["segments"],
                )
                for ci in cut_instances
            ) + ")"
            cutter_block = f"local cutDefs = {cut_defs}\n            {_CUTTER_SCRIPT}\n            "
            fail_cleanup = (
                "\n                    if (findItem madeCutters opNodes[i]) > 0 do (try (delete opNodes[i]) catch ())"
            )

        names_arr = "#(" + ", ".join(f'"{safe_string(o)}"' for o in ops) + ")"
        enums_arr = "#(" + ", ".join(per_op) + ")"
        props = [f"bm.useLiveReference = {str(live).lower()}"]
        props.append(f"bm.useOperandMaterial = {str(use_operand_material).lower()}")
        if meth is not None:
            props.append(f"bm.method = {meth}")
        if meth == 1 and voxel_size > 0:
            props.append(f"bm.voxelSize = {voxel_size}")
        if modifier_name:
            props.append(f'bm.name = "{safe_string(modifier_name)}"')
        find_or_make = (
            "local bm = undefined\n"
            + (
                ""
                if new_modifier
                else "for i = 1 to obj.modifiers.count while bm == undefined do "
                "(if classof obj.modifiers[i] == BooleanMod do bm = obj.modifiers[i])\n"
            )
            + "local madeNew = false\n"
            "if bm == undefined do (bm = BooleanMod(); addModifier obj bm; madeNew = true)\n"
        )
        script = f"""(
{_base_guard(safe)}(
    local opNames = {names_arr}
    local opEnums = {enums_arr}
    local missing = ""
    local badKind = ""
    local opNodes = #()
    for nm in opNames do (
        local o = getNodeByName nm
        if o == undefined then missing += nm + ", "
        else if superclassof o != GeometryClass then badKind += nm + " (" + ((classof o) as string) + "), "
        else append opNodes o
    )
    if missing != "" then ("__ERROR__|operand(s) not found: " + missing)
    else if badKind != "" then ("__ERROR__|operand(s) not geometry: " + badKind)
    else (
        undo "Boolean" on (
            {find_or_make}
            {chr(10).join("            " + p for p in props)}
            -- Initialize the modifier context before capturing non-live operands.
            -- Otherwise a fresh BooleanMod can lose their relative transforms.
            local baseFaceCount = GetTriMeshFaceCount obj
            {cutter_block}local failed = ""
            for i = 1 to opNodes.count do (
                local ok = false
                try (ok = bm.appendOperand #single operandNode:opNodes[i] operationType:opEnums[i] operationOption:{opt}) catch ()
                if not ok do (
                    failed += opNames[i] + ", "{fail_cleanup}
                )
            )
            local tris = 0
            try (tris = (GetTriMeshFaceCount obj)[1]) catch ()
            local total = 0
            try (total = bm.GetNumOperands()) catch ()
            "OK|" + bm.name + "|" + (madeNew as string) + "|" + (total as string) + "|" + (tris as string) + "|" + failed
        )
    )
)
)"""
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        parts = raw.split("|")
        if len(parts) < 6 or parts[0] != "OK":
            return _err(f"unexpected bridge reply: {raw[:200]}")
        failed = [f.strip() for f in parts[5].split(",") if f.strip()]
        all_names = ops + [ci["name"] for ci in cut_instances]
        result: dict[str, Any] = {
            "modifier": parts[1],
            "new_modifier": parts[2] == "true",
            "operands_total": int(parts[3]) if parts[3].isdigit() else 0,
            "appended": [o for o in all_names if o not in failed],
            "tris": int(parts[4]) if parts[4].isdigit() else 0,
            "live": live,
        }
        if cut_instances:
            result["cutters_created"] = [ci["name"] for ci in cut_instances]
        if failed:
            result["failed"] = failed
        if not live:
            result["consumed"] = result["appended"]
            result["note"] = (
                "non-live operands were captured into the modifier and their scene "
                "nodes deleted; they keep their names in the operand list (see action=list)"
            )
        return result

    if action == "list":
        script = f"""(
{_base_guard(safe)}(
    {_find_boolmod()}
    if bm == undefined then "__ERROR__|no Boolean modifier on {safe}" else (
        local out = stringstream ""
        local cnt = 0
        try (cnt = bm.GetNumOperands()) catch ()
        for i = 1 to cnt do (
            local nm = ""
            local ty = #single
            local op = #union
            local oo = #none
            local dis = false
            try (bm.GetFlatOperandName i &nm) catch ()
            try (bm.GetRootOperandType i &ty) catch ()
            try (bm.GetOperationType i &op) catch ()
            try (bm.GetOperationOption i &oo) catch ()
            try (bm.GetDisable i &dis) catch ()
            format "OPER|%|%|%|%|%|%\\n" i nm (ty as string) (op as string) (oo as string) (dis as string) to:out
        )
        local tris = 0
        try (tris = (GetTriMeshFaceCount obj)[1]) catch ()
        format "INFO|%|%|%|%\\n" bm.name (bm.method as string) (bm.useLiveReference as string) (tris as string) to:out
        out as string
    )
)
)"""
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        operand_rows: list[dict[str, Any]] = []
        info: dict[str, Any] = {}
        for line in raw.splitlines():
            kind, _, rest = line.partition("|")
            f = rest.split("|")
            if kind == "OPER" and len(f) >= 6:
                operand_rows.append(
                    {
                        "index": int(f[0]),
                        "name": f[1],
                        "kind": f[2],
                        "operation": f[3],
                        "option": f[4],
                        "disabled": f[5] == "true",
                    }
                )
            elif kind == "INFO" and len(f) >= 4:
                info = {
                    "modifier": f[0],
                    "method": "openvdb" if f[1] == "1" else "mesh",
                    "live": f[2] == "true",
                    "tris": int(f[3]) if f[3].isdigit() else 0,
                }
        return {**info, "operands": operand_rows}

    if action in {"set_operand", "remove_operand", "extract_operand"}:
        if operand_index < 1:
            return _err("operand_index (1-based, as returned by action=list) is required")
        if action == "set_operand":
            edits: list[str] = []
            if operation.strip() and _op_enum(operation) is None:
                return _err(f"unknown operation '{operation}'", valid=sorted(set(OPERATIONS)))
            if operation.strip():
                edits.append(f"try (bm.SetOperationType {operand_index} {_op_enum(operation)}; done += \"op \") catch ()")
            if operation_option.strip():
                opt = OPTIONS.get(operation_option.strip().lower())
                if opt is None:
                    return _err("operation_option must be imprint, cookie, or empty")
                edits.append(f"try (bm.SetOperationOption {operand_index} {opt}; done += \"option \") catch ()")
            if rename:
                edits.append(
                    f'try (bm.SetRootOperandName {operand_index} "{safe_string(rename)}"; done += "rename ") catch ()'
                )
            if disable is not None:
                edits.append(f"try (bm.SetDisable {operand_index} {str(disable).lower()}; done += \"disable \") catch ()")
            if not edits:
                return _err("set_operand needs operation, operation_option, rename, or disable")
            body = (
                'local ty = #single\n'
                f"        try (bm.GetRootOperandType {operand_index} &ty) catch ()\n"
                '        if ty == #modified then "__ERROR__|index ' + str(operand_index) + ' is the base object, not an operand" else (\n'
                '            local done = ""\n'
                "            undo \"Boolean operand\" on (\n"
                + "\n".join("                " + e for e in edits)
                + "\n            )\n"
                '            "OK|" + done\n'
                "        )"
            )
        elif action == "remove_operand":
            body = (
                'local ty = #single\n'
                f"        try (bm.GetRootOperandType {operand_index} &ty) catch ()\n"
                '        if ty == #modified then "__ERROR__|index ' + str(operand_index) + ' is the base object, not an operand" else (\n'
                "            local ok = false\n"
                f'            undo "Boolean remove" on (try (ok = bm.RemoveOperand {operand_index}) catch ())\n'
                '            if ok then "OK|removed" else "__ERROR__|RemoveOperand failed at index ' + str(operand_index) + '"\n'
                "        )"
            )
        else:  # extract_operand — needs the node selected with the Modify panel active
            body = (
                "local before = objects.count\n"
                "        local prevSel = selection as array\n"
                "        local prevMode = getCommandPanelTaskMode()\n"
                "        select obj\n"
                "        setCommandPanelTaskMode #modify\n"
                "        local ok = false\n"
                f'        undo "Boolean extract" on (try (ok = bm.ExtractAsObject {operand_index}) catch ())\n'
                "        local newName = \"\"\n"
                "        if objects.count > before do newName = objects[objects.count].name\n"
                "        setCommandPanelTaskMode prevMode\n"
                "        clearSelection()\n"
                "        if prevSel.count > 0 do try (select prevSel) catch ()\n"
                '        if not ok then "__ERROR__|ExtractAsObject failed at index ' + str(operand_index) + '" else (\n'
                '            "OK|" + newName\n'
                "        )"
            )
        script = f"""(
{_base_guard(safe)}(
    {_find_boolmod()}
    if bm == undefined then "__ERROR__|no Boolean modifier on {safe}" else (
        {body}
    )
)
)"""
        raw = _run(script)
        if raw.startswith("__ERROR__|"):
            return _err(raw.split("|", 1)[1])
        if not raw.startswith("OK|"):
            return _err(f"unexpected bridge reply: {raw[:200]}")
        payload = raw.split("|", 1)[1]
        if action == "set_operand":
            applied = payload.split()
            out: dict[str, Any] = {"operand_index": operand_index, "applied": applied}
            requested = sum(
                1 for flag in (operation.strip(), operation_option.strip(), rename, disable is not None) if flag
            )
            if len(applied) < requested:
                out["warning"] = "some edits did not apply (check operand_index with action=list)"
            return out
        if action == "remove_operand":
            return {"operand_index": operand_index, "removed": True}
        return {"operand_index": operand_index, "extracted": payload or "(unknown — check query_scene delta)"}

    return _err(f"unknown action: {action} (apply | list | set_operand | remove_operand | extract_operand)")
