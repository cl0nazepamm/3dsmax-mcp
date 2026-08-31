"""Native, deterministic scene-graph QA with narrowly safe repairs."""

from __future__ import annotations

import json
from typing import Optional

from ..coerce import DictList, IntList, StrList
from ..server import client, mcp


@mcp.tool()
def scene_qa(
    action: str = "scan",
    checks: Optional[StrList] = None,
    fixes: Optional[StrList] = None,
    scope: str = "scene",
    names: Optional[StrList] = None,
    handles: Optional[IntList] = None,
    refs: Optional[DictList] = None,
    expected_scene_seq: Optional[int] = None,
    dry_run: bool = False,
    max_issues: int = 1000,
    transform_epsilon: float = 1.0e-6,
    far_origin_threshold: Optional[float] = None,
) -> str:
    """Scan or repair non-mesh scene hygiene using the native SDK.

    Checks are limited to names, transforms, hierarchy/group metadata, frame rate,
    and animation range. No mesh, UV, normals, topology, skinning, or visual-quality
    judgement is performed. ``action=fix`` only applies explicitly deterministic
    repairs (currently ``name_collisions`` and ``empty_names``), inside one undo step.
    Pass ``expected_scene_seq`` to reject an apply against persistently changed
    scene state; selection-only interaction does not invalidate the token.
    """
    normalized_action = action.strip().lower()
    if normalized_action not in {"scan", "fix"}:
        raise ValueError("action must be scan or fix")
    if scope not in {"scene", "selection", "targets"}:
        raise ValueError("scope must be scene, selection, or targets")
    if max_issues < 1 or max_issues > 100_000:
        raise ValueError("max_issues must be between 1 and 100000")
    if transform_epsilon <= 0:
        raise ValueError("transform_epsilon must be greater than zero")
    if far_origin_threshold is not None and far_origin_threshold <= 0:
        raise ValueError("far_origin_threshold must be greater than zero")
    if expected_scene_seq is not None and expected_scene_seq < 0:
        raise ValueError("expected_scene_seq must be non-negative")
    if handles and any(handle <= 0 for handle in handles):
        raise ValueError("handles must contain positive integers")
    if scope == "targets" and not names and not handles and not refs:
        raise ValueError("scope=targets requires refs, names, or handles")
    if not client.native_available:
        return "Native bridge is required for scene_qa."

    payload: dict = {
        "action": normalized_action,
        "scope": scope,
        "dry_run": dry_run,
        "max_issues": max_issues,
        "transform_epsilon": transform_epsilon,
    }
    if checks:
        payload["checks"] = list(checks)
    if fixes:
        payload["fixes"] = list(fixes)
    if names:
        payload["names"] = list(names)
    if handles:
        payload["handles"] = list(handles)
    if refs:
        payload["refs"] = list(refs)
    if expected_scene_seq is not None:
        payload["expected_scene_seq"] = expected_scene_seq
    if far_origin_threshold is not None:
        payload["far_origin_threshold"] = far_origin_threshold

    # A dry-run repair is read-only and deliberately uses the scan route so it
    # never opens an empty undo record or trips safe-mode mutation gating.
    cmd_type = (
        "native:scene_qa_fix"
        if normalized_action == "fix" and not dry_run
        else "native:scene_qa_scan"
    )
    response = client.send_command(
        json.dumps(payload),
        cmd_type=cmd_type,
        timeout=30.0,
    )
    return response.get("result", "")
