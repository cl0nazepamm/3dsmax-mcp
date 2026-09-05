"""Persistent named-curve construction on editable Max objects."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ..coerce import DictValue
from ..helpers.curves import build_model, keys
from ..helpers.curve_runtime import CURVE_FUNCTIONS, FINGERPRINT, decode, run
from ..helpers.loft import validate_parameters
from ..helpers.mesh import MESH_FUNCTIONS, point, target_script, vector
from ..helpers.maxscript import safe_string
from ..server import mcp

APPDATA_ID = 1129731140  # CVMD; independent of the legacy loft definition.
SCHEMA = "3dsmax-mcp/curve-model"
VERSION = 1
MARKER = "__MCP_CURVE_FINGERPRINT__"
MAX_BYTES = 4_000_000
HISTORY = 16


def literal(text):
    from .loft import _literal
    return _literal(text)


def encode(data):
    raw=json.dumps(data,ensure_ascii=True,separators=(",",":"),allow_nan=False)
    if len(raw)+raw.count(MARKER)*(95-len(MARKER))>MAX_BYTES:
        raise ValueError("Curve construction exceeds 4 MB")
    return raw


def functions():
    from .loft import LOFT_FUNCTIONS
    return MESH_FUNCTIONS+CURVE_FUNCTIONS+LOFT_FUNCTIONS+r'''
fn cmToken obj = (if isKindOf obj.baseobject SplineShape then cvToken obj else lfToken obj)
'''


def _parse(raw):
    if len(raw)>MAX_BYTES: raise ValueError("Stored construction exceeds 4 MB")
    data=json.loads(raw)
    keys(data,"schema version definition origin kind alignment history topology","stored construction")
    if data.get("schema")!=SCHEMA or data.get("version")!=VERSION:
        raise ValueError("Unsupported curve construction version")
    vector(data.get("origin"),"origin")
    if data.get("kind") not in {"curve","sweep","loft"}: raise ValueError("Invalid construction kind")
    if not isinstance(data.get("topology"),str) or len(data["topology"])!=64: raise ValueError("Invalid topology signature")
    if not isinstance(data.get("history"),list) or not 1<=len(data["history"])<=HISTORY:
        raise ValueError("Invalid construction history")
    names=None
    for row in data["history"]:
        keys(row,"parameters fingerprint","construction state")
        if not isinstance(row.get("fingerprint"),str) or not FINGERPRINT.fullmatch(row["fingerprint"]):
            raise ValueError("Invalid construction fingerprint")
        values=validate_parameters(row.get("parameters"))
        if names is not None and set(values)!=names: raise ValueError("Parameter names changed across history")
        names=set(values)
    if not isinstance(data.get("definition"),dict): raise ValueError("Missing construction recipe")
    return data


def _read(name,handle):
    raw=run(f'''(
        {functions()}
        try (
            {target_script(name,handle)}
            local data = getAppData obj {APPDATA_ID}
            if data == undefined do throw "Node has no curve construction recipe"
            local instances = #(); InstanceMgr.GetInstances obj &instances
            local out = stringstream ""
            format "NAME|%\\n" (cvB64 obj.name) to:out
            format "META|%|%|%|%\\n" (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") (cmToken obj) obj.modifiers.count instances.count to:out
            format "DATA|%\\n" (cvB64 data) to:out
            out as string
        ) catch ("__ERROR__|" + (getCurrentException() as string))
    )''')
    try:
        rows=dict(line.split("|",1) for line in raw.splitlines() if "|" in line)
        h,token,mods,instances=rows["META"].split("|")
        data_raw=decode(rows["DATA"])
        if not FINGERPRINT.fullmatch(token): raise ValueError("Invalid fingerprint")
        return {"name":decode(rows["NAME"]),"handle":int(h),"fingerprint":token,
                "modifiers_above":int(mods),"instance_count":int(instances)},_parse(data_raw),data_raw
    except (KeyError,ValueError,UnicodeError) as exc:
        raise RuntimeError("Invalid construction readback; no write attempted") from exc


def _active(data,token):
    matches=[row["parameters"] for row in data["history"] if row["fingerprint"]==token]
    return matches[-1] if matches and all(p==matches[0] for p in matches) else None


def _model_token(identity,raw):
    # Includes the persisted recipe, not merely its geometric result.
    return hashlib.sha256((str(identity["handle"])+"|"+identity["fingerprint"]+"|"+raw).encode()).hexdigest()


def _public(identity,data,raw,include_definition):
    parameters=_active(data,identity["fingerprint"])
    result={**identity,"kind":data["kind"],"parameters":parameters,"model_token":_model_token(identity,raw),
            "geometry_matches_recipe":parameters is not None,"retained_parameter_states":len(data["history"]),
            "curve_names":list(data["definition"]["curves"]),"origin":data["origin"],
            "construction_space":"Initial world coordinates; updates follow the node transform and preserve its stack."}
    if include_definition: result["definition"]=data["definition"]
    if parameters is None: result["reason"]="Manual edit, ambiguous state or geometry outside retained undo history; parameter updates blocked"
    return result


def _topology(built):
    value=([built["curve"].closed,[k["seg"] for k in built["curve"].knots()]] if built["kind"]=="curve"
           else [len(built["vertices"]),built["faces"]])
    return hashlib.sha256(json.dumps(value,separators=(",",":")).encode()).hexdigest()


def _preview(built):
    result={"kind":built["kind"],"curves":{k:{"knots":len(c.knots()),**built["qa"][k]} for k,c in built["curves"].items()},
            "alignment":built["alignment"],"notes":built.get("notes",[])}
    if built["kind"]=="curve": result["counts"]={"knots":len(built["curve"].knots()),"splines":1}
    else:
        result["counts"]={"vertices":len(built["vertices"]),"faces":len(built["faces"])}
        result["bounds"]=[[min(p[a] for p in built["vertices"]) for a in range(3)],[max(p[a] for p in built["vertices"]) for a in range(3)]]
    return result


def _install(data):
    return f'''local recipe = substituteString {literal(encode(data))} "{MARKER}" (cmToken obj)
        setAppData obj {APPDATA_ID} recipe
        if (getAppData obj {APPDATA_ID}) != recipe do throw "Construction recipe did not persist"'''


def _curve_write(curve,origin,create):
    # Spline MAXScript methods dispatch on the node, unlike polyop's base API.
    # Local coordsys addresses raw cage coordinates even with object offsets.
    lines=["local shape = obj"]
    if create: lines.append("addNewSpline shape")
    for i,k in enumerate(curve.knots(),1):
        p,iv,ov=[point([v[a]-origin[a] for a in range(3)]) for v in (k["pos"],k["in_vec"],k["out_vec"])]
        if create: lines.append(f"addKnot shape 1 #bezierCorner #{k['seg']} {p} {iv} {ov}")
        else: lines.extend([f"setKnotPoint shape 1 {i} {p}",f"setInVec shape 1 {i} {iv}",f"setOutVec shape 1 {i} {ov}"])
    if create and curve.closed: lines.append("close shape 1")
    lines.append("updateShape shape")
    return "in coordsys local ("+";\n".join(lines)+")"


@mcp.tool()
def curve_model(
    action: str = "preview",
    name: str = "",
    handle: int = 0,
    definition: DictValue | None = None,
    parameters: DictValue | None = None,
    expected_model: str = "",
    include_definition: bool = False,
) -> dict[str, Any]:
    """Construct editable curves, swept profiles or matched quad lofts with saved controls.

    preview evaluates locally without Max. create saves a new named object and its
    recipe in one undo step. read returns controls/token; include_definition adds
    source. update requires expected_model from read and changes existing numeric
    parameters, preserving node placement/materials/modifiers and component count.
    Manual geometry edits, instanced bases and stale recipes block updates.

    definition={curves:{name:curve,...},output:{kind:curve|sweep|loft,...},tolerance:.01}.
    Curve kinds: polyline, spline (through points), bezier (4 control points), path
    (start + line/arc/tangent_arc/bezier segments), circle, arc, rounded_rectangle.
    plane: xy|xz|yz or {origin,x_axis,normal}. Points have 2/3 local coordinates.
    Dimensions accept arithmetic strings referring to parameters; angles are degrees.
    polyline supports closed, fillet radius, and outward miter offset for closed XY.
    rounded_rectangle: width,depth,radius. circle/arc: radius,center,start_angle,sweep.
    path segment: {kind,to,label?}; arc adds radius/clockwise; tangent_arc adds
    tangent (or inherits previous); bezier adds start_tangent/end_tangent and
    start_length/end_length (directions of travel, positive handle lengths).

    output curve: {kind:"curve",curve:"outline"}.
    output sweep: {kind:"sweep",path:"rail",profile:"section",up:[0,0,1],
      path_samples:48,profile_samples:32,twist:0,scale:[1,1],caps:true}.
    Profile is CCW in world XY at z=0. up sets its initial Y direction; must not
    parallel the path. Frames follow bends without arbitrary roll. Closed paths
    need caps:false, equal scales and whole-turn twist.
    output loft: {kind:"loft",sections:["a","b"],profile_samples:32,
      align:"start"|"auto",caps:true}. Same winding required; auto chooses cyclic
    seams at creation and locks correspondence across parameter updates.
    All outputs support reverse on meshes. No extra scene helper nodes. Recipes
    retain source curves; mesh outputs are editable quad cages, not live Sweep
    modifiers. Curve QA is sampled; intersections/thickness of surfaces are not
    certified. Preview, create, inspect visually, then refine controls.
    """
    if action not in {"preview","create","read","update"}: raise ValueError("action must be preview, create, read or update")
    if type(include_definition) is not bool: raise ValueError("include_definition must be boolean")
    if not isinstance(name,str) or any(ord(c)<32 for c in name): raise ValueError("name must be a string without control characters")
    values=validate_parameters(parameters)
    if action in {"preview","create"}:
        if handle or expected_model: raise ValueError("preview/create do not target existing nodes")
        # Bound input before compiling nested expressions and sampling curves.
        encode({"definition":definition})
        built=build_model(definition,values)
        preview=_preview(built)
        if action=="preview": return {"action":action,**preview}
        if not name.strip(): raise ValueError("create requires name")
        points=([k["pos"] for k in built["curve"].knots()] if built["kind"]=="curve" else built["vertices"])
        origin=[(min(p[a] for p in points)+max(p[a] for p in points))/2 for a in range(3)]
        data={"schema":SCHEMA,"version":VERSION,"definition":copy.deepcopy(definition),"origin":origin,
              "kind":built["kind"],"alignment":built["alignment"],"topology":_topology(built),
              "history":[{"parameters":values,"fingerprint":MARKER}]}
        if built["kind"]=="curve":
            raw=run(f'''(
                {functions()}
                local holding = false
                try (
                    if (getNodeByName "{safe_string(name)}" exact:true) != undefined do throw "A node with this name already exists"
                    if theHold.Holding() do throw "An undo transaction is active"
                    theHold.Begin(); holding = true
                    local obj = SplineShape name:"{safe_string(name)}" pos:{point(origin)}
                    {_curve_write(built['curve'],origin,True)}
                    {_install(data)}
                    local answer = formattedPrint ((getHandleByAnim obj) as integer64) format:"d"
                    theHold.Accept "MCP curve construction"; holding = false
                    try (completeredraw()) catch ()
                    answer
                ) catch (local detail = getCurrentException() as string; if holding do theHold.Cancel(); "__ERROR__|"+detail)
            )''')
            created=int(raw)
        else:
            from .mesh_ops import _create_mesh
            created=_create_mesh(name,built["vertices"],built["faces"],before_accept=functions()+_install(data))["handle"]
        identity,stored,raw=_read(name,created)
        if _active(stored,identity["fingerprint"])!=values:
            raise RuntimeError("Construction created, but parameter readback changed; inspect before retrying")
        return {"action":action,**_public(identity,stored,raw,include_definition),"preview":preview}
    if definition is not None: raise ValueError("definition is creation-only; update changes saved parameters")
    if action=="read" and (parameters is not None or expected_model): raise ValueError("read does not accept changes or expected_model")
    if action=="update" and (not values or not isinstance(expected_model,str) or len(expected_model)!=64):
        raise ValueError("update needs parameters and the model_token from read")
    identity,data,old_raw=_read(name,handle)
    if action=="read": return {"action":action,**_public(identity,data,old_raw,include_definition)}
    if _model_token(identity,old_raw)!=expected_model: raise RuntimeError("STALE_MODEL: read the construction again")
    current=_active(data,identity["fingerprint"])
    if current is None: raise RuntimeError("Geometry changed manually or is outside retained undo history")
    if identity["instance_count"]>1: raise RuntimeError("Construction base is instanced; make it unique before changing parameters")
    if set(values)-set(current): raise ValueError(f"Unknown construction parameters: {sorted(set(values)-set(current))}")
    updated={**current,**values}
    if updated==current: return {"action":action,**_public(identity,data,old_raw,include_definition),"unchanged":True}
    built=build_model(data["definition"],updated,alignment=data["alignment"])
    if _topology(built)!=data["topology"]: raise ValueError("Parameter change alters topology; create a separate revised construction")
    if built["kind"]=="curve": write=_curve_write(built["curve"],data["origin"],False)
    else:
        points="#("+",".join(point([p[a]-data["origin"][a] for a in range(3)]) for p in built["vertices"])+")"
        write=f"local points = {points}; for i = 1 to points.count do polyop.setVert obj.baseobject i points[i]; update obj"
    aliases="\n".join(f'if (cmToken obj) == "{r["fingerprint"]}" do throw "Different parameters produce an existing geometry state; undo would be ambiguous"'
                        for r in data["history"] if r["parameters"]!=updated)
    data["history"]=[*data["history"],{"parameters":updated,"fingerprint":MARKER}][-HISTORY:]
    raw=run(f'''(
        {functions()}
        local holding = false; local targetNode = undefined
        try (
            {target_script(identity['name'],identity['handle'])}
            targetNode = obj
            if (getAppData obj {APPDATA_ID}) != {literal(old_raw)} or (cmToken obj) != "{identity['fingerprint']}" do throw "STALE_MODEL: construction changed during evaluation"
            local instances = #(); InstanceMgr.GetInstances obj &instances
            if instances.count > 1 do throw "Construction became instanced"
            if theHold.Holding() do throw "An undo transaction is active"
            theHold.Begin(); holding = true
            {write}
            {aliases or 'true'}
            {_install(data)}
            local answer = cmToken obj
            theHold.Accept "MCP construction parameters"; holding = false
            try (completeredraw()) catch ()
            answer
        ) catch (
            local detail = getCurrentException() as string
            if holding do (
                theHold.Cancel()
                try (if isValidNode targetNode do setAppData targetNode {APPDATA_ID} {literal(old_raw)}) catch (detail += "; recipe rollback failed")
            )
            "__ERROR__|"+detail
        )
    )''')
    identity,stored,stored_raw=_read(identity["name"],identity["handle"])
    if identity["fingerprint"]!=raw or _active(stored,raw)!=updated:
        raise RuntimeError("Update committed, but readback changed; inspect before retrying")
    return {"action":action,**_public(identity,stored,stored_raw,include_definition),"preview":_preview(built),
            "changed_parameters":{k:{"before":current[k],"after":updated[k]} for k in values}}
