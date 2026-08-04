"""Shared scene read helpers — one native walk, many tool shapes."""

from __future__ import annotations

import json
from typing import Any

from ..max_client import MaxClient


def fetch_scene_snapshot(client: MaxClient, max_roots: int = 50) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if max_roots != 50:
        params["max_roots"] = max_roots
    response = client.send_command(
        json.dumps(params) if params else "",
        cmd_type="native:scene_snapshot",
    )
    return json.loads(response.get("result", "{}"))


def scene_info_summary_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Legacy get_scene_info() no-filter shape from a scene_snapshot payload."""
    return {
        "totalObjects": snapshot.get("objectCount", 0),
        "classCounts": snapshot.get("classCounts", {}),
        "layers": snapshot.get("layers", []),
        "hiddenCount": snapshot.get("hiddenCount", 0),
        "frozenCount": snapshot.get("frozenCount", 0),
    }


def fetch_class_instances_native(
    client: MaxClient,
    class_name: str,
    limit: int = 100,
) -> dict[str, Any]:
    response = client.send_command(
        json.dumps({"class_name": class_name, "limit": limit}),
        cmd_type="native:find_class_instances",
    )
    return json.loads(response.get("result", "{}"))
