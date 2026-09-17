"""Pulze Scene Manager setups: inspect, pre-flight and guarded edits."""

from __future__ import annotations

from typing import Any

from ..server import mcp, client
from ..helpers import pulze as impl


def _match(setups: list[dict[str, Any]], selector: str | int) -> int:
    """Resolve a setup by index, id, or name (exact first, then case-insensitive)."""
    if isinstance(selector, int) or (isinstance(selector, str) and selector.isdigit()):
        index = int(selector)
        if not 0 <= index < len(setups):
            raise ValueError(f"Setup index {index} is out of range (0..{len(setups) - 1}).")
        return index
    wanted = str(selector)
    for i, setup in enumerate(setups):
        if setup.get("id") == wanted:
            return i
    names = [impl.setup_name(s, i) for i, s in enumerate(setups)]
    if wanted in names:
        return names.index(wanted)
    folded = [n.casefold() for n in names]
    if wanted.casefold() in folded:
        return folded.index(wanted.casefold())
    raise ValueError(f"No setup called '{wanted}'. Available: {', '.join(names) or '(none)'}")


@mcp.tool()
def pulze_inspect(detail: str = "summary") -> dict:
    """Read every Pulze Scene Manager setup in the open scene.

    Use when: you need what each setup renders — camera, resolution, frame range,
    output path, and which layer/element/object overrides it carries.
    Not when: you only want the current Max render settings — use query_scene.
    Reads a scene custom attribute, so no Scene Manager window has to be open and
    nothing in the scene is changed. detail=full returns each setup's raw JSON.
    """
    if detail not in {"summary", "full"}:
        raise ValueError("detail must be summary or full.")
    setups, token = impl.read_setups(client)
    rows = [impl.summarize(setup, index) for index, setup in enumerate(setups)]
    payload: dict[str, Any] = {"token": token, "count": len(rows), "setups": rows}
    if detail == "full":
        payload["raw"] = setups
    return payload


@mcp.tool()
def pulze_preflight(include_disabled: bool = False) -> dict:
    """Check Pulze setups for the faults that waste an overnight render.

    Use when: before starting or submitting a batch — catches a camera deleted
    from the scene, a missing or extension-less output path, an output folder
    that no longer exists, two setups writing to the same file, a test-size
    resolution, and an unintended frame range.
    Not when: you want to fix them — that is pulze_set. This only reports.
    Checks render-enabled setups; include_disabled=true checks all of them.
    """
    return impl.preflight(client, include_disabled)


@mcp.tool()
def pulze_set(edits: list[dict[str, Any]], expected_token: str, confirm: bool = False) -> dict:
    """Edit existing Pulze setups, all of them in one guarded write.

    Use when: pulze_preflight found something to fix, or a batch needs a new
    resolution, camera, output path or render flag.
    Not when: you want a new setup — creating setups is not supported yet.
    Each edit is {setup: index|id|name, resolution: {width, height}, camera: name,
    output_path: str, render_enabled: bool}. A camera must exist in the scene —
    Scene Manager resolves it by node handle, which is looked up here. expected_token comes from
    pulze_inspect and is refused if anything changed since; confirm must be true.
    Returns backup_id — pass it to pulze_restore to roll the whole edit back.
    """
    if not edits or len(edits) > 64:
        raise ValueError("Supply 1..64 edits.")
    if not confirm:
        raise ValueError("This rewrites Scene Manager data in the open scene. Call again with confirm=true.")

    setups, token = impl.read_setups(client)
    if token != expected_token:
        raise ValueError("Setups changed since pulze_inspect. Inspect again and retry with the new token.")

    applied: list[dict[str, Any]] = []
    for edit in edits:
        if "setup" not in edit:
            raise ValueError("Each edit needs a 'setup' selector (index, id or name).")
        index = _match(setups, edit["setup"])
        setup = setups[index]
        changes: list[str] = []

        resolution = edit.get("resolution")
        if resolution is not None:
            width, height = resolution.get("width"), resolution.get("height")
            if width is None or height is None:
                raise ValueError("resolution needs both width and height.")
            block = setup.setdefault("resolution", {})
            block["width"], block["height"] = str(int(width)), str(int(height))
            block["ratio"] = str(round(int(width) / int(height), 6))
            block["enabled"] = True
            changes.append(f"resolution={width}x{height}")

        camera = edit.get("camera")
        if camera is not None:
            handle = impl.resolve_camera(client, str(camera))
            block = setup.setdefault("camera", {})
            block["name"], block["enabled"] = str(camera), True
            block["handle"] = handle
            changes.append(f"camera={camera} (handle {handle})")

        output_path = edit.get("output_path")
        if output_path is not None:
            block = setup.setdefault("output_Common", {})
            block["path"], block["enabled"] = str(output_path), True
            changes.append("output_path")

        render_enabled = edit.get("render_enabled")
        if render_enabled is not None:
            setup.setdefault("render", {})["enabled"] = bool(render_enabled)
            changes.append(f"render_enabled={bool(render_enabled)}")

        if not changes:
            raise ValueError(f"Edit for '{edit['setup']}' changes nothing.")
        applied.append({"setup": impl.setup_name(setup, index), "index": index, "changes": changes})

    result = impl.write_setups(client, setups, expected_token)
    result["applied"] = applied
    result["reopen_note"] = "Close and reopen the Scene Manager window to see the new values."
    return result


@mcp.tool()
def pulze_restore(backup_id: str) -> dict:
    """Roll Pulze setups back to the state before a pulze_set in this session.

    Use when: an edit went wrong and the setups must go back exactly as they were.
    Not when: you simply want different values — edit forward with pulze_set.
    backup_id is what pulze_set returned. Backups live in the running MCP server,
    so they are gone after a server restart.
    """
    if not backup_id.strip():
        raise ValueError("backup_id is empty.")
    return impl.restore_backup(client, backup_id.strip())
