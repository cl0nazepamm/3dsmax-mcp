"""mx shading helpers: materials, and render_setup for lights, cameras and renders."""

from __future__ import annotations

from typing import Any, Iterable

from ._runtime import Node, call, ms_value, node_name

__all__ = [
    "material",
    "share_material",
    "set_material",
    "set_material_properties",
    "materials",
    "render_setup",
]

def _names_handles(targets: Node | str | Iterable[Node | str]) -> tuple[list[str] | None, list[int] | None]:
    if isinstance(targets, (Node, str)):
        targets = [targets]
    names: list[str] = []
    handles: list[int] = []
    for target in targets:
        if isinstance(target, Node) and target.handle:
            handles.append(target.handle)
        else:
            names.append(node_name(target))
    return names or None, handles or None


def _material_target(target: Node | str) -> dict[str, Any]:
    if isinstance(target, Node) and target.handle:
        return {"handle": target.handle}
    return {"name": node_name(target)}


def material(targets: Node | str | Iterable[Node | str], material_class: str = "", material_name: str = "",
             params: str = "") -> Any:
    """Create a material and assign it to targets (assign_material).

    params: MAXScript constructor arguments, e.g. 'base_color:(color 200 30 30) roughness:0.4'.
    """
    names, handles = _names_handles(targets)
    return call("assign_material", names=names, handles=handles, material_class=material_class,
                material_name=material_name, params=params)


def share_material(targets: Node | str | Iterable[Node | str], source: Node | str) -> Any:
    """Instance the source object's existing material onto targets (assign_material)."""
    names, handles = _names_handles(targets)
    if isinstance(source, Node) and source.handle:
        return call("assign_material", names=names, handles=handles, source_handle=source.handle)
    return call("assign_material", names=names, handles=handles, source_name=node_name(source))


def set_material(target: Node | str, property: str, value: Any, sub_material_index: int = 0) -> Any:  # noqa: A002
    """Set one property on the target's material or sub-material (set_material_property).

    value: MAXScript value text, e.g. '0.5' or '(color 255 0 0)'.
    """
    return call("set_material_property", **_material_target(target), property=property, value=ms_value(value),
                sub_material_index=sub_material_index)


def set_material_properties(target: Node | str, properties: dict[str, Any], sub_material_index: int = 0) -> Any:
    """Set several properties on the target's material in one call (set_material_properties)."""
    return call("set_material_properties", **_material_target(target),
                properties={key: ms_value(v) for key, v in properties.items()},
                sub_material_index=sub_material_index)


def materials() -> Any:
    """List materials assigned to scene objects (get_materials)."""
    return call("get_materials")


def render_setup(renderer: str = "", quality: str = "", settings: dict[str, Any] | None = None,
                 lights: list[dict[str, Any]] | None = None, sun: dict[str, Any] | None = None,
                 environment: dict[str, Any] | None = None, camera: dict[str, Any] | None = None,
                 render: dict[str, Any] | bool | None = None, distance_unit: str = "scene", job: str = "") -> Any:
    """Light, frame and render the scene with the same call on every renderer (render_setup).

    No arguments: what the renderer offers and what the scene has. mx.help("render_setup") is the manual.
    """
    return call("render_setup", renderer=renderer or None, quality=quality or None, settings=settings, lights=lights,
                sun=sun, environment=environment, camera=camera, render=render or None,
                distance_unit=None if distance_unit == "scene" else distance_unit, job=job or None)
