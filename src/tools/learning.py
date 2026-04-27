"""Deep SDK learning tools — reference graphs, class relationships, scene patterns, live events."""

import json
from ..server import mcp, client


@mcp.tool()
def walk_references(
    name: str,
    max_depth: int = 4,
) -> str:
    """Walk the full reference dependency graph of a scene object."""
    payload = json.dumps({"name": name, "max_depth": max_depth})
    response = client.send_command(payload, cmd_type="native:walk_references")
    return response.get("result", "{}")


@mcp.tool()
def map_class_relationships(
    pattern: str = "",
    superclass: str = "",
    limit: int = 100,
) -> str:
    """Map which classes can reference which types via their ParamBlock2 params."""
    payload = {}
    if pattern:
        payload["pattern"] = pattern
    if superclass:
        payload["superclass"] = superclass
    if limit != 100:
        payload["limit"] = limit
    response = client.send_command(
        json.dumps(payload) if payload else "",
        cmd_type="native:map_class_relationships",
    )
    return response.get("result", "{}")


@mcp.tool()
def learn_scene_patterns() -> str:
    """Analyze the current scene to learn real-world class usage patterns."""
    response = client.send_command("", cmd_type="native:learn_scene_patterns")
    return response.get("result", "{}")


@mcp.tool()
def watch_scene(
    action: str = "status",
    since: int = 0,
    limit: int = 100,
) -> str:
    """Live scene event watcher — track what happens in 3ds Max in real-time."""
    payload = {"action": action}
    if action == "get":
        payload["since"] = since
        payload["limit"] = limit
    response = client.send_command(json.dumps(payload), cmd_type="native:watch_scene")
    return response.get("result", "{}")
