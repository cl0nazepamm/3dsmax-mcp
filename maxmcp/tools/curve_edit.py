"""Guarded curve inspection, image targeting and atomic knot edits."""
from __future__ import annotations

import json
import math
from typing import Any

from ..coerce import DictList
from ..helpers.curve_runtime import CURVE_FUNCTIONS, FINGERPRINT, read_curve, run
from ..helpers.curves import Curve, Segment, curve_qa, keys, line
from ..helpers.mesh import MESH_FUNCTIONS, integer, point, target_script, vector
from ..server import client, mcp


def _curve(row):
    knots=row["knots"]; segments=[]
    for i in range(len(knots) if row["closed"] else len(knots)-1):
        a,b=knots[i],knots[(i+1)%len(knots)]
        segments.append(line(a["pos"],b["pos"]) if a["seg"]=="line" else
                        Segment(a["pos"],a["out_vec"],b["in_vec"],b["pos"]))
    return Curve(segments,row["closed"]) if segments else None


def _viewport(**kwargs):
    from .viewport import agent_viewport
    return agent_viewport(**kwargs)


def _same(reference,name,handle):
    current=read_curve(name,handle)
    if current["curve_token"]!=reference:
        raise RuntimeError("STALE_CURVE: shape changed during inspection; inspect again")


@mcp.tool()
def inspect_curve(
    name: str = "",
    handle: int = 0,
    action: str = "read",
    spline: int = 1,
    knot_ids: list[int] | None = None,
    capture: bool = False,
    x: float | None = None,
    y: float | None = None,
    expected_view: str = "",
    tolerance: float = 0.025,
    limit: int = 100,
) -> dict[str, Any]:
    """Read an editable spline's world knots/handles, QA, and stale-edit token.

    Spline is 1-based. knot_ids filters output; limit caps rows, not QA. read
    reports sampled planarity/intersections/tangent breaks (corners can be intended).
    capture labels K#/I#/O# into an AGENT VIEWPORT image, never the user's view.
    pick needs x/y normalized to that image and expected_view; ranks knot and
    handle candidates within tolerance (fraction of shorter image dimension).
    Visibility is not guaranteed: overlapping front/back handles are ambiguous.
    Pass curve_token as edit_curve.expected_curve. Any base-geometry, topology
    or object-transform change invalidates it; selection alone does not.
    No conversion, selection changes or scene helper objects. Maximum 2000 base
    knots; QA uses a bounded polyline approximation, not an exact intersection test.
    """
    if action not in {"read","pick"}: raise ValueError("action must be read or pick")
    integer(spline,"spline",high=128); integer(limit,"limit",high=1000)
    if type(capture) is not bool: raise ValueError("capture must be boolean")
    if knot_ids is not None:
        if not isinstance(knot_ids,list) or not knot_ids: raise ValueError("knot_ids needs a nonempty ID list")
        for k in knot_ids: integer(k,"knot ID",high=2000)
    if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or not 0<=tolerance<=.25:
        raise ValueError("tolerance must be in [0,.25]")
    if action=="pick":
        if not isinstance(expected_view,str) or not expected_view: raise ValueError("pick needs expected_view from capture")
        for value in (x,y):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:
                raise ValueError("pick needs normalized x/y in [0,1]")
    elif x is not None or y is not None: raise ValueError("x/y require action=pick")
    data=read_curve(name,handle)
    name,handle=data["name"],data["handle"]
    if spline>len(data["splines"]): raise ValueError("spline index is out of range")
    row=data["splines"][spline-1]
    ids=set(knot_ids) if knot_ids is not None else {k["knot"] for k in row["knots"]}
    if ids-{k["knot"] for k in row["knots"]}: raise ValueError("knot ID is out of range")
    chosen=[k for k in row["knots"] if k["knot"] in ids]
    result={k:v for k,v in data.items() if k!="splines"}
    result.update(spline=spline,closed=row["closed"],knot_count=len(row["knots"]),
                  knots=chosen[:limit],truncated=len(chosen)>limit)
    c=_curve(row)
    if c:
        try: result["qa"]=curve_qa(c)
        except ValueError as exc: result["qa"]={"complete":False,"reason":str(exc)}
    else: result["qa"]={"complete":False,"reason":"Spline has no segments"}
    labels=[]; candidates=[]
    for k in chosen:
        for component,field,prefix in (("knot","pos","K"),("in_handle","in_vec","I"),("out_handle","out_vec","O")):
            if component!="knot" and k["type"] not in {"bezier","bezierCorner","smooth"}: continue
            candidates.append({"spline":spline,"knot":k["knot"],"component":component,"point":k[field],"type":k["type"]})
            if len(labels)<100: labels.append({"point":k[field],"text":prefix+str(k["knot"])})
    if action=="pick":
        ranked=[]; invisible=0
        width=height=None
        if not candidates:
            reply=_viewport(action="ray",x=x,y=y,expected_view=expected_view)
            if reply.get("view_token")!=expected_view: raise RuntimeError("STALE_VIEW: capture again")
        for offset in range(0,len(candidates),1000):
            chunk=candidates[offset:offset+1000]
            reply=_viewport(action="project",points=[c["point"] for c in chunk],expected_view=expected_view)
            if reply.get("view_token")!=expected_view: raise RuntimeError("STALE_VIEW: capture again")
            w,h=reply.get("width"),reply.get("height")
            if not isinstance(w,int) or not isinstance(h,int) or min(w,h)<2: raise RuntimeError("Invalid viewport dimensions")
            if width is not None and (w,h)!=(width,height): raise RuntimeError("STALE_VIEW: image dimensions changed")
            width,height=w,h
            pixels=reply.get("pixels")
            if not isinstance(pixels,list) or len(pixels)!=len(chunk): raise RuntimeError("Incomplete curve projection")
            projections=reply.get("projections",[])
            for i,(candidate,pixel) in enumerate(zip(chunk,pixels)):
                if pixel is None or (len(projections)==len(chunk) and projections[i].get("in_front") is False):
                    invisible+=1; continue
                px,py=vector(pixel,"projection",size=2)
                distance=math.hypot(px-x*(w-1),py-y*(h-1))
                if distance<=tolerance*min(w,h):
                    ranked.append({**candidate,"distance_pixels":distance,"image":[px/(w-1),py/(h-1)]})
        ranked.sort(key=lambda c:(c["distance_pixels"],c["knot"],c["component"]))
        result.update(candidates=ranked[:limit],ambiguous=len(ranked)>1,
                      candidates_truncated=len(ranked)>limit,unprojectable=invisible,view_token=expected_view,
                      visibility="Projected candidates; occlusion is not certified")
    if capture:
        payload={"action":"capture","source":"agent","labels":labels}
        if expected_view: payload["expected_view"]=expected_view
        reply=json.loads(client.send_command(json.dumps(payload),cmd_type="native:agent_viewport")["result"])
        if "error" in reply: raise RuntimeError(str(reply["error"]))
        result["capture"]={**reply,"labels":len(labels),"labels_truncated":len(candidates)>len(labels)}
    if capture or action=="pick": _same(data["curve_token"],name,handle)
    return result


@mcp.tool()
def edit_curve(
    expected_curve: str,
    edits: DictList,
    name: str = "",
    handle: int = 0,
) -> dict[str, Any]:
    """Atomic world-space base-spline edits guarded by inspect_curve.curve_token.

    edits: [{op:"set",spline:1,knot:2,pos:[x,y,z],in_vec:...,out_vec:...,
    type:corner|smooth|bezier|bezierCorner}]. Fields are optional; moving pos
    translates existing handles unless explicitly replaced. Handles are absolute
    world positions. Each knot appears once per batch; all IDs preflight before
    writes. Editing smooth/corner handles requires type:"bezierCorner" or "bezier".

    Topology operations: {op:"insert",spline:1,segment:2,param:.5},
    {op:"delete",spline:1,knot:2}, {op:"reverse",spline:1},
    {op:"close"|"open",spline:1}. Use one topology operation per call and
    inspect again for new IDs. No conversion or stack collapse. Shared bases are
    rejected; make unique first. A manual edit to a curve_model object blocks its
    parameter updates until geometry returns to a retained recipe state.
    """
    if not isinstance(expected_curve,str) or not FINGERPRINT.fullmatch(expected_curve):
        raise ValueError("expected_curve must be the token from inspect_curve")
    if not isinstance(edits,list) or not 1<=len(edits)<=1000: raise ValueError("edits needs 1-1000 operations")
    preflight=[]; writes=[]; seen=set()
    for edit in edits:
        keys(edit,"op spline knot segment param pos in_vec out_vec type","edit")
        op=edit.get("op","set"); s=integer(edit.get("spline",1),"spline",high=128)
        if op not in {"set","insert","delete","reverse","close","open"}: raise ValueError("Unknown curve edit op")
        if op!="set" and len(edits)!=1: raise ValueError("Topology operations require a separate call, followed by inspection")
        allowed={"set":"op spline knot pos in_vec out_vec type","insert":"op spline segment param",
                 "delete":"op spline knot","reverse":"op spline","close":"op spline","open":"op spline"}[op]
        keys(edit,allowed,"edit")
        preflight.append(f'if {s} > numSplines shape do throw "Spline ID out of range"')
        if op in {"set","delete"}:
            k=integer(edit.get("knot"),"knot",high=2000)
            preflight.append(f'if {k} > numKnots shape {s} do throw "Knot ID out of range"')
            if (s,k) in seen: raise ValueError("Each knot may appear only once per batch")
            seen.add((s,k))
        if op=="set":
            if not set(edit)&{"pos","in_vec","out_vec","type"}: raise ValueError("set needs a position, handle or type change")
            kind=edit.get("type")
            if kind is not None and kind not in {"corner","smooth","bezier","bezierCorner"}: raise ValueError("Invalid knot type")
            if "in_vec" in edit or "out_vec" in edit:
                if kind is not None and kind not in {"bezier","bezierCorner"}: raise ValueError("Explicit handles require a Bezier knot type")
                if kind is None:
                    preflight.append(f'if (getKnotType shape {s} {k}) != #bezier and (getKnotType shape {s} {k}) != #bezierCorner do throw "Convert knot type to bezierCorner before editing handles"')
            # Read the previous handles before moving the knot; some Max knot
            # types translate handles themselves on setKnotPoint.
            writes += [f"local oldP = getKnotPoint shape {s} {k}",f"local oldIn = getInVec shape {s} {k}",f"local oldOut = getOutVec shape {s} {k}"]
            if kind: writes.append(f"setKnotType shape {s} {k} #{kind}")
            if "pos" in edit:
                p=point(vector(edit["pos"],"pos"))
                writes.extend([f"local newP = {p} * inverseTM",f"setKnotPoint shape {s} {k} newP"])
            else: writes.append("local newP = oldP")
            for field,setter,old in (("in_vec","setInVec","oldIn"),("out_vec","setOutVec","oldOut")):
                p=f"({point(vector(edit[field],field))} * inverseTM)" if field in edit else f"({old}+newP-oldP)"
                writes.append(f"if (getKnotType shape {s} {k}) == #bezier or (getKnotType shape {s} {k}) == #bezierCorner do {setter} shape {s} {k} {p}")
        elif op=="insert":
            seg=integer(edit.get("segment"),"segment",high=2000)
            t=edit.get("param",.5)
            if isinstance(t,bool) or not isinstance(t,(float,int)) or not math.isfinite(t) or not 0<t<1:
                raise ValueError("insert param must be strictly between 0 and 1")
            preflight.append(f'if {seg} > numSegments shape {s} do throw "Segment ID out of range"')
            preflight.append('if numKnots shape >= 2000 do throw "Knot limit reached"')
            writes.append(f"refineSegment shape {s} {seg} {t}")
        elif op=="delete":
            preflight.append(f'if numKnots shape {s} <= (if isClosed shape {s} then 3 else 2) do throw "Deletion would leave too few knots"')
            writes.append(f"deleteKnot shape {s} {k}")
        else:
            if op=="close": preflight.append(f'if numKnots shape {s} < 3 do throw "Closing needs at least three knots"')
            writes.append(f"reverse shape {s} keepFirstKnot:true" if op=="reverse" else f"{op} shape {s}")
    preflight_code=";\n".join(preflight); write_code=";\n".join(writes)
    raw=run(f'''(
        {MESH_FUNCTIONS}
        {CURVE_FUNCTIONS}
        local holding = false
        try (
            {target_script(name,handle)}
            local shape = cvBase obj
            if (cvToken obj includeTransform:true) != "{expected_curve}" do throw "STALE_CURVE: inspect again before editing"
            local instances = #(); InstanceMgr.GetInstances obj &instances
            if instances.count > 1 do throw "Spline base is instanced; make it unique before editing"
            local tm = obj.objecttransform
            if abs (dot (cross tm.row1 tm.row2) tm.row3) < 1e-12 do throw "Object transform is singular; repair scale before editing"
            local inverseTM = inverse tm
            {preflight_code}
            if theHold.Holding() do throw "An undo transaction is active"
            theHold.Begin(); holding = true
            in coordsys local (
                {write_code}
                updateShape shape
            )
            local token = cvToken obj includeTransform:true
            local answer = (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") + "|" + token
            theHold.Accept "MCP curve edit"; holding = false
            try (completeredraw()) catch ()
            answer
        ) catch (local detail = getCurrentException() as string; if holding do theHold.Cancel(); "__ERROR__|"+detail)
    )''')
    h,token=raw.split("|",1)
    result=read_curve(name,int(h))
    if result["curve_token"]!=token: raise RuntimeError("Curve edit committed, but changed before readback; inspect again")
    return {"edited":len(edits),**result,"hint":"Inspect new IDs after topology edits; use the returned curve_token for the next edit."}
