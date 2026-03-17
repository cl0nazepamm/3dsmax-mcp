"""Scene organization tools — layers, groups, named selection sets."""

import json

from ..server import mcp, client


@mcp.tool()
def manage_layers(
    action: str,
    name: str = "",
    names: list[str] | None = None,
    layer: str = "",
    color: list[int] | None = None,
    hidden: bool | None = None,
    frozen: bool | None = None,
    renderable: bool | None = None,
    parent: str = "",
    rename: str = "",
    boxMode: bool | None = None,
    castShadows: bool | None = None,
    rcvShadows: bool | None = None,
    xRayMtl: bool | None = None,
    backCull: bool | None = None,
    allEdges: bool | None = None,
    vertTicks: bool | None = None,
    trajectory: bool | None = None,
    primaryVisibility: bool | None = None,
    secondaryVisibility: bool | None = None,
) -> str:
    """Manage scene layers — create, delete, list, set properties, move objects.

    All operations run through pure C++ SDK (ILayerManager). Requires native bridge.

    Actions:
        list: List all layers with properties and object counts.
        create: Create a new layer. Params: name, color, hidden, frozen, renderable, parent.
        delete: Delete a layer by name (must be empty and not default).
        set_current: Set the active layer by name.
        set_properties: Set layer properties. Params: name + any of hidden, frozen,
                        renderable, color, rename, boxMode, castShadows, rcvShadows.
        add_objects: Move objects to a layer. Params: layer (target), names (object list).
        select_objects: Select all objects on a layer. Params: name.

    Args:
        action: One of: list, create, delete, set_current, set_properties, add_objects, select_objects.
        name: Layer name (used by most actions).
        names: Object names (for add_objects).
        layer: Target layer name (for add_objects).
        color: RGB color [r, g, b] 0-255.
        hidden: Hide the layer.
        frozen: Freeze the layer.
        renderable: Make layer renderable.
        parent: Parent layer name (for create).
        rename: New name (for set_properties).
        boxMode: Display objects as boxes.
        castShadows: Cast shadows.
        rcvShadows: Receive shadows.
        xRayMtl: Enable X-Ray material display.
        backCull: Enable backface culling.
        allEdges: Show all edges.
        vertTicks: Show vertex ticks.
        trajectory: Show trajectories.
        primaryVisibility: Primary visibility flag.
        secondaryVisibility: Secondary visibility flag.
    """
    payload = {"action": action}
    if name:
        payload["name"] = name
    if names:
        payload["names"] = names
    if layer:
        payload["layer"] = layer
    if color:
        payload["color"] = color
    if hidden is not None:
        payload["hidden"] = hidden
    if frozen is not None:
        payload["frozen"] = frozen
    if renderable is not None:
        payload["renderable"] = renderable
    if parent:
        payload["parent"] = parent
    if rename:
        payload["rename"] = rename
    if boxMode is not None:
        payload["boxMode"] = boxMode
    if castShadows is not None:
        payload["castShadows"] = castShadows
    if rcvShadows is not None:
        payload["rcvShadows"] = rcvShadows
    if xRayMtl is not None:
        payload["xRayMtl"] = xRayMtl
    if backCull is not None:
        payload["backCull"] = backCull
    if allEdges is not None:
        payload["allEdges"] = allEdges
    if vertTicks is not None:
        payload["vertTicks"] = vertTicks
    if trajectory is not None:
        payload["trajectory"] = trajectory
    if primaryVisibility is not None:
        payload["primaryVisibility"] = primaryVisibility
    if secondaryVisibility is not None:
        payload["secondaryVisibility"] = secondaryVisibility

    response = client.send_command(json.dumps(payload), cmd_type="native:manage_layers")
    return response.get("result", "{}")


@mcp.tool()
def manage_groups(
    action: str,
    name: str = "",
    names: list[str] | None = None,
    group: str = "",
) -> str:
    """Manage object groups — create, ungroup, open, close, attach, detach.

    All operations run through pure C++ SDK (Interface::GroupNodes). Requires native bridge.

    Actions:
        list: List all groups with members.
        create: Create a new group from objects. Params: names (objects to group), name (optional group name).
        ungroup: Dissolve a group. Params: name (group head name).
        open: Open a group for editing. Params: name.
        close: Close an open group. Params: name.
        attach: Add objects to existing group. Params: group (target), names (objects to add).
        detach: Remove objects from their group. Params: names (objects to detach).

    Args:
        action: One of: list, create, ungroup, open, close, attach, detach.
        name: Group name (for create, ungroup, open, close).
        names: Object names (for create, attach, detach).
        group: Target group name (for attach).
    """
    payload = {"action": action}
    if name:
        payload["name"] = name
    if names:
        payload["names"] = names
    if group:
        payload["group"] = group

    response = client.send_command(json.dumps(payload), cmd_type="native:manage_groups")
    return response.get("result", "{}")


@mcp.tool()
def manage_selection_sets(
    action: str,
    name: str = "",
    names: list[str] | None = None,
) -> str:
    """Manage named selection sets — create, delete, list, select, replace.

    All operations run through pure C++ SDK (INamedSelectionSetManager). Requires native bridge.

    Actions:
        list: List all named selection sets with members.
        create: Create a new selection set. Params: name (set name), names (object names).
        delete: Delete a selection set by name.
        select: Select all objects in a set (replaces current selection).
        replace: Replace a set's members. Params: name, names (new members).

    Args:
        action: One of: list, create, delete, select, replace.
        name: Selection set name.
        names: Object names (for create, replace).
    """
    payload = {"action": action}
    if name:
        payload["name"] = name
    if names:
        payload["names"] = names

    response = client.send_command(json.dumps(payload), cmd_type="native:manage_selection_sets")
    return response.get("result", "{}")
