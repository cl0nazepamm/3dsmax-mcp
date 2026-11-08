"""mx shading helpers: materials and lights over the guarded 3dsmax-mcp tools."""

from __future__ import annotations

from typing import Any, Iterable

from ._runtime import Node, call, ms_value, node_name

__all__ = [
    "material",
    "share_material",
    "set_material",
    "set_material_properties",
    "materials",
    "light_capabilities",
    "lights",
    "area_light",
    "point_light",
    "sun",
    "inspect_lights",
    "edit_lights",
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


def _light_target(target: Node | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(target, dict):
        return target
    if isinstance(target, Node) and target.handle:
        return {"node_ref": {"handle": target.handle}}
    return {"node_ref": {"name": node_name(target)}}


def _vector(value: Any) -> list[float] | None:
    return None if value is None else list(value)


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


def light_capabilities(renderer: str = "current", detail: str = "summary") -> Any:
    """Supported light kinds, shapes, output units and color policy (lighting_capabilities)."""
    return call("lighting_capabilities", renderer=renderer, detail=detail)


def lights(lights: list[dict[str, Any]], renderer: str = "current", distance_unit: str = "scene",
           expected_context: str | None = None) -> Any:
    """Create 1..32 lights from LightSpec dicts in one transaction (create_lights)."""
    return call("create_lights", lights=list(lights), renderer=renderer, distance_unit=distance_unit,
                expected_context=expected_context)


def _spec(name: str, kind: str, *, at: Any = None, aim_at: Any = None, direction: Any = None,
          intensity: float | None = None, unit: str | None = None, color: dict[str, Any] | None = None,
          fstorm: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {"name": name, "kind": kind, **{k: v for k, v in fields.items() if v is not None}}
    if at is not None:
        spec["position"] = list(at)
    orientation = {k: v for k, v in (("aim_at", _vector(aim_at)), ("direction", _vector(direction))) if v is not None}
    if orientation:
        spec["orientation"] = orientation
    output = {k: v for k, v in (("value", intensity), ("unit", unit)) if v is not None}
    if output:
        spec["output"] = output
    if color is not None:
        spec["color"] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in color.items()}
    if fstorm is not None:
        spec["fstorm"] = fstorm
    return spec


def _created(result: dict[str, Any]) -> Node:
    entry = result["lights"][0]
    return Node.from_result({**entry, **entry["light_ref"]["node_ref"]})


def area_light(name: str, width: float | None = None, height: float | None = None, at: Any = None,
               aim_at: Any = None, intensity: float | None = None, unit: str | None = None,
               shape: str = "rectangle", *, radius: float | None = None, length: float | None = None,
               direction: Any = None, color: dict[str, Any] | None = None, fstorm: dict[str, Any] | None = None,
               renderer: str = "current", distance_unit: str = "scene") -> Node:
    """Create one area light (create_lights) and return its Node.

    Size keys per shape: rectangle width+height, disk/sphere radius, cylinder radius+length.
    intensity/unit form the required output; units come from light_capabilities (renderer, lm, cd, cd/m2).
    """
    size = {k: v for k, v in (("width", width), ("height", height), ("radius", radius), ("length", length))
            if v is not None}
    spec = _spec(name, "area", at=at, aim_at=aim_at, direction=direction, intensity=intensity, unit=unit,
                 color=color, fstorm=fstorm, shape=shape, size=size or None)
    return _created(lights([spec], renderer=renderer, distance_unit=distance_unit))


def point_light(name: str, at: Any = None, intensity: float | None = None, unit: str | None = None, *,
                color: dict[str, Any] | None = None, renderer: str = "current",
                distance_unit: str = "scene") -> Node:
    """Create one point light (create_lights; photometric only) and return its Node."""
    spec = _spec(name, "point", at=at, intensity=intensity, unit=unit, color=color)
    return _created(lights([spec], renderer=renderer, distance_unit=distance_unit))


def sun(name: str, direction: Any = None, intensity: float | None = None, unit: str | None = None, *,
        aim_at: Any = None, at: Any = None, color: dict[str, Any] | None = None,
        fstorm: dict[str, Any] | None = None, renderer: str = "current", distance_unit: str = "scene") -> Node:
    """Create one directional sun (create_lights) and return its Node.

    Give direction or aim_at, or for FStorm fstorm={'solar': {'hour': ..., 'month': ...}} without orientation.
    """
    spec = _spec(name, "directional", at=at, aim_at=aim_at, direction=direction, intensity=intensity, unit=unit,
                 color=color, fstorm=fstorm, body="sun")
    return _created(lights([spec], renderer=renderer, distance_unit=distance_unit))


def inspect_lights(targets: Node | str | dict[str, Any] | Iterable[Node | str | dict[str, Any]],
                   detail: str = "summary") -> Any:
    """Decode actual light settings (inspect_lights).

    targets: Nodes, names, light_ref dicts from create_lights, or {node_ref: ..., provider: ...}.
    """
    if isinstance(targets, (Node, str, dict)):
        targets = [targets]
    return call("inspect_lights", targets=[_light_target(t) for t in targets], detail=detail)


def edit_lights(edits: list[dict[str, Any]], distance_unit: str = "scene") -> Any:
    """Apply inspected light edits atomically (edit_lights): light_ref, expected_light, changes."""
    return call("edit_lights", edits=list(edits), distance_unit=distance_unit)
