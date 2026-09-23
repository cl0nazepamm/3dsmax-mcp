"""Pulze Scene Manager setups: inspect, pre-flight and guarded edits."""

from __future__ import annotations

from typing import Any

from ..server import mcp, client
from ..helpers import pulze as impl


@mcp.tool()
def pulze_inspect(detail: str = "summary") -> dict:
    """Read every Pulze Scene Manager setup in the open scene.

    Use when: you need what each setup renders: camera, resolution, frame range,
    output path, and which layer/element/object overrides it carries.
    Not when: you only want the current Max render settings (use query_scene).
    Reads a scene custom attribute; nothing in the scene changes. Also reports
    window_open: while the Scene Manager window is open, edits are refused.
    detail=full returns each setup's raw JSON.
    """
    if detail not in {"summary", "full"}:
        raise ValueError("detail must be summary or full.")
    setups, state = impl.read_setups(client)
    rows = [impl.summarize(setup, index) for index, setup in enumerate(setups)]
    payload: dict[str, Any] = {"token": state["token"], "window_open": state["window_open"],
                               "count": len(rows), "setups": rows}
    if detail == "full":
        payload["raw"] = setups
    return payload


@mcp.tool()
def pulze_preflight(include_disabled: bool = False) -> dict:
    """Check Pulze setups for the faults that waste an overnight render.

    Use when: before starting or submitting a batch. Catches a camera whose node
    handle no longer resolves (even if a camera with the same name exists), a
    renamed camera, a missing or extension-less output path, an output folder
    that does not exist, two setups writing to the same file, an invalid or
    test-size resolution, and an unintended frame range.
    Not when: you want to fix them (that is pulze_set). This only reports.
    Checks render-enabled setups; include_disabled=true checks all of them.
    """
    return impl.preflight(client, include_disabled)


@mcp.tool()
def pulze_set(edits: list[dict[str, Any]], expected_token: str, confirm: bool = False) -> dict:
    """Edit existing Pulze setups in one guarded, undoable write.

    Each edit selects exactly one setup with index (int), id or name, plus any of
    resolution {width, height} (whole pixels 1..32768), camera (unique scene
    name; stored by node handle), output_path, render_enabled. Ambiguous names
    or ids are refused. expected_token comes from pulze_inspect or
    pulze_preflight; confirm must be true.

    Refused, with nothing changed, when the Scene Manager window is open (it
    would overwrite the edit when it closes), when another undo operation is in
    progress, when the scene or 3ds Max process changed, or when the setups
    changed since the token was read. The write is one undo step, is read back,
    and is rolled back if the read-back fails. Returns backup_id for pulze_restore.
    Creating new setups is not supported.
    """
    if not isinstance(edits, list) or not 1 <= len(edits) <= 64:
        raise ValueError("Supply 1..64 edits.")
    if not confirm:
        raise ValueError("This rewrites Scene Manager data in the open scene. Call again with confirm=true.")

    setups, state = impl.read_setups(client)
    if state["token"] != expected_token:
        raise ValueError("Setups changed since they were read. Inspect again and retry with the new token.")
    if state["window_open"]:
        raise impl.PulzeRefused("window_open: Close the Scene Manager window first; it owns the setups while open. "
                                "Nothing was changed.")

    applied: list[dict[str, Any]] = []
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("Each edit must be an object.")
        unknown = set(edit) - {"index", "id", "name", "resolution", "camera", "output_path", "render_enabled"}
        if unknown:
            raise ValueError(f"Unknown edit keys: {sorted(unknown)}")
        index = impl.select_setup(setups, edit)
        setup = setups[index]
        changes: list[str] = []

        resolution = edit.get("resolution")
        if resolution is not None:
            if not isinstance(resolution, dict):
                raise ValueError("resolution must be {width, height}.")
            width = impl.positive_int(resolution.get("width"), "resolution.width")
            height = impl.positive_int(resolution.get("height"), "resolution.height")
            block = setup.setdefault("resolution", {})
            block["width"], block["height"] = str(width), str(height)
            block["ratio"] = str(round(width / height, 6))
            block["enabled"] = True
            changes.append(f"resolution={width}x{height}")

        camera = edit.get("camera")
        if camera is not None:
            if not isinstance(camera, str) or not camera:
                raise ValueError("camera must be a scene camera name.")
            handle = impl.resolve_camera(client, camera)
            block = setup.setdefault("camera", {})
            block["name"], block["handle"], block["enabled"] = camera, handle, True
            changes.append(f"camera={camera} (handle {handle})")

        output_path = edit.get("output_path")
        if output_path is not None:
            if not isinstance(output_path, str) or not output_path.strip():
                raise ValueError("output_path must be a non-empty path.")
            block = setup.setdefault("output_Common", {})
            block["path"], block["enabled"] = output_path, True
            changes.append("output_path")

        render_enabled = edit.get("render_enabled")
        if render_enabled is not None:
            if not isinstance(render_enabled, bool):
                raise ValueError("render_enabled must be true or false.")
            setup.setdefault("render", {})["enabled"] = render_enabled
            changes.append(f"render_enabled={render_enabled}")

        if not changes:
            raise ValueError(f"Edit for setup {index} changes nothing.")
        applied.append({"setup": impl.setup_name(setup, index), "index": index, "changes": changes})

    result = impl.write_setups(client, state, setups)
    result["applied"] = applied
    return result


@mcp.tool()
def pulze_restore(backup_id: str) -> dict:
    """Put Pulze setups back exactly as they were before a pulze_set.

    backup_id is what pulze_set returned. Backups are files bound to the 3ds Max
    process and scene they came from. Refused, with nothing changed, on another
    scene or process, when the setups changed after that edit (restoring would
    overwrite newer work), or while the Scene Manager window is open. The
    restore is one undo step and is verified by read-back before reporting
    restored=true. Ctrl+Z in 3ds Max also reverts a pulze_set.
    """
    if not isinstance(backup_id, str) or not backup_id.strip():
        raise ValueError("backup_id is empty.")
    return impl.restore_backup(client, backup_id.strip())
