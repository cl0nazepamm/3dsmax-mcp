"""External .max file access tools — inspect, merge, and batch scan."""

import json
import fnmatch
import os
from pathlib import Path
from typing import Optional
from ..server import mcp, client


@mcp.tool()
def inspect_max_file(
    file_path: str,
    list_objects: bool = False,
    list_classes: bool = False,
) -> str:
    """Inspect an external .max file without opening it.

    Reads OLE metadata (file size, dates, author, title, comments) directly
    from the file's structured storage — no scene load required.

    With list_classes=True, reads the binary ClassDirectory3 stream to show
    every material, modifier, geometry, texture, and controller class used
    in the file. This is pure OLE reading — no scene load, no main thread.

    With list_objects=True, lists all object names (uses merge-list API,
    slightly slower).

    Args:
        file_path: Full path to the .max file.
        list_objects: If True, also list all object names in the file.
        list_classes: If True, read the class directory to show all material,
                      modifier, geometry, and texture classes used in the file.

    Returns:
        JSON with file metadata and optionally object names and class inventory.
    """
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
    object_names: Optional[list[str]] = None,
    select_merged: bool = True,
    duplicate_action: str = "rename",
) -> str:
    """Merge objects from an external .max file into the current scene.

    Supports selective merging (specific objects by name) or full merge.
    Uses the SDK's MergeFromFile with configurable duplicate handling.

    Args:
        file_path: Full path to the .max file to merge from.
        object_names: Optional list of specific object names to merge.
                      If empty/None, merges all objects.
        select_merged: If True, select the merged objects after import.
        duplicate_action: How to handle duplicate names:
            - "rename": Auto-rename merged objects (default)
            - "skip": Don't merge objects with existing names
            - "merge": Keep both old and new
            - "delete_old": Replace existing objects

    Returns:
        JSON with list of merged object names and count.
    """
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
    file_paths: list[str],
    list_objects: bool = False,
) -> str:
    """Read metadata from multiple .max files in a single call.

    Metadata-only mode runs in parallel threads for maximum speed.
    With list_objects=True, object listing runs sequentially on the main thread.

    Args:
        file_paths: List of full paths to .max files.
        list_objects: If True, also list object names from each file.

    Returns:
        JSON array with metadata for each file.
    """
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
    """Search .max files in a folder for objects matching a name pattern.

    Scans every .max file in the folder, lists all objects, and filters
    by the given wildcard pattern. Designed to handle entire drives —
    files are scanned in batches and output is token-optimized.

    When pattern is "*", returns a compact summary per file (count only).
    When pattern is specific, returns matching object names (capped per file).
    Use inspect_max_file on a specific file to get its full object list.

    Args:
        folder: Folder path to scan for .max files.
        pattern: Wildcard pattern to match object names (e.g. "Fridge*",
                 "*Light*", "CC_Base_*"). Default "*" returns summary only.
        recursive: If True, scan subfolders too. Default True.
        max_matches_per_file: Cap matched names per file (0 = auto: 20 for
                              specific patterns, 0 for summary mode).
        max_files: Stop after this many files (0 = no limit).

    Returns:
        JSON with matching objects grouped by file. Compact output by default.
    """
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
