"""Direct, inspectable polygon modeling without an application restart.

Published MAXScript poly APIs run on the native bridge's main thread. Batches
own one hold and cancel it on failure; no UI button automation or mesh copies.
"""
from __future__ import annotations

import math
import json
import base64
import os
import tempfile
import uuid
from typing import Any

from ..server import client, mcp
from ..coerce import DictList, DictValue
from ..helpers.maxscript import safe_string
from ..helpers.mesh import MESH_FUNCTIONS, integer, point, selection_script, target_script, vector
from .splines import _parse_p3


def _run(script: str) -> str:
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    if not raw:
        raise RuntimeError("Mesh operation returned no readback; inspect before retrying")
    return raw


def _counts(raw):
    return dict(zip(("vertices", "edges", "faces"), map(int, raw.split(","))))


def _ids(raw):
    return [int(i) for i in raw.split(",") if i]


@mcp.tool()
def inspect_mesh(
    name: str = "",
    handle: int = 0,
    level: str = "face",
    selection: DictValue | None = None,
    limit: int = 100,
    capture: bool = False,
) -> dict[str, Any]:
    """Inspect an Editable Poly BASE cage with actionable component IDs and optional labeled capture.

    level: vertex | edge | face. Omit name/handle to inspect the one selected node.
    selection filters (intersect): indices:[1,...], current:true, all:true,
    bbox:[minx,miny,minz,maxx,maxy,maxz], near:[x,y,z]+radius,
    normal:[x,y,z]+angle (faces, degrees, default 10), boundary:true (edges),
    sharp:30 (edges with at least this dihedral angle, excludes open borders).
    Bbox/near match component CENTERS in world space; normals are world normals.
    Defaults to all components, capped by limit (1-1000). Does not convert geometry.
    Returns counts, selected IDs, ordered vertex IDs, positions, normals, mesh_token.
    IDs belong to the base cage beneath modifiers, not the subdivided surface.
    capture=true labels up to 100 matched components in AGENT VIEWPORT when open,
    otherwise the current viewport, and
    saves a PNG; inspect that file. Narrow the filter for legible labels.
    """
    level = level.strip().lower()
    integer(limit, "limit", high=1000)
    choose = selection_script(selection, level, default_all=True)
    agent_context = None
    if capture:
        from .viewport import _agent_context
        agent_context = _agent_context(client)
    capture_on_active = capture and agent_context is None
    capture_id = uuid.uuid4().hex
    image_path = os.path.join(tempfile.gettempdir(), "mcp_mesh_"+capture_id+".png").replace("\\", "/")
    overlay = ""
    cleanup = "true"
    if capture_on_active:
        # Nitrous requires drawing inside a redraw callback. Register only around
        # this capture; no persistent overlay, helper nodes, or selection changes.
        overlay = f'''
            global MCPMeshOverlayData = #()
            global MCPMeshOverlayDraw
            global MCPMeshOverlayError = ""
            global MCPMeshOverlayCalls = 0
            unregisterRedrawViewsCallback MCPMeshOverlayDraw
            fn MCPMeshOverlayDraw = (
              try (
                MCPMeshOverlayCalls += 1
                gw.setTransform (matrix3 1)
                for row in MCPMeshOverlayData do (
                    gw.marker row[1] #smallHollowBox color:(color 70 220 255)
                    gw.text row[1] row[2] color:(color 255 220 60)
                )
                gw.enlargeUpdateRect #whole
                gw.updateScreen()
              ) catch (MCPMeshOverlayError = getCurrentException() as string)
            )
            local labelCount = 0
            for i in chosen while labelCount < {min(limit,100)} do (
                append MCPMeshOverlayData #((mcCenter mesh tm #{level} i), ("{level[0].upper()}" + (i as string)))
                labelCount += 1
            )
            registerRedrawViewsCallback MCPMeshOverlayDraw
            completeredraw()
        '''
        cleanup = 'try (unregisterRedrawViewsCallback MCPMeshOverlayDraw; MCPMeshOverlayDraw = undefined; MCPMeshOverlayData = undefined; MCPMeshOverlayError = undefined; MCPMeshOverlayCalls = undefined) catch ()'
        overlay = overlay.replace("MCPMeshOverlay", "MCPMeshOverlay_"+capture_id)
        cleanup = cleanup.replace("MCPMeshOverlay", "MCPMeshOverlay_"+capture_id)
    raw = _run(f'''(
        {MESH_FUNCTIONS}
        try (
            {target_script(name, handle)}
            if classof obj.baseobject != Editable_Poly do throw "Editable Poly base required. Use mesh_edit with convert=true explicitly."
            local mesh = obj.baseobject
            local tm = obj.objectTransform
            {choose}
            local out = stringstream ""
            format "NAME|%\\n" ((dotNetClass "System.Convert").ToBase64String ((dotNetClass "System.Text.Encoding").UTF8.GetBytes obj.name)) to:out
            local instances = #()
            InstanceMgr.GetInstances obj &instances
            format "INSTANCES|%\\n" instances.count to:out
            format "META|%|%|%|%\\n" (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") (mcCounts mesh) obj.modifiers.count (mcToken obj) to:out
            format "MATCH|%\\n" chosen.numberSet to:out
            local currentSelection = mcSelected mesh #{level}
            local currentIds = currentSelection as array
            if currentIds.count > {limit} do currentIds.count = {limit}
            format "SELECTED|%|%\\n" (mcIds currentIds) currentSelection.numberSet to:out
            local emitted = 0
            for i in chosen while emitted < {limit} do (
                local verts = mcVerts mesh #{level} i
                local normal = if #{level} == #face then mcNormal mesh tm i else [0,0,0]
                format "C|%|%|%|%\\n" i (mcPoint (mcCenter mesh tm #{level} i)) (mcIds verts) (mcPoint normal) to:out
                emitted += 1
            )
            {overlay}
            out as string
        ) catch ({cleanup}; "__ERROR__|" + (getCurrentException() as string))
    )''')
    if capture_on_active:
        # Nitrous performs the callback after returning to Max's message loop.
        # Capturing in the registration request produces an unlabeled image.
        try:
            _run(f'''(try (
                if MCPMeshOverlay_{capture_id}Error != "" do throw MCPMeshOverlay_{capture_id}Error
                if MCPMeshOverlay_{capture_id}Calls == 0 do throw "Viewport overlay has not redrawn; retry capture"
                local bmp = gw.getViewportDib()
                bmp.filename = "{image_path}"
                save bmp
                close bmp
                "OK"
            ) catch ("__ERROR__|" + getCurrentException()))''')
        finally:
            _run(f'({cleanup}; completeredraw(); "OK")')
    result = {"name":name, "level":level, "space":"world", "cage":"base", "components":[]}
    for line in raw.splitlines():
        fields = line.split("|")
        if fields[0] == "META":
            result.update(handle=int(fields[1]), counts=_counts(fields[2]), modifiers_above=int(fields[3]), mesh_token=fields[4])
        elif fields[0] == "MATCH": result["matched"] = int(fields[1])
        elif fields[0] == "NAME": result["name"] = base64.b64decode(fields[1]).decode("utf-8")
        elif fields[0] == "INSTANCES": result["instance_count"] = int(fields[1])
        elif fields[0] == "SELECTED":
            result["selected"] = _ids(fields[1]); result["selected_count"] = int(fields[2])
        elif fields[0] == "C":
            row = {"id":int(fields[1]), "center":_parse_p3(fields[2]), "vertices":_ids(fields[3])}
            if level == "face": row["normal"] = _parse_p3(fields[4])
            result["components"].append(row)
    if "mesh_token" not in result:
        raise RuntimeError(f"Invalid mesh readback: {raw[:200]}")
    result["returned"] = len(result["components"])
    result["truncated"] = result["returned"] < result["matched"]
    if capture:
        if agent_context:
            payload={"action":"capture","source":"agent","expected_view":agent_context["view_token"],
                "labels":[{"point":row["center"],"text":level[0].upper()+str(row["id"])} for row in result["components"][:100]]}
            result["capture"]=json.loads(client.send_command(json.dumps(payload),cmd_type="native:agent_viewport")["result"])
            result["capture"]["labels"]=len(payload["labels"])
        else:
            result["capture"] = {"file":image_path, "labels":min(result["matched"],limit,100), "size_bytes":os.path.getsize(image_path)}
    return result


def _number(op, key, default=None, *, positive=False):
    val = op.get(key, default)
    if isinstance(val, bool) or not isinstance(val, (int,float)) or not math.isfinite(val) or (positive and val <= 0):
        raise ValueError(f"{key} must be a {'positive ' if positive else ''}finite number")
    return float(val)


def _operation(op: dict, index: int) -> str:
    if not isinstance(op, dict): raise ValueError("Each operation must be an object")
    action = str(op.get("op", "")).lower()
    level = str(op.get("level", "face")).lower()
    common = {"op","level","selection"}
    keys = {
        "select":set(), "move":{"offset"}, "scale":{"factors","pivot"},
        "extrude":{"amount","mode"}, "bevel":{"amount","outline","mode"},
        "inset":{"amount","mode"}, "chamfer":{"amount","segments"},
        "connect":{"segments","pinch","slide"}, "bridge":{"segments"},
        "delete":set(), "cap":set(), "relax":{"amount","iterations"},
    }
    if action not in keys: raise ValueError(f"Unsupported mesh op: {action}")
    if set(op) - common - keys[action]: raise ValueError(f"Unknown keys for {action}: {sorted(set(op)-common-keys[action])}")
    choose = selection_script(op.get("selection"), level)
    if action in {"extrude","inset","bevel"} and level != "face":
        raise ValueError(f"{action} requires face level")
    if action in {"chamfer","connect","bridge","cap"} and level != "edge":
        if not (action == "bridge" and level == "face"):
            raise ValueError(f"{action} requires edge level" + (" or face level" if action == "bridge" else ""))
    body = ""
    if action in {"move","scale","relax"}:
        body += f'local affected = case #{level} of (#vertex: chosen; #edge: polyop.getVertsUsingEdge mesh chosen; #face: polyop.getVertsUsingFace mesh chosen)\n'
    if action == "move":
        offset = vector(op.get("offset"), "offset")
        body += f'local invTM = inverse tm; for v in affected do polyop.setVert mesh v (((polyop.getVert mesh v) * tm + {point(offset)}) * invTM)'
    elif action == "scale":
        factors = vector(op.get("factors"), "factors")
        pivot = vector(op["pivot"],"pivot") if "pivot" in op else None
        body += ('local pivot = '+point(pivot) if pivot else 'local pivot = [0,0,0]; for v in affected do pivot += (polyop.getVert mesh v) * tm; pivot /= affected.numberSet') + '\n'
        body += f'local invTM = inverse tm; for v in affected do (local p = (polyop.getVert mesh v) * tm - pivot; polyop.setVert mesh v ((pivot + p * {point(factors)}) * invTM))'
    elif action in {"extrude","bevel","inset"}:
        mode = op.get("mode","group")
        modes = {"group":0,"local":1,"polygon":2}
        if mode not in modes: raise ValueError("mode must be group, local, or polygon")
        amount = _number(op,"amount",positive=action == "inset")
        body += f'mesh.extrusionType = {modes[mode]}\n'
        if action == "extrude": body += f'polyop.extrudeFaces mesh chosen {amount}'
        elif action == "bevel": body += f'polyop.bevelFaces mesh chosen {amount} {_number(op,"outline",0)}'
        else: body += f'polyop.bevelFaces mesh chosen 0.0 {-amount}'
    elif action == "chamfer":
        segments = integer(op.get("segments",1), "segments", high=32)
        body += f'mesh.edgeChamferSegments = {segments}; mesh.edgeChamferSmooth = true; mesh.EditablePoly.chamferEdges {_number(op,"amount",positive=True)} open:false'
    elif action == "connect":
        segments = integer(op.get("segments",1), "segments", high=100)
        pinch = _number(op,"pinch",0); slide = _number(op,"slide",0)
        if not -100 <= pinch <= 100 or not -100 <= slide <= 100: raise ValueError("pinch/slide must be -100 to 100")
        body += f'mesh.connectEdgeSegments = {segments}; mesh.connectEdgePinch = {pinch}; mesh.connectEdgeSlide = {slide}; if not (mesh.EditablePoly.ConnectEdges()) do throw "Edges could not be connected"'
    elif action == "bridge":
        body += f'mesh.bridgeSegments = {integer(op.get("segments",1),"segments",high=100)}; mesh.bridgeSelected = 1; if not (mesh.EditablePoly.bridge selLevel:#{level}) do throw "Selection could not be bridged"'
    elif action == "cap":
        body += 'if not (mesh.EditablePoly.capHoles #Edge) do throw "Selection has no cappable border"'
    elif action == "delete":
        method = {"vertex":"deleteVerts","edge":"deleteEdges","face":"deleteFaces"}[level]
        body += f'polyop.{method} mesh chosen'
    elif action == "relax":
        amount = _number(op,"amount",0.25)
        if not 0 <= amount <= 1: raise ValueError("relax amount must be 0-1")
        iterations = integer(op.get("iterations",1),"iterations",high=100)
        body += f'mesh.relaxAmount = {amount}; mesh.relaxIterations = {iterations}; mesh.relaxHoldBoundaryPoints = true; mesh.relaxHoldOuterPoints = true; if not (mesh.EditablePoly.Relax selLevel:#{level}) do throw "Selection could not be relaxed"'
    return f'''(
        local tm = obj.objectTransform
        {choose}
        if chosen.numberSet == 0 do throw "Operation {index}: selection matched no {level} components"
        local beforeCounts = mcCounts mesh
        mcSelect mesh #{level} chosen
        {body}
        polyop.collapseDeadStructs mesh
        update obj
        lastLevel = #{level}
        format "STEP|{action}|%|%|%\\n" chosen.numberSet beforeCounts (mcCounts mesh) to:out
    )'''


@mcp.tool()
def mesh_edit(
    operations: DictList,
    name: str = "",
    handle: int = 0,
    convert: bool = False,
    expected_mesh: str = "",
    show_selection: bool = True,
) -> dict[str, Any]:
    """Edit vertices, edges, and faces as one undoable batch, rolling back on failure.

    Each operation: {op, level:vertex|edge|face, selection:{...}, operation values}.
    Selection uses inspect_mesh filters. Omitted selection uses current component
    selection; all:true is explicit. Geometric filters are reevaluated per step.
    ops: select; move(offset:[x,y,z]); scale(factors:[x,y,z],pivot?:world point);
    extrude(amount,mode:group|local|polygon); inset(amount,mode); bevel(amount,outline,mode);
    chamfer(amount,segments); connect(segments,pinch,slide); bridge(segments);
    delete; cap; relax(amount:0-1,iterations). Chamfer/connect/cap require edges;
    extrude/inset/bevel require faces; bridge accepts faces or edges.
    Moves/scales and selection filters use WORLD space. Surface-operation amounts
    use mesh LOCAL units; inspect transforms before editing scaled meshes.
    Edits the Editable Poly BASE cage, preserving modifiers above it. convert=true
    explicitly converts a non-poly object and collapses its stack in the same undo.
    expected_mesh optionally guards IDs with inspect_mesh's mesh_token. After topology
    edits, inspect again or use geometric selectors instead of reusing old IDs.
    show_selection displays the last component selection in Max's Modify panel.
    Shared base geometry changes in all instances; inspect_mesh reports instance_count.
    """
    if not operations or len(operations) > 100: raise ValueError("operations must contain 1-100 edits")
    steps = [_operation(op, i+1) for i,op in enumerate(operations)]
    target = target_script(name,handle)
    guard = f'if classof obj.baseobject != Editable_Poly do throw "Stale mesh token: object type changed"; if mcToken obj != "{safe_string(expected_mesh)}" do throw "Stale mesh token: inspect_mesh again"' if expected_mesh else ""
    # subObjectLevel's MAXScript setter does not parse a case expression inside
    # this try block. The final operation is already validated in Python.
    ui_level = {"vertex": 1, "edge": 2, "face": 4}[str(operations[-1].get("level", "face")).lower()]
    selection_ui = f'''select obj; max modify mode; modPanel.setCurrentObject obj.baseobject; subObjectLevel = {ui_level}''' if show_selection else ""
    raw = _run(f'''(
        {MESH_FUNCTIONS}
        local holding = false
        try (
            {target}
            if classof obj.baseobject != Editable_Poly and not {str(convert).lower()} do throw "Editable Poly base required; pass convert=true explicitly to collapse the stack"
            {guard or 'true'}
            if theHold.Holding() do throw "An undo transaction is active; retry after it completes"
            local wasConverted = false
            theHold.Begin(); holding = true
            if classof obj.baseobject != Editable_Poly do (convertToPoly obj; wasConverted = true)
            local mesh = obj.baseobject
            local out = stringstream ""
            format "NAME|%\\n" ((dotNetClass "System.Convert").ToBase64String ((dotNetClass "System.Text.Encoding").UTF8.GetBytes obj.name)) to:out
            local lastLevel = #face
            {chr(10).join(steps)}
            format "DONE|%|%|%|%\\n" (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") (mcCounts mesh) wasConverted (mcToken obj) to:out
            theHold.Accept "MCP mesh edit"; holding = false
            try ({selection_ui or 'true'}) catch ()
            completeredraw()
            out as string
        ) catch (
            local err = getCurrentException() as string
            if holding do (theHold.Cancel(); holding = false)
            "__ERROR__|" + err
        )
    )''')
    result = {"name":name, "steps":[]}
    for line in raw.splitlines():
        f = line.split("|")
        if f[0] == "NAME": result["name"] = base64.b64decode(f[1]).decode("utf-8")
        if f[0] == "STEP": result["steps"].append({"op":f[1],"matched":int(f[2]),"before":_counts(f[3]),"after":_counts(f[4])})
        elif f[0] == "DONE": result.update(handle=int(f[1]),counts=_counts(f[2]),converted=f[3]=="true",mesh_token=f[4])
    if "mesh_token" not in result: raise RuntimeError(f"Invalid mesh edit readback: {raw[:200]}")
    return result


@mcp.tool()
def create_mesh(name: str, vertices: list[list[float]], faces: list[list[int]]) -> dict[str, Any]:
    """Create an Editable Poly from explicit WORLD vertices and ordered polygon faces.

    faces use 1-based vertex IDs, at least three distinct IDs each. Counterclockwise
    winding viewed from outside gives outward normals. Supports quads and n-gons;
    no forced triangulation. Useful for curved furniture, lofted sections, and custom
    architectural details; then refine through inspect_mesh/mesh_edit.
    Rejects an existing name. Creation is one undo step with rollback on error.
    """
    return _create_mesh(name, vertices, faces)


def _create_mesh(name: str, vertices: list[list[float]], faces: list[list[int]], *,
                 before_accept: str = "") -> dict[str, Any]:
    """Internal construction hook runs within the creation hold.

    before_accept is trusted implementation MAXScript, never a public tool
    argument. Persistent builders can attach their definition before acceptance.
    """
    if not name.strip(): raise ValueError("name is required")
    if not 3 <= len(vertices) <= 50000 or not 1 <= len(faces) <= 50000:
        raise ValueError("Use 3-50000 vertices and 1-50000 faces")
    verts = [vector(v, f"vertices[{i}]") for i,v in enumerate(vertices)]
    center = [(min(p[i] for p in verts)+max(p[i] for p in verts))*0.5 for i in range(3)]
    local_verts = [[p[i]-center[i] for i in range(3)] for p in verts]
    for i,face in enumerate(faces):
        if not isinstance(face,(list,tuple)) or len(face) < 3:
            raise ValueError(f"faces[{i}] needs at least three distinct IDs")
        for v in face: integer(v, f"faces[{i}] vertex ID", high=len(verts))
        if len(set(face)) != len(face): raise ValueError(f"faces[{i}] repeats a vertex ID")
    if sum(map(len,faces)) > 500000:
        raise ValueError("Use at most 500000 polygon corners")
    v_literal = "#(" + ",".join(point(v) for v in local_verts) + ")"
    f_literal = "#(" + ",".join("#("+",".join(map(str,f))+")" for f in faces) + ")"
    raw = _run(f'''(
        {MESH_FUNCTIONS}
        local holding = false
        try (
            if (getNodeByName "{safe_string(name)}" exact:true) != undefined do throw "A node with this name already exists"
            if theHold.Holding() do throw "An undo transaction is active"
            theHold.Begin(); holding = true
            local obj = Editable_Mesh name:"{safe_string(name)}" pos:{point(center)}
            convertToPoly obj
            local mesh = obj.baseobject
            for p in {v_literal} do polyop.createVert mesh p
            for f in {f_literal} do (local faceId = polyop.createPolygon mesh f; if faceId == undefined or faceId == 0 do throw "Could not create polygon; check winding and topology")
            update obj
            {before_accept or 'true'}
            local answer = (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") + "|" + (mcCounts mesh) + "|" + mcToken obj
            theHold.Accept "MCP create mesh"; holding = false
            completeredraw()
            answer
        ) catch (
            local err = getCurrentException() as string
            if holding do theHold.Cancel()
            "__ERROR__|" + err
        )
    )''')
    h,counts,token = raw.split("|",2)
    return {"name":name,"handle":int(h),"counts":_counts(counts),"mesh_token":token}
