"""mx objects helpers: create, find, select, parent, clone and delete scene nodes."""

from __future__ import annotations

from typing import Any, Sequence

from ._runtime import Node, call, node_name

__all__ = [
    "box",
    "sphere",
    "cylinder",
    "plane",
    "create",
    "get",
    "find",
    "scene",
    "selection",
    "delete",
    "clone",
    "parent",
    "select",
    "params",
]

Vec3 = Sequence[float]


def _list(value: Vec3 | None) -> list[float] | None:
    return list(value) if value is not None else None


def create(
    type: str,  # noqa: A002 - tool argument name
    name: str | None = None,
    params: dict[str, Any] | str | None = None,
    pos: Vec3 | None = None,
    pos_mode: str | None = None,
    length: float | None = None,
    width: float | None = None,
    height: float | None = None,
    depth: float | None = None,
    radius: float | None = None,
    radius1: float | None = None,
    radius2: float | None = None,
    fillet: float | None = None,
    capheight: float | None = None,
) -> Node:
    """Create any create_object type (Teapot, Tube, ChamferBox, ...) and return its Node.

    params: {name: value} for any of the class's parameters (mx.params(class_name=type)
    lists them), in degrees, 0-1 colors and choice names. pos_mode: ground (default,
    pos is the bbox bottom-center), center, or pivot.
    """
    return Node.from_result(call(
        "create_object", type=type, name=name, params=params, pos=_list(pos), pos_mode=pos_mode,
        length=length, width=width, height=height, depth=depth, radius=radius,
        radius1=radius1, radius2=radius2, fillet=fillet, capheight=capheight,
    ))


def box(
    name: str | None = None,
    width: float | None = None,
    length: float | None = None,
    height: float | None = None,
    pos: Vec3 | None = None,
    pos_mode: str | None = None,
) -> Node:
    """Create a Box; unset dimensions use the tool's defaults."""
    return create("Box", name=name, width=width, length=length, height=height, pos=pos, pos_mode=pos_mode)


def sphere(
    name: str | None = None,
    radius: float | None = None,
    pos: Vec3 | None = None,
    pos_mode: str | None = None,
) -> Node:
    """Create a Sphere."""
    return create("Sphere", name=name, radius=radius, pos=pos, pos_mode=pos_mode)


def cylinder(
    name: str | None = None,
    radius: float | None = None,
    height: float | None = None,
    pos: Vec3 | None = None,
    pos_mode: str | None = None,
) -> Node:
    """Create a Cylinder."""
    return create("Cylinder", name=name, radius=radius, height=height, pos=pos, pos_mode=pos_mode)


def plane(
    name: str | None = None,
    width: float | None = None,
    length: float | None = None,
    pos: Vec3 | None = None,
    pos_mode: str | None = None,
) -> Node:
    """Create a Plane."""
    return create("Plane", name=name, width=width, length=length, pos=pos, pos_mode=pos_mode)


def get(name: Node | str) -> Node:
    """An existing node by name, verified with inspect_object (raises MaxToolError if missing)."""
    return Node.from_result(call("inspect_object", name=node_name(name)))


def find(
    pattern: str | None = None,
    class_name: str | None = None,
    layer: str | None = None,
    roots_only: bool | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[Node] | dict[str, Any]:
    """Nodes matching the filters (query_scene action=filter).

    Give at least one filter (pattern="*" lists all): with none the tool's scene summary is returned unchanged.
    """
    result = call(
        "query_scene", action="filter", pattern=pattern, class_name=class_name, layer=layer,
        roots_only=roots_only, limit=limit, offset=offset,
    )
    objects = result.get("objects") if isinstance(result, dict) else None
    if not isinstance(objects, list):
        return result
    return [Node.from_result(entry) for entry in objects]


def scene(max_roots: int | None = None) -> Any:
    """Scene overview: counts, classes, materials, modifiers, layers and roots."""
    return call("query_scene", action="overview", max_roots=max_roots)


def selection(detail: str | None = None, max_items: int | None = None) -> list[Node]:
    """The currently selected nodes (query_scene action=selection; detail=compact|full)."""
    result = call("query_scene", action="selection", detail=detail, max_items=max_items)
    entries = result if isinstance(result, list) else (result or {}).get("objects", [])
    return [Node.from_result(entry) for entry in entries]


def delete(*targets: Node | str, dry_run: bool | None = None) -> Any:
    """Delete nodes (by handle when a Node has one, else by name)."""
    handles = [t.handle for t in targets if isinstance(t, Node) and t.handle]
    names = [node_name(t) for t in targets if not (isinstance(t, Node) and t.handle)]
    return call("delete_objects", names=names or None, handles=handles or None, dry_run=dry_run)


def clone(
    target: Node | str,
    mode: str | None = None,
    offset: Vec3 | None = None,
    count: int | None = None,
) -> list[Node]:
    """Clone one node and return the new Nodes.

    mode: copy (default), instance or reference; count new copies, each offset * (1..count) from the original.
    """
    result = call("clone_objects", names=[node_name(target)], mode=mode, offset=_list(offset), count=count)
    cloned, handles = result.get("cloned", []), result.get("handles") or []
    if len(handles) != len(cloned):
        handles = [None] * len(cloned)
    nodes = result.get("nodes") or [{"name": name, "handle": handle} for name, handle in zip(cloned, handles)]
    return [Node.from_result(entry) for entry in nodes]


def parent(child: Node | str, parent: Node | str | None) -> Any:  # noqa: A002 - tool argument name
    """Parent child under parent; parent=None unparents it."""
    return call(
        "set_parent",
        children=[node_name(child)],
        parent=node_name(parent) if parent is not None else None,
    )


def select(
    *targets: Node | str,
    pattern: str | None = None,
    class_name: str | None = None,
    all: bool | None = None,  # noqa: A002 - tool argument name
) -> Any:
    """Select nodes by name, pattern, class or all; select() with nothing clears the selection."""
    return call(
        "select_objects",
        names=[node_name(t) for t in targets],
        pattern=pattern,
        class_name=class_name,
        all=all,
    )


def params(
    target: Node | str | None = None,
    path: str = "object",
    values: dict[str, Any] | None = None,
    *,
    class_name: str | None = None,
    kind: str | None = None,
    names: list[str] | None = None,
    query: str | None = None,
    offset: int | None = None,
    **set_values: Any,
) -> Any:
    """Describe, read or set the parameters of anything a node carries, or of a class (parameters).

    mx.params(node, "modifiers[Bend]")                    -> every parameter: type, value, default,
                                                            range, named choices, label
    mx.params(node, "modifiers[Bend]", {"BendAngle": 30, "BendAxis": "x"})  -> readback in ["values"]
    mx.params(node, "material", base_color_map={"class": "Noise", "params": {"size": 4}})
    mx.params(node, "material.base_color_map", names=["size"])               -> {"values": {...}}
    mx.params(class_name="Lathe", kind="modifier")        -> before any instance exists

    path: object | modifiers[<name, class or 1-based position>] | material | material[2] |
    transform | node, then .<map slot or subanim> (material.base_color_map, modifiers[Bend].gizmo).
    Values: degrees, percentages 0-100, colors 0-1 or "#rrggbb", choice names, transforms as
    {"position", "rotation", "scale"}, map slots as {"class": ..., "params": {...}}.
    """
    merged = dict(values or {})
    merged.update(set_values)
    if class_name is not None:
        if merged or names:
            raise ValueError("class_name describes a class; set or get on a node and path")
        return call("parameters", action="describe", class_name=class_name, kind=kind, query=query, offset=offset)
    if target is None:
        raise ValueError("pass a node (or name), or class_name=... to describe a class")
    selector: dict[str, Any] = (
        {"handle": target.handle} if isinstance(target, Node) and target.handle
        else {"handle": getattr(target, "handle")} if not isinstance(target, str) and getattr(target, "handle", None)
        else {"name": node_name(target)})
    if merged:
        return call("parameters", action="set", path=path, values=merged, **selector)
    if names:
        return call("parameters", action="get", path=path, names=list(names), **selector)
    return call("parameters", action="describe", path=path, query=query, offset=offset, **selector)
