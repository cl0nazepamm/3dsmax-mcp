"""Read-only evaluated geometry QA with explicit snapshot-only component IDs."""
from __future__ import annotations

import base64
import hashlib
import math
import tempfile
from pathlib import Path
from typing import Any

from ..helpers.geometry_qa import analyze_triangles
from ..helpers.mesh import integer, target_script
from ..helpers.maxscript import safe_string
from ..server import client, mcp


def _snapshot_script(name: str, handle: int, path: str, max_faces: int) -> str:
    """No temporary node, selection change, modifier collapse, or undo entry."""
    return f'''(
        local qaMesh = undefined
        local qaFile = undefined
        try (
            {target_script(name, handle)}
            qaMesh = snapshotAsMesh obj
            if qaMesh == undefined do throw "Object does not provide an evaluated triangle mesh"
            local nv = getNumVerts qaMesh
            local nf = getNumFaces qaMesh
            if nf > {max_faces} or nv > {max_faces * 3} do throw "Geometry QA limit exceeded; reduce subdivision or raise max_faces (at most 500000)"
            qaFile = createFile "{safe_string(path)}" encoding:#utf8 writeBOM:false
            if qaFile == undefined do throw "Cannot create geometry QA snapshot file"
            format "NAME|%\\n" ((dotNetClass "System.Convert").ToBase64String ((dotNetClass "System.Text.Encoding").UTF8.GetBytes obj.name)) to:qaFile
            format "META|%|%|%|%|%\\n" (formattedPrint ((getHandleByAnim obj) as integer64) format:"d") nv nf obj.modifiers.count (currentTime as integer) to:qaFile
            in coordsys world (
                for i = 1 to nv do (
                    local p = getVert qaMesh i
                    format "V|%,%,%\\n" (formattedPrint p.x format:".9g") (formattedPrint p.y format:".9g") (formattedPrint p.z format:".9g") to:qaFile
                )
            )
            for i = 1 to nf do (
                local f = getFace qaMesh i
                format "F|%,%,%\\n" (f.x as integer) (f.y as integer) (f.z as integer) to:qaFile
            )
            close qaFile
            qaFile = undefined
            delete qaMesh
            qaMesh = undefined
            "OK"
        ) catch (
            local detail = getCurrentException() as string
            if qaFile != undefined do try (close qaFile) catch ()
            if qaMesh != undefined do try (delete qaMesh) catch ()
            "__ERROR__|" + detail
        )
    )'''


def _parse_snapshot(raw: bytes) -> tuple[dict[str, Any], list[list[float]], list[list[int]]]:
    try:
        lines = raw.decode("ascii").splitlines()
        if len(lines) < 2 or not lines[0].startswith("NAME|") or not lines[1].startswith("META|"):
            raise ValueError("missing metadata")
        meta = lines[1].split("|")
        if len(meta) != 6:
            raise ValueError("invalid metadata")
        handle, nv, nf, modifiers, ticks = map(int, meta[1:])
        if handle <= 0 or min(nv, nf, modifiers) < 0 or len(lines) != 2 + nv + nf:
            raise ValueError("incomplete counts")
        vertices = []
        for line in lines[2:2 + nv]:
            if not line.startswith("V|"):
                raise ValueError("missing vertex")
            vertices.append([float(v) for v in line[2:].split(",")])
        faces = []
        for line in lines[2 + nv:]:
            if not line.startswith("F|"):
                raise ValueError("missing triangle")
            faces.append([int(v) for v in line[2:].split(",")])
        identity = {
            "name": base64.b64decode(lines[0][5:], validate=True).decode("utf-8"),
            "handle": handle, "modifiers_above": modifiers, "time_ticks": ticks,
            "snapshot_token": hashlib.sha256(raw).hexdigest()[:24],
        }
        return identity, vertices, faces
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f"Incomplete or invalid geometry snapshot: {exc}") from exc


@mcp.tool()
def geometry_qa(
    name: str = "",
    handle: int = 0,
    limit: int = 12,
    max_faces: int = 100000,
    area_epsilon: float = 0.0,
) -> dict[str, Any]:
    """Check evaluated mesh topology without changing the scene or collapsing its stack.

    Pass name/handle, or omit both for the one selected node. Counts boundary
    edges, >2-face non-manifold edges, inconsistent adjacent triangle winding,
    degenerate/duplicate triangles, disconnected edge-components and isolated
    vertices. Samples include world positions; limit=1..100 caps samples only.
    All geometry is checked up to max_faces (default 100000, maximum 500000;
    vertices capped at 3*max_faces). A limit failure never returns partial QA.
    area_epsilon uses squared scene units; 0 chooses bbox_diagonal^2 * 1e-12.
    IDs are evaluated snapshot vertices/triangles, NOT inspect_mesh base-cage IDs.
    Use sample centers to inspect the base cage before editing. snapshot_token
    identifies this report only and is not a mesh_edit expected_mesh token.
    Open boundaries/separate components can be intentional. No welding, repair,
    self-intersection, thickness, vertex-manifoldness, or custom-normal checks.
    """
    integer(limit, "limit", high=100)
    integer(max_faces, "max_faces", high=500000)
    if (isinstance(area_epsilon, bool) or not isinstance(area_epsilon, (int, float))
            or not math.isfinite(area_epsilon) or area_epsilon < 0):
        raise ValueError("area_epsilon must be a nonnegative finite number")
    # A bounded scratch file avoids returning megabytes of triangles through
    # the bridge and exposing them in the MCP response. Max always frees its
    # transient TriMesh, including error paths; Python owns the scratch path.
    with tempfile.TemporaryDirectory(prefix="mcp_geometry_qa_") as directory:
        path = Path(directory) / "snapshot.txt"
        response = str(client.send_command(
            _snapshot_script(name, handle, path.as_posix(), max_faces)
        ).get("result", ""))
        if response.startswith("__ERROR__|"):
            raise RuntimeError(response.split("|", 1)[1])
        if response != "OK":
            raise RuntimeError("Geometry QA returned no complete snapshot")
        raw = path.read_bytes()
    identity, vertices, faces = _parse_snapshot(raw)
    if len(faces) > max_faces or len(vertices) > 3 * max_faces:
        raise RuntimeError("Geometry QA snapshot exceeded the requested limit")
    report = analyze_triangles(vertices, faces, limit=limit, area_epsilon=area_epsilon)
    return {
        **identity,
        "scope": "evaluated_triangle_mesh", "space": "world",
        **report,
        "complete": True,
        "limits": {"max_faces": max_faces, "max_vertices": 3 * max_faces, "samples_per_issue": limit},
        "notes": [
            "Triangle/vertex samples belong to this evaluated snapshot, not the Editable Poly base cage. Re-inspect the base near sample centers before editing.",
            "Topology uses vertex indices without welding. Open boundaries and disconnected components may be intentional.",
            "Adjacent winding checks exclude degenerate faces and non-manifold edges; consistent winding does not establish outward or custom normals.",
            "Not checked: self/inter-object intersections, thickness, vertex-manifoldness, UVs or custom normals. Limits apply after Max evaluates the snapshot.",
        ],
    }
