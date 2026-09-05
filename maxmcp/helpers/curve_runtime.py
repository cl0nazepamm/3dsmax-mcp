"""Shared base-spline identity and bounded MAXScript readback.

Max's spline methods require a node, even when they access its spline base
beneath modifiers. ``coordsys local`` reads that base without transforming it;
the node object transform is applied explicitly only for world targeting.
"""
from __future__ import annotations

import base64
import json
import re

from .mesh import MESH_FUNCTIONS, target_script

FINGERPRINT = re.compile(r"[0-9A-F]{2}(?:-[0-9A-F]{2}){31}")

CURVE_FUNCTIONS = r'''
fn cvHash text = (
    local sha = (dotNetClass "System.Security.Cryptography.SHA256").Create()
    local bytes = (dotNetClass "System.Text.Encoding").UTF8.GetBytes text
    local result = (dotNetClass "System.BitConverter").ToString (sha.ComputeHash bytes)
    sha.Dispose(); result as string
)
fn cvBase obj = (
    if not isKindOf obj.baseobject SplineShape do throw "Editable spline base required; no automatic conversion"
    if numSplines obj > 128 or numKnots obj > 2000 do throw "Curve inspection limit: 128 splines / 2000 knots"
    obj
)
fn cvToken obj includeTransform:false = (
    local shape = cvBase obj
    local out = stringstream ""
    format "%|" (numSplines shape) to:out
    in coordsys local (
        for s = 1 to numSplines shape do (
            format "%|%|" (isClosed shape s) (numKnots shape s) to:out
            for k = 1 to numKnots shape s do (
                format "%|%|%|%|" (getKnotType shape s k) (mcPoint (getKnotPoint shape s k)) (mcPoint (getInVec shape s k)) (mcPoint (getOutVec shape s k)) to:out
                if k <= numSegments shape s do format "%|" (getSegmentType shape s k) to:out
            )
        )
    )
    if includeTransform do (
        format "%|" ((getHandleByAnim obj) as integer64) to:out
        for row = 1 to 4 do format "%|" (mcPoint obj.objecttransform[row]) to:out
    )
    cvHash (out as string)
)
fn cvB64 text = ((dotNetClass "System.Convert").ToBase64String ((dotNetClass "System.Text.Encoding").UTF8.GetBytes text))
'''


def run(script):
    from ..server import client
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    if not raw:
        raise RuntimeError("Curve operation returned no readback; inspect before retrying")
    return raw


def decode(value):
    return base64.b64decode(value, validate=True).decode("utf-8")


def read_curve(name="", handle=0):
    raw = run(f'''(
        {MESH_FUNCTIONS}
        {CURVE_FUNCTIONS}
        try (
            {target_script(name,handle)}
            local shape = cvBase obj
            local tm = obj.objecttransform
            local out = stringstream ""
            format "NAME|%\\n" (cvB64 obj.name) to:out
            format "META|%|%|%|%\\n" (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") (cvToken obj includeTransform:true) (cvToken obj) obj.modifiers.count to:out
            in coordsys local (
                for s = 1 to numSplines shape do (
                    format "SPL|%|%\\n" s (isClosed shape s) to:out
                    for k = 1 to numKnots shape s do (
                        local seg = if k <= numSegments shape s then getSegmentType shape s k else #line
                        format "KNOT|%|%|%|%|%|%|%\\n" s k (getKnotType shape s k) seg (mcPoint ((getKnotPoint shape s k)*tm)) (mcPoint ((getInVec shape s k)*tm)) (mcPoint ((getOutVec shape s k)*tm)) to:out
                    )
                )
            )
            out as string
        ) catch ("__ERROR__|" + (getCurrentException() as string))
    )''')
    result={"splines":[],"space":"world","cage":"base"}
    splines={}
    try:
        for line in raw.splitlines():
            fields=line.split("|")
            if fields[0]=="NAME": result["name"]=decode(fields[1])
            elif fields[0]=="META":
                result.update(handle=int(fields[1]),curve_token=fields[2],fingerprint=fields[3],modifiers_above=int(fields[4]))
            elif fields[0]=="SPL":
                row={"spline":int(fields[1]),"closed":fields[2]=="true","knots":[]}
                splines[row["spline"]]=row; result["splines"].append(row)
            elif fields[0]=="KNOT":
                splines[int(fields[1])]["knots"].append({"knot":int(fields[2]),"type":fields[3].lstrip("#"),"seg":fields[4].lstrip("#"),
                    "pos":json.loads(fields[5]),"in_vec":json.loads(fields[6]),"out_vec":json.loads(fields[7])})
        if not FINGERPRINT.fullmatch(result["curve_token"]) or not FINGERPRINT.fullmatch(result["fingerprint"]):
            raise ValueError("invalid curve tokens")
    except (KeyError,IndexError,ValueError) as exc:
        raise RuntimeError("Invalid curve readback") from exc
    return result
