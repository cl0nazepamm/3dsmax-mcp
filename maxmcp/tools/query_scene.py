"""Unified scene query tool — one MCP entry for overview, filter, class, property, selection, delta."""

from __future__ import annotations

from ..server import mcp, client
from ._query_scene_core import dispatch_query_scene


@mcp.tool()
def query_scene(
    action: str,
    class_name: str = "",
    pattern: str = "",
    layer: str = "",
    limit: int = 100,
    offset: int = 0,
    roots_only: bool = False,
    property_name: str = "",
    property_value: str = "",
    class_filter: str = "",
    superclass: str = "",
    scope: str = "auto",
    detail: str = "full",
    max_roots: int = 50,
    max_items: int = 50,
    capture: bool = False,
    unchanged_since: int = 0,
) -> str:
    """Unified scene query. action: overview | filter | class | property | selection | delta.

    Use when: reading scene state, finding nodes, checking selection, or verifying edits (delta).
    Not when: deep single-object dumps (inspect_object), bridge diagnosis (get_bridge_status),
    or bundling bridge+capabilities+scene (get_session_context — on demand only).

    overview — counts, materials, modifiers, layers, roots (max_roots)
    filter — paginated node list (class_name, pattern, layer, limit, offset, roots_only)
    class — find class instances (class_name, scope=nodes|refs|auto, superclass, limit)
    property — find objects by property (property_name, property_value, class_filter)
    selection — current selection (detail=compact|full, max_items)
    delta — changes since last baseline (capture=true to reset; unchanged_since=N for cheap journal no-op)
    """
    return dispatch_query_scene(
        client,
        action,
        class_name=class_name,
        pattern=pattern,
        layer=layer,
        limit=limit,
        offset=offset,
        roots_only=roots_only,
        property_name=property_name,
        property_value=property_value,
        class_filter=class_filter,
        superclass=superclass,
        scope=scope,
        detail=detail,
        max_roots=max_roots,
        max_items=max_items,
        capture=capture,
        unchanged_since=unchanged_since,
    )
