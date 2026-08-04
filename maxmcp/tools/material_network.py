"""Native material-network inspection and replication tools.

These tools are the renderer-neutral material workflow for agents:
inspect the live graph once, preview a clone/remap plan, then apply only the
requested assignment/path edits through the native bridge.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..coerce import StrList
from ..server import client, mcp

_REMAP_MODES = frozenset({"clone_and_remap", "remap_in_place", "preset_cc_octane_skin"})
_INSPECT_SCOPES = frozenset({"wired", "files_only", "all_slots"})


def _native_unavailable(tool: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": (
                f"{tool} requires the native C++ bridge. "
                "Use get_material_slots for flat fallback inspection."
            ),
        },
        separators=(",", ":"),
    )


def _json_result(response: dict[str, Any], fallback: str = "{}") -> str:
    raw = response.get("result", fallback)
    if not isinstance(raw, str):
        return json.dumps(raw, separators=(",", ":"))
    try:
        payload = json.loads(raw)
    except Exception:
        return raw
    if isinstance(payload, dict):
        return json.dumps(payload, separators=(",", ":"))
    return raw


def _normalize_texture_folder(path: str) -> str:
    cleaned = (path or "").strip().replace("/", "\\")
    return cleaned.rstrip("\\")


def _validate_replicate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    mode = str(payload.get("mode", "clone_and_remap")).lower()
    if mode in _REMAP_MODES:
        folder = str(payload.get("texture_folder", "")).strip()
        path_map = payload.get("path_map") or {}
        if not folder and not path_map:
            errors.append("texture_folder or path_map is required for remap modes")
        elif folder and not os.path.isdir(folder):
            errors.append(f"texture_folder does not exist: {folder}")
    if payload.get("assign") and not payload.get("targets"):
        errors.append("target is required when assign=true")
    return errors


def _compact_inspect_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok", True):
        return payload
    compact = {
        "ok": payload.get("ok", True),
        "query": payload.get("query"),
        "resolvedVia": payload.get("resolvedVia"),
        "owner": payload.get("owner"),
        "root": payload.get("root"),
        "hints": payload.get("hints"),
        "wiredSlots": payload.get("wiredSlots", []),
        "issues": payload.get("issues", []),
        "warnings": payload.get("warnings", []),
        "truncated": payload.get("truncated"),
    }
    manifest = payload.get("fileManifest")
    if isinstance(manifest, list):
        compact["fileManifest"] = manifest
    else:
        compact["fileManifest"] = [
            {
                "nodeId": node.get("id"),
                "nodeClass": node.get("class"),
                "param": file.get("param"),
                "path": file.get("path"),
                "exists": file.get("exists"),
                "bytes": file.get("bytes"),
                "udim": file.get("udim"),
            }
            for node in payload.get("nodes", [])
            if isinstance(node, dict) and node.get("files")
            for file in node["files"]
            if isinstance(file, dict)
        ]
    return compact


@mcp.tool()
def inspect_material_network(
    name: str,
    sub_material_index: int = 0,
    depth: int = 3,
    scope: str = "wired",
    include_values: bool = True,
    verify_files: bool = True,
    max_nodes: int = 80,
    profile: str = "auto",
    compact: bool = False,
) -> str:
    """Inspect a material graph: wired slots, nested maps, file manifest, and health issues."""
    if not client.native_available:
        return _native_unavailable("inspect_material_network")

    normalized_scope = (scope or "wired").strip().lower()
    if normalized_scope not in _INSPECT_SCOPES:
        normalized_scope = "wired"

    payload = {
        "name": name,
        "sub_material_index": max(0, int(sub_material_index)),
        "depth": min(6, max(0, int(depth))),
        "scope": normalized_scope,
        "include_values": bool(include_values),
        "verify_files": bool(verify_files),
        "max_nodes": max(1, int(max_nodes)),
        "profile": (profile or "auto").strip().lower(),
    }
    response = client.send_command(
        json.dumps(payload, separators=(",", ":")),
        cmd_type="native:inspect_material_network",
    )
    raw = _json_result(response)
    if not compact:
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw
    if isinstance(parsed, dict):
        return json.dumps(_compact_inspect_payload(parsed), separators=(",", ":"))
    return raw


@mcp.tool()
def replicate_material(
    source: str,
    target: StrList,
    source_sub_material_index: int = 0,
    mode: str = "clone_and_remap",
    texture_folder: str = "",
    path_map: dict[str, str] | None = None,
    preset: str = "auto",
    material_name: str = "",
    assign: bool = True,
    instance_maps: bool = False,
    preview: bool = True,
    verify: bool = True,
    allow_missing: bool = False,
    confirm: bool = False,
    extend_udim_tiles: bool = True,
    max_nodes: int = 160,
) -> str:
    """Preview or apply a structure-preserving material clone/remap through native C++."""
    if not client.native_available:
        return _native_unavailable("replicate_material")

    normalized_mode = (mode or "clone_and_remap").strip().lower()
    if normalized_mode == "preview":
        preview = True
    targets = [target] if isinstance(target, str) else list(target or [])

    payload = {
        "source": source,
        "target": targets,
        "targets": targets,
        "source_sub_material_index": max(0, int(source_sub_material_index)),
        "mode": normalized_mode,
        "texture_folder": _normalize_texture_folder(texture_folder),
        "path_map": path_map or {},
        "preset": (preset or "auto").strip().lower(),
        "material_name": material_name or "",
        "assign": bool(assign),
        "instance_maps": bool(instance_maps),
        "preview": bool(preview),
        "verify": bool(verify),
        "include_values": False,
        "allow_missing": bool(allow_missing),
        "confirm": bool(confirm),
        "extend_udim_tiles": bool(extend_udim_tiles),
        "max_nodes": max(1, int(max_nodes)),
    }

    validation_errors = _validate_replicate_payload(payload)
    if validation_errors and not preview:
        return json.dumps(
            {
                "ok": False,
                "preview": False,
                "status": "blocked",
                "errors": validation_errors,
            },
            separators=(",", ":"),
        )

    response = client.send_command(
        json.dumps(payload, separators=(",", ":")),
        cmd_type="native:replicate_material",
    )
    return _json_result(response)
