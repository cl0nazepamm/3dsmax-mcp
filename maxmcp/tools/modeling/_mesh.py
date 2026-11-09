"""Shared Editable Poly inspection and geometric component selection.

All IDs refer to the BASE cage. World coordinates include object offsets,
parent transforms, and nonuniform scale. No evaluated triangle IDs are exposed.
"""
from __future__ import annotations

import json
import math
from ...core.maxscript import node_by_name, safe_string


def vector(value, label, size=3):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{label} must contain {size} finite numbers")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(f"{label} must contain {size} finite numbers")
    return [float(v) for v in value]


def point(value):
    return "[" + ",".join(format(float(v), ".9g") for v in value) + "]"


def integer(value, label, low=1, high=1000000):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")
    return value


def target_script(name: str, handle: int):
    code = 'local obj = undefined\n'
    if handle:
        integer(handle, "handle", high=2**63-1)
        code += f'obj = getAnimByHandle {handle}\n'
        code += 'if not isValidNode obj do throw "Node handle is no longer valid"\n'
        if name:
            code += f'if obj.name != "{safe_string(name)}" do throw "Node handle/name mismatch"\n'
    elif name:
        code += f'obj = {node_by_name(name, "Node")}\n'
    else:
        code += 'if selection.count != 1 do throw "Select exactly one mesh or pass name/handle"\nobj = selection[1]\n'
    return code


def resolve_script(name: str, handle: int, checks: str = ""):
    """MAXScript returning the target's handle after target_script and checks."""
    return f'''(
        {target_script(name, handle)}
        {checks}
        formattedPrint ((getHandleByAnim obj) as integer64) format:"d"
    )'''


def run_mesh_script(client, script: str, *, resolve: str = "", expected_mesh: str = "",
                    undo: str = "", finish: str = "") -> dict:
    """Run mesh MAXScript with its base-cage token in one Max main-thread step.

    script returns "<node handle>\\n<output>". native:mesh_script hashes that
    node's Editable Poly base cage after the script (mesh_token). resolve
    returns the target handle before the script; with expected_mesh the bridge
    rejects a changed cage before the script runs. undo labels the one hold
    that wraps the script and rolls it back on failure; finish runs after the
    hold is accepted, with obj bound to the script's node. Script errors raise;
    nothing is retried.
    """
    payload = {"script": script}
    for key, value in (("resolve", resolve), ("expected_mesh", expected_mesh),
                       ("undo", undo), ("finish", finish)):
        if value:
            payload[key] = value
    raw = client.send_command(json.dumps(payload), cmd_type="native:mesh_script").get("result", "")
    try:
        result = json.loads(raw)
        if (not isinstance(result.get("output"), str) or not isinstance(result.get("mesh_token"), str)
                or isinstance(result.get("handle"), bool) or not isinstance(result.get("handle"), int)):
            raise ValueError("incomplete mesh readback")
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid mesh readback; inspect before retrying: {str(raw)[:200]}") from exc
    return result


def selection_script(selection: dict | None, level: str, *, default_all=False):
    """Produce a deterministic BitArray; multiple filters intersect."""
    if selection is not None and not isinstance(selection, dict):
        raise ValueError("selection must be an object")
    spec = dict(selection or {})
    allowed = {"indices", "all", "current", "bbox", "near", "radius", "normal", "angle", "boundary", "sharp"}
    extra = set(spec) - allowed
    if extra:
        raise ValueError(f"Unknown selection keys: {sorted(extra)}")
    if level not in {"vertex", "edge", "face"}:
        raise ValueError("level must be vertex, edge, or face")
    for key in ("all", "current", "boundary"):
        if key in spec and not isinstance(spec[key], bool):
            raise ValueError(f"selection.{key} must be boolean")
    if sum(bool(spec.get(k)) for k in ("indices", "all", "current")) > 1:
        raise ValueError("Choose one of selection.indices, all, or current")
    if "normal" in spec and level != "face":
        raise ValueError("normal selection requires face level")
    if "boundary" in spec and level != "edge":
        raise ValueError("boundary selection requires edge level")
    if "sharp" in spec and level != "edge":
        raise ValueError("sharp selection requires edge level")
    if "radius" in spec and "near" not in spec:
        raise ValueError("selection.radius requires near")
    if "angle" in spec and "normal" not in spec:
        raise ValueError("selection.angle requires normal")
    count = {"vertex":"polyop.getNumVerts mesh", "edge":"polyop.getNumEdges mesh", "face":"polyop.getNumFaces mesh"}[level]
    get_sel = {"vertex":"getVertSelection", "edge":"getEdgeSelection", "face":"getFaceSelection"}[level]
    lines = [f'local componentCount = {count}', 'local chosen = #{}']
    if "indices" in spec:
        indices = spec["indices"]
        if not isinstance(indices, list) or not indices or len(indices) > 10000:
            raise ValueError("selection.indices must contain 1-10000 IDs")
        ids = [integer(i, "component ID") for i in indices]
        lines += [f'for i in #({",".join(map(str, ids))}) do (if i > componentCount do throw "Component ID out of range"; chosen[i] = true)']
    elif spec.get("current") or (not default_all and not any(k in spec for k in ("bbox","near","normal","boundary","sharp")) and not spec.get("all")):
        lines += [f'chosen = polyop.{get_sel} mesh']
    else:
        lines += ['if componentCount > 0 do chosen = #{1..componentCount}']
    dead = {"vertex":"getDeadVerts", "edge":"getDeadEdges", "face":"getDeadFaces"}[level]
    lines += [f'chosen -= polyop.{dead} mesh']
    if "bbox" in spec:
        bounds = vector(spec["bbox"], "selection.bbox", 6)
        if any(bounds[i] > bounds[i+3] for i in range(3)):
            raise ValueError("bbox minimum must not exceed maximum")
        lines += [f'local lo = {point(bounds[:3])}; local hi = {point(bounds[3:])}',
                  f'for i in chosen do (local p = mcCenter mesh tm #{level} i; if p.x < lo.x or p.y < lo.y or p.z < lo.z or p.x > hi.x or p.y > hi.y or p.z > hi.z do chosen[i] = false)']
    if "near" in spec:
        near = vector(spec["near"], "selection.near")
        radius = spec.get("radius")
        if isinstance(radius, bool) or not isinstance(radius, (int,float)) or not math.isfinite(radius) or radius <= 0:
            raise ValueError("selection.near requires a positive finite radius")
        lines += [f'for i in chosen where distance (mcCenter mesh tm #{level} i) {point(near)} > {radius} do chosen[i] = false']
    if "normal" in spec:
        normal = vector(spec["normal"], "selection.normal")
        if math.dist(normal,[0,0,0]) < 1e-9:
            raise ValueError("selection.normal must be nonzero")
        angle = spec.get("angle", 10.0)
        if isinstance(angle, bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle) or not 0 <= angle <= 180:
            raise ValueError("selection.angle must be from 0 to 180 degrees")
        lines += [f'local wantedNormal = normalize {point(normal)}',
                  f'for i in chosen where dot (mcNormal mesh tm i) wantedNormal < cos {angle} do chosen[i] = false']
    if "boundary" in spec:
        lines += ['local boundaryEdges = polyop.getOpenEdges mesh']
        lines += ['chosen = chosen * boundaryEdges' if spec["boundary"] else 'chosen -= boundaryEdges']
    if "sharp" in spec:
        angle = spec["sharp"]
        if isinstance(angle, bool) or not isinstance(angle, (int,float)) or not math.isfinite(angle) or not 0 <= angle <= 180:
            raise ValueError("selection.sharp must be a minimum dihedral angle from 0 to 180 degrees")
        lines += [f'for i in chosen do (local fs = (polyop.getFacesUsingEdge mesh #{{i}}) as array; if fs.count != 2 then chosen[i] = false else if dot (mcNormal mesh tm fs[1]) (mcNormal mesh tm fs[2]) > cos {angle} do chosen[i] = false)']
    return "\n".join(lines)


MESH_FUNCTIONS = r'''
fn mcPoint p = (
    "[" + (formattedPrint p.x format:".9g") + "," + (formattedPrint p.y format:".9g") + "," + (formattedPrint p.z format:".9g") + "]"
)
fn mcIds bits = (
    local out = ""
    for i in bits do (if out != "" do out += ","; out += i as string)
    out
)
fn mcVerts mesh level i = (
    case level of (
        #vertex: #(i)
        #edge: (polyop.getEdgeVerts mesh i)
        #face: (polyop.getFaceVerts mesh i)
    )
)
fn mcCenter mesh tm level i = (
    local vs = mcVerts mesh level i
    local p = [0,0,0]
    for v in vs do p += (polyop.getVert mesh v) * tm
    p / vs.count
)
fn mcNormal mesh tm i = (
    local vs = polyop.getFaceVerts mesh i
    local n = [0,0,0]
    for k = 1 to vs.count do (
        local a = (polyop.getVert mesh vs[k]) * tm
        local b = (polyop.getVert mesh vs[if k == vs.count then 1 else k+1]) * tm
        n.x += (a.y-b.y)*(a.z+b.z)
        n.y += (a.z-b.z)*(a.x+b.x)
        n.z += (a.x-b.x)*(a.y+b.y)
    )
    if length n < 0.0000001 then [0,0,0] else normalize n
)
fn mcCounts mesh = (
    (polyop.getNumVerts mesh as string) + "," + (polyop.getNumEdges mesh as string) + "," + (polyop.getNumFaces mesh as string)
)
fn mcHandle obj = (
    formattedPrint ((getHandleByAnim obj) as integer64) format:"d"
)
fn mcSelected mesh level = (
    case level of (
        #vertex: (polyop.getVertSelection mesh)
        #edge: (polyop.getEdgeSelection mesh)
        #face: (polyop.getFaceSelection mesh)
    )
)
fn mcSelect mesh level bits = (
    case level of (
        #vertex: (polyop.setVertSelection mesh bits)
        #edge: (polyop.setEdgeSelection mesh bits)
        #face: (polyop.setFaceSelection mesh bits)
    )
)
'''
