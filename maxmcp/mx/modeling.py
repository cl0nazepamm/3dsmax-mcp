"""mx modeling helpers: booleans, modifiers, meshes, splines, curves, lofts and QA."""

from __future__ import annotations

from typing import Any

from ._runtime import Node, call, ms_value, node_name

__all__ = [
    "boolean",
    "add_modifier",
    "set_modifier",
    "remove_modifier",
    "mesh",
    "inspect_mesh",
    "mesh_edit",
    "spline",
    "curve",
    "loft",
    "qa",
    "contacts",
]


def _plain(value: Any) -> Any:
    """Tuples to lists, recursively, so coordinates can be written as tuples."""
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _target(target: Node | str | None) -> dict[str, Any]:
    """name/handle arguments for tools that take both; handle wins when the Node has one."""
    if target is None:
        return {}
    if isinstance(target, Node) and target.handle:
        return {"handle": target.handle}
    return {"name": node_name(target)}


def _names(targets: Any) -> list[str] | str | None:
    # A lone name passes through for the tool to coerce, instead of being split into characters.
    if targets is None or isinstance(targets, (Node, str)):
        return None if targets is None else node_name(targets)
    return [node_name(item) for item in targets]


def boolean(
    target: Node | str,
    operation: str = "subtract",
    operands: list[Node | str] | None = None,
    cutters: list[dict[str, Any]] | None = None,
    repeat: dict[str, Any] | None = None,
    operations: list[str] | None = None,
    operation_option: str | None = None,
    method: str | None = None,
    voxel_size: float | None = None,
    live: bool | None = None,
    use_operand_material: bool | None = None,
    new_modifier: bool | None = None,
    modifier_name: str | None = None,
    backend: str | None = None,
) -> Any:
    """Apply operands and/or inline cutters to `target` with boolean_operation action=apply.

    Non-live operands are consumed into the modifier; cutters are {name, shape, size, pos, rot, ...}.
    """
    return call(
        "boolean_operation",
        action="apply",
        name=node_name(target),
        operation=operation,
        operands=_names(operands),
        cutters=_plain(cutters),
        repeat=_plain(repeat),
        operations=_plain(operations),
        operation_option=operation_option,
        method=method,
        voxel_size=voxel_size,
        live=live,
        use_operand_material=use_operand_material,
        new_modifier=new_modifier,
        modifier_name=modifier_name,
        backend=backend,
    )


def add_modifier(target: Node | str, modifier: str, params: dict[str, Any] | str = "") -> Any:
    """Add a modifier by class name with its parameters; returns the tool's result.

    params: {name: value}, e.g. {"iterations": 2} or {"BendAxis": "x", "BendAngle": 45};
    mx.params(class_name=modifier, kind="modifier") lists them. MAXScript text
    ("iterations:2") is still accepted.
    """
    return call("add_modifier", **_target(target), modifier=modifier, params=params)


def set_modifier(
    target: Node | str | None,
    property_name: str,
    property_value: Any,
    modifier_index: int | None = None,
    modifier_name: str | None = None,
    modifier_class: str | None = None,
    names: list[Node | str] | None = None,
    selection_only: bool | None = None,
) -> Any:
    """Set one modifier property via set_modifier_property; pick the modifier by index, name or class.

    property_value: a Python value (2, True, 0.5, "x" for a choice, {"rotation": [0, 0, 45]} for a
    gizmo) set through the parameter model and read back per hit; a string that is not a choice
    name is taken as MAXScript text.
    """
    return call(
        "set_modifier_property",
        property_name=property_name,
        property_value=list(property_value) if isinstance(property_value, tuple) else property_value,
        name=None if target is None else node_name(target),
        names=_names(names),
        selection_only=selection_only,
        modifier_class=modifier_class,
        modifier_name=modifier_name,
        modifier_index=modifier_index,
    )


def remove_modifier(target: Node | str, modifier: str) -> Any:
    """Remove a modifier by its stack entry name; returns the tool's message."""
    return call("remove_modifier", **_target(target), modifier=modifier)


def mesh(name: str, vertices: list[Any], faces: list[Any]) -> Node:
    """Create an Editable Poly from world vertices and 1-based polygon faces (create_mesh)."""
    return Node.from_result(call("create_mesh", name=name, vertices=_plain(vertices), faces=_plain(faces)))


def inspect_mesh(
    target: Node | str | None = None,
    level: str | None = None,
    selection: dict[str, Any] | None = None,
    limit: int | None = None,
    capture: bool | None = None,
) -> Any:
    """Base-cage component IDs, centers and mesh_token (inspect_mesh); None targets the selection."""
    return call(
        "inspect_mesh",
        **_target(target),
        level=level,
        selection=_plain(selection),
        limit=limit,
        capture=capture,
    )


def mesh_edit(
    target: Node | str | None,
    operations: list[dict[str, Any]],
    expected_mesh: str = "",
    convert: bool | None = None,
    show_selection: bool | None = None,
) -> Any:
    """Run component edits as one undoable batch (mesh_edit); returns steps, counts and mesh_token.

    expected_mesh is inspect_mesh's mesh_token, guarding component IDs against stale geometry.
    """
    return call(
        "mesh_edit",
        operations=_plain(operations),
        **_target(target),
        convert=convert,
        expected_mesh=expected_mesh,
        show_selection=show_selection,
    )


def spline(
    name: str,
    points: Any,
    closed: bool = False,
    knot_type: str | None = None,
    thickness: float | None = None,
    sides: int | None = None,
    center_pivot: bool | None = None,
) -> Node:
    """Create a spline shape from world points (draw_spline action=create).

    points are [x,y,z] or {pos, type, seg, in_vec, out_vec} dicts.
    """
    return Node.from_result(call(
        "draw_spline",
        action="create",
        name=name,
        points=_plain(points),
        closed=closed,
        knot_type=knot_type,
        thickness=thickness,
        sides=sides,
        center_pivot=center_pivot,
    ))


def curve(
    definition: dict[str, Any] | None = None,
    name: Node | str = "",
    action: str = "create",
    parameters: dict[str, Any] | None = None,
    expected_model: str | None = None,
    include_definition: bool | None = None,
    handle: int | None = None,
) -> Any:
    """Preview, create, read or update a parametric curve/sweep/loft (curve_model).

    create returns a Node; preview, read and update return the tool's result.
    """
    target = _target(name) if isinstance(name, Node) else {"name": name, "handle": handle}
    result = call(
        "curve_model",
        action=action,
        **target,
        definition=_plain(definition),
        parameters=_plain(parameters),
        expected_model=expected_model,
        include_definition=include_definition,
    )
    if action == "create" and isinstance(result, dict) and result.get("name"):
        return Node.from_result(result)
    return result


def loft(
    sections: list[Any] | None = None,
    name: Node | str = "",
    action: str = "create",
    parameters: dict[str, Any] | None = None,
    profile_closed: bool | None = None,
    close_path: bool | None = None,
    caps: bool | None = None,
    reverse: bool | None = None,
    include_definition: bool | None = None,
    handle: int | None = None,
) -> Any:
    """Create, read or update a parameterized quad loft (loft_mesh); create returns a Node.

    sections are matched point lists [[[x,y,z], ...], ...]; coordinates may be parameter expressions.
    """
    target = _target(name) if isinstance(name, Node) else {"name": name, "handle": handle}
    result = call(
        "loft_mesh",
        action=action,
        **target,
        sections=_plain(sections),
        parameters=_plain(parameters),
        profile_closed=profile_closed,
        close_path=close_path,
        caps=caps,
        reverse=reverse,
        include_definition=include_definition,
    )
    if action.strip().lower() == "create" and isinstance(result, dict) and result.get("name"):
        return Node.from_result(result)
    return result


def qa(
    target: Node | str | None = None,
    limit: int | None = None,
    max_faces: int | None = None,
    area_epsilon: float | None = None,
) -> Any:
    """Evaluated-mesh topology report (geometry_qa); None targets the selection."""
    return call("geometry_qa", **_target(target), limit=limit, max_faces=max_faces, area_epsilon=area_epsilon)


def contacts(
    names: list[Node | str] | None = None,
    against: list[Node | str] | None = None,
    tolerance: float | None = None,
    near_gap: float | None = None,
    include_separate: bool | None = None,
    limit: int | None = None,
    max_pairs: int | None = None,
    max_faces: int | None = None,
    time_budget_s: float | None = None,
) -> Any:
    """Penetration, touch and near-gap report between meshes (contact_check).

    With `against`, each of `names` is checked only against those nodes; with neither, the selection is used.
    """
    return call(
        "contact_check",
        names=_names(names),
        against=_names(against),
        tolerance=tolerance,
        near_gap=near_gap,
        include_separate=include_separate,
        limit=limit,
        max_pairs=max_pairs,
        max_faces=max_faces,
        time_budget_s=time_budget_s,
    )
