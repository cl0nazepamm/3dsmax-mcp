"""Parameters by name for anything a node carries, and for classes before they exist."""
from __future__ import annotations

import json as _json
from typing import Any

from ...server import mcp, client
from ...core.coerce import DictValue, StrList

_ACTIONS = ("describe", "get", "set")


@mcp.tool()
def parameters(
    action: str = "describe",
    name: str = "",
    handle: int = 0,
    path: str = "object",
    values: DictValue | None = None,
    names: StrList | None = None,
    class_name: str = "",
    kind: str = "",
    query: str = "",
    offset: int = 0,
    limit: int = 400,
) -> dict[str, Any]:
    """Reflect on and edit the parameters of any object, modifier, material, map or controller.

    One model for every plugin: describe what an owner has, then set it by name.
    The same parameters the create/add_modifier/assign_material `params` dicts take.

    Owner: a node (`name` or `handle`) and a `path`, written as MAXScript reaches it:
    - `object` (default) - the base object, below the modifiers
    - `modifiers[Bend]` - by name, then class; `modifiers[1]` is the top of the stack
    - `material`, `material[2]` - the material, a sub-material (1-based or by name)
    - `.name` steps into a map/material slot or a subanim:
      `material.base_color_map`, `modifiers[Bend].gizmo`, `transform.position`
    - `transform` - the node's transform controller
    Or `class_name` (+ `kind`: modifier, material, texmap, geometry, shape, light,
    camera, helper, controller, renderer, ...) to describe a class with no instance.

    Actions:
    - describe: every parameter with type, value, default, range, named choices,
      UI label and animatable flag. `query` filters by name or label; follow
      `next_offset` when a page is full.
    - get: `names` -> {name: value}.
    - set: `values` {name: value} -> readback of each. Unknown names fail before
      anything changes, with the owner's parameter list and a suggestion.

    Values (MAXScript units): distances in scene units, angles in degrees,
    percentages 0-100, frames for times, colors [r, g, b(, a)] as 0-1 floats or
    "#rrggbb". Ints with choices take the choice's name ("Z", "Fractal").
    Transform subanims (gizmo, Lathe's axis) take {"position", "rotation" (degrees),
    "scale"}, any part. Map/material slots take null, {"handle": n},
    {"class": "Noise", "name": ..., "params": {...}} built in place (nested as deep
    as needed), or {"params": {...}} to edit what the slot already holds.
    Out-of-range values are set and reported in warnings.
    """
    action = (action or "describe").strip().lower()
    if action not in _ACTIONS:
        raise ValueError(f"action must be one of {', '.join(_ACTIONS)}")
    payload: dict[str, Any] = {"action": action}
    if class_name:
        if action != "describe":
            raise ValueError("class_name describes a class; get and set need name or handle and a path")
        payload.update(class_name=class_name, kind=kind)
    else:
        if not name and not handle:
            raise ValueError("Pass name or handle (and a path), or class_name to describe a class")
        if handle:
            payload["handle"] = handle
        if name:
            payload["name"] = name
        payload["path"] = path or "object"
    if action == "set":
        if not values:
            raise ValueError("set needs values: {name: value, ...}")
        payload["values"] = values
    if action == "get":
        if not names:
            raise ValueError("get needs names: [parameter names]")
        payload["names"] = list(names)
    if action == "describe":
        payload.update(query=query, offset=max(0, offset), limit=max(1, min(limit, 2000)))
    if not client.native_available:
        raise RuntimeError("parameters needs the native 3ds Max bridge.")
    response = client.send_command(_json.dumps(payload, allow_nan=False), cmd_type="native:parameters")
    result = response.get("result", {})
    if isinstance(result, str):
        result = _json.loads(result)
    if not isinstance(result, dict):
        raise RuntimeError("parameters returned no structured readback; describe the owner before retrying.")
    return result
