"""External .max file access tools — inspect, merge, and batch scan."""

import json
import fnmatch
import os
from pathlib import Path
from typing import Optional
from ..server import mcp, client
from ..coerce import StrList


@mcp.tool()
def inspect_max_file(
    file_path: str,
    list_objects: bool = False,
    list_classes: bool = False,
) -> str:
    """Inspect an external .max file without opening it."""
    payload = json.dumps({
        "file_path": file_path,
        "list_objects": list_objects,
        "list_classes": list_classes,
    })
    response = client.send_command(payload, cmd_type="native:inspect_max_file")
    return response.get("result", "")


@mcp.tool()
def merge_from_file(
    file_path: str,
    object_names: Optional[StrList] = None,
    select_merged: bool = True,
    duplicate_action: str = "rename",
) -> str:
    """Merge objects from an external .max file into the current scene."""
    payload = {
        "file_path": file_path,
        "select_merged": select_merged,
        "duplicate_action": duplicate_action,
    }
    if object_names:
        payload["object_names"] = object_names
    response = client.send_command(json.dumps(payload), cmd_type="native:merge_from_file")
    return response.get("result", "")


@mcp.tool()
def batch_file_info(
    file_paths: StrList,
    list_objects: bool = False,
) -> str:
    """Read metadata from multiple .max files in a single call."""
    payload = json.dumps({
        "file_paths": file_paths,
        "list_objects": list_objects,
    })
    response = client.send_command(payload, cmd_type="native:batch_file_info")
    return response.get("result", "")


# ── Batch size for native calls (avoids pipe buffer issues on huge scans)
_BATCH_SIZE = 50


def _scan_files_in_batches(max_files: list[str]) -> list[dict]:
    """Send files to native:batch_file_info in chunks, return merged results."""
    all_file_infos: list[dict] = []
    for i in range(0, len(max_files), _BATCH_SIZE):
        batch = max_files[i : i + _BATCH_SIZE]
        payload = json.dumps({"file_paths": batch, "list_objects": True})
        response = client.send_command(payload, cmd_type="native:batch_file_info")
        raw = response.get("result", "")
        try:
            data = json.loads(raw)
        except Exception:
            continue
        all_file_infos.extend(data.get("files", []))
    return all_file_infos


def _compact_path(file_path: str, folder: str) -> str:
    """Return path relative to scan root for compact output."""
    try:
        return os.path.relpath(file_path, folder)
    except ValueError:
        return os.path.basename(file_path)


@mcp.tool()
def search_max_files(
    folder: str,
    pattern: str = "*",
    recursive: bool = True,
    max_matches_per_file: int = 0,
    max_files: int = 0,
) -> str:
    """Search .max files in a folder for objects matching a name pattern."""
    p = Path(folder)
    if not p.is_dir():
        return json.dumps({"error": f"Folder not found: {folder}"})

    glob_pattern = "**/*.max" if recursive else "*.max"
    file_list = sorted(str(f) for f in p.glob(glob_pattern) if f.is_file())

    if not file_list:
        return json.dumps({"error": f"No .max files found in: {folder}"})

    if max_files > 0:
        file_list = file_list[:max_files]

    # Fix common AI mistake: pattern like "*.max" or "*.MAX" is meant to find
    # files, not filter object names. Treat file-extension patterns as "*".
    cleaned = pattern.strip()
    if cleaned.lower() in ("*.max", "*.3ds", "*.fbx", "*.obj", "*.max;*.max",
                            "**/*.max", "*.MAX", "**\\*.max"):
        cleaned = "*"

    summary_mode = cleaned == "*"
    pattern = cleaned
    cap = max_matches_per_file if max_matches_per_file > 0 else (0 if summary_mode else 20)

    all_file_infos = _scan_files_in_batches(file_list)

    results = []
    total_matches = 0
    pat_lower = pattern.lower()

    for file_info in all_file_infos:
        objects = file_info.get("objects", [])
        if summary_mode:
            matched_count = len(objects)
        else:
            matched_count = sum(1 for name in objects if fnmatch.fnmatch(name.lower(), pat_lower))

        if matched_count == 0:
            continue

        rel = _compact_path(file_info.get("filePath", ""), folder)

        if summary_mode:
            # Ultra-compact: just file + count
            results.append({"file": rel, "objects": matched_count})
        else:
            # Filtered mode: return matched names, capped
            matched = [name for name in objects if fnmatch.fnmatch(name.lower(), pat_lower)]
            entry: dict = {"file": rel, "matched": len(matched)}
            if cap > 0 and len(matched) > cap:
                entry["names"] = matched[:cap]
                entry["more"] = len(matched) - cap
            else:
                entry["names"] = matched
            results.append(entry)

        total_matches += matched_count

    return json.dumps({
        "folder": folder,
        "pattern": pattern,
        "scanned": len(file_list),
        "found": len(results),
        "totalObjects": total_matches,
        "results": results,
    })
