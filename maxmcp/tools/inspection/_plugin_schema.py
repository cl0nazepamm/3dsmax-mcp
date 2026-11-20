"""Shared native schema access. No scene mutation fallbacks or constructor probes."""
from __future__ import annotations

import json
from typing import Any
from ._plugin_semantics import annotate


class PluginGuardError(ValueError):
    """Actionable preflight refusal, preserved by the public tool envelope."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.retryable = False
        super().__init__(f"{code}: {message}")


def native(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    from ...server import client
    if not client.native_available:
        raise RuntimeError("This operation requires the updated native 3ds Max bridge.")
    response = client.send_command(json.dumps(payload, allow_nan=False), cmd_type=command)
    result = response.get("result", {})
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        raise RuntimeError("Native operation returned no structured readback; inspect before retrying.")
    return result


def inspect(*, class_name: str = "", class_ref: dict | None = None,
            owner_ref: dict | None = None, query: str = "", fields: list | None = None,
            limit: int = 25, offset: int = 0) -> dict:
    payload = dict(query=query, fields=fields or [], limit=limit, offset=offset)
    if owner_ref is not None:
        payload["owner_ref"] = owner_ref
    elif class_ref is not None:
        payload["class_ref"] = class_ref
    elif class_name:
        payload["class_name"] = class_name
    else:
        raise ValueError("Supply class_name, class_ref or owner_ref.")
    return annotate(native("native:plugin_inspect", payload))


def all_properties(**target) -> dict:
    result = inspect(**target, limit=256)
    properties = list(result["properties"])
    while result.get("next_offset") is not None:
        page = inspect(**target, limit=256, offset=result["next_offset"])
        if page["schema_token"] != result["schema_token"]:
            raise RuntimeError("Schema changed during inspection; retry discovery.")
        properties.extend(page["properties"])
        result = {**result, "next_offset": page["next_offset"]}
    result["properties"] = properties
    return result
