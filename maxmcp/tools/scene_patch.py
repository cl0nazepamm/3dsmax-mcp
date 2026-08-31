"""Atomic node-reference resolution and mechanical scene patches."""

from __future__ import annotations

import json
from typing import Any

from ..server import client, mcp


def _require_native() -> None:
    if not client.native_available:
        raise RuntimeError(
            "resolve_node_refs and scene_patch require the native 3ds Max bridge."
        )


@mcp.tool()
def resolve_node_refs(refs: list[dict[str, Any]]) -> str:
    """Resolve NodeRefs to canonical identities plus mutation sceneSeq.

    Each ref accepts ``handle``, ``name``, or ``path``. Paths are absolute
    JSON-Pointer hierarchy paths, for example ``/Rig/Camera``; ``~1`` escapes
    a slash inside a node name and ``~0`` escapes a tilde. Supplying multiple
    selectors cross-checks them instead of silently retargeting a stale handle.
    Name-only refs must be globally unique and path-only refs must be unique at
    every hierarchy segment.

    ``sceneSeq`` advances only for persistent scene mutations. Selection and
    sub-object selection remain visible in ``activitySeq`` but do not stale a
    guarded patch.
    """
    if not refs:
        raise ValueError("refs must be a non-empty list of NodeRef objects")
    _require_native()
    response = client.send_command(
        json.dumps({"refs": refs}),
        cmd_type="native:resolve_node_refs",
    )
    return response.get("result", "{}")


@mcp.tool()
def scene_patch(
    operations: list[dict[str, Any]],
    expected_scene_seq: int | None = None,
    dry_run: bool = False,
    label: str = "MCP Scene Patch",
) -> str:
    """Preflight and atomically apply mechanical node edits in one native undo step.

    Every operation has ``target`` (a NodeRef) and one of these ``op`` values:

    - ``rename``: ``name``.
    - ``transform``: relative ``move``/``rotate``/``scale`` arrays and optional
      ``coordinate_system`` (``world`` or ``local``). Rotation is in degrees.
    - ``set_flags``: any of ``hidden``, ``frozen``, ``renderable``,
      ``cast_shadows``, or ``receive_shadows``.
    - ``set_parent``: required ``parent`` NodeRef or null (detach), plus optional
      ``keep_transform``.

    Pass ``expected_scene_seq`` from ``resolve_node_refs`` to reject plans made
    stale by persistent scene edits. Selection and sub-object selection do not
    invalidate this token. ``dry_run`` performs the same resolution and validation
    without opening an undo hold or changing the scene. All refs and operations
    are resolved against the pre-patch scene and validated before the first edit;
    use handles when a patch renames or reparents a target. An apply failure
    cancels the native hold.
    """
    if not operations:
        raise ValueError("operations must be a non-empty list")
    if expected_scene_seq is not None and expected_scene_seq < 0:
        raise ValueError("expected_scene_seq must be non-negative")
    _require_native()

    payload: dict[str, Any] = {
        "operations": operations,
        "dry_run": bool(dry_run),
        "label": label,
    }
    if expected_scene_seq is not None:
        payload["expected_scene_seq"] = expected_scene_seq

    response = client.send_command(
        json.dumps(payload),
        cmd_type="native:scene_patch",
    )
    return response.get("result", "{}")
