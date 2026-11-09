"""Resolve material texture sources from the complete connection graph."""
from __future__ import annotations

from ...server import mcp, client
from . import _material_roles as impl


@mcp.tool()
def material_roles(names: list[str] | None = None, scan_scene: bool = False,
                   limit: int = 25, only_problems: bool = False, offset: int = 0,
                   depth: int = 8, max_nodes: int = 400) -> dict:
    """Read the files connected to each material slot, including wrappers and submaterials.

    Supply unique object/material names OR scan_scene=true for assigned scene
    materials. Distinct slots, shared maps, composite inputs and file tiles stay
    separate. Each row carries the submaterial and source path; procedural maps
    can have no file. The operation is read-only.

    Filename mismatches are advisory: inspect channel selection, conversions and
    renderer modes before changing anything. Mask inputs are identified separately.
    only_problems retains missing files, mismatches, warnings and incomplete reads.
    Follow next_offset for more results; pages are stable while the scene is unchanged.
    complete=false means there are unread pages, failed targets, a truncated graph or
    an image map whose path could not be read (FILE_PATH_UNREADABLE warning).
    Increase depth/max_nodes for truncated graphs. Use inspect_material_network for
    parameter values. Requires a native bridge supporting graphVersion 2 connections.
    """
    if not isinstance(scan_scene, bool) or not isinstance(only_problems, bool):
        raise ValueError("scan_scene and only_problems must be booleans")
    for key, value, low, high in (("limit", limit, 1, 200), ("offset", offset, 0, 2147483647),
                                   ("depth", depth, 1, 16), ("max_nodes", max_nodes, 1, 2000)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} must be an integer from {low} to {high}")
    if names is not None and (not isinstance(names, list) or len(names) > 200 or
                             any(not isinstance(n, str) or not n.strip() for n in names)):
        raise ValueError("names must be a list of up to 200 nonempty object/material names")
    if scan_scene == bool(names):
        raise ValueError("Supply names=[...] or scan_scene=true, exclusively")
    # Preserve significant whitespace in Max names; repeat targets only once.
    wanted = list(dict.fromkeys(names or []))
    batch = impl.fetch_graphs(client, names=wanted, scan_scene=scan_scene, limit=limit,
                             offset=offset, depth=depth, max_nodes=max_nodes)
    audited = [impl.roles_from_payload(graph) for graph in batch["graphs"]]
    results = [r for r in audited if not only_problems or r["mismatches"] or r["missing_files"]
               or r["issues"] or r["warnings"] or not r["complete"]]
    return {
        "materials": results, "checked": batch["checked"], "returned": len(results),
        "total": batch["total"], "offset": batch["offset"], "next_offset": batch["next_offset"],
        "complete": batch["next_offset"] is None and not batch["failed"] and all(r["complete"] for r in audited),
        "with_mismatch": sum(bool(r["mismatches"]) for r in audited),
        "with_missing_files": sum(bool(r["missing_files"]) for r in audited),
        "failed": batch["failed"],
    }
