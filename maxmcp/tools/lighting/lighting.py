"""Renderer-independent lighting entry points."""
from typing import Any
from ...server import mcp
from . import _lighting as impl
from ._lighting import LightSpec


@mcp.tool()
def lighting_capabilities(renderer: str = "current", detail: str = "summary") -> dict:
    """Discover supported lighting shapes, units, renderer routes and color policy.
    Renderer is current or an exact installed renderer name. detail=full also
    returns the strict light specification schema. Never changes the renderer.
    """
    return impl.capabilities(renderer, detail)


@mcp.tool()
def create_lights(lights: list[LightSpec], renderer: str = "current", distance_unit: str = "scene",
                  expected_context: str | None = None) -> dict:
    """Create 1..32 semantic lights in one native transaction with typed readback.
    Finite spherical bulbs are area/sphere; HDRI skies are environment. Distances
    use scene, mm, cm, m, in or ft. Orientation uses a world aim_at or direction.
    FStorm: area rectangle/disk/sphere, renderer power, RGB or Kelvin. Directional
    sun uses fstorm.solar (hour/month/latitude/north_direction) without orientation.
    Optional fstorm controls include visibility, double_sided and sun_size/model.
    Discover supported kinds and physical/native output units first. Native maps
    and environment bindings are created in the same transaction. No render starts.
    """
    return impl.create(lights, renderer, distance_unit, expected_context)


@mcp.tool()
def inspect_lights(targets: list[dict[str, Any]], detail: str = "summary") -> dict:
    """Decode actual light settings. Targets are light_ref objects returned by
    create_lights, or {node_ref:{handle/name/path},provider:optional}. detail=bindings
    includes exact properties/schema/state tokens for guarded edits.
    """
    if not targets or len(targets) > 32 or detail not in {"summary", "bindings"}:
        raise ValueError("Supply 1..32 targets and detail=summary or bindings.")
    result = []
    for target in targets:
        ref = target.get("owner_ref") or {"node": target["node_ref"], "scope": "base_object"}
        state = impl.inspect_one(ref, target.get("provider"))
        if detail == "summary": state = impl.summary(state)
        result.append(state)
    return {"lights": result}


@mcp.tool()
def edit_lights(edits: list[dict[str, Any]], distance_unit: str = "scene") -> dict:
    """Edit inspected lights atomically. Each edit contains light_ref,
    expected_light (the light_token from inspection), changes and optional
    sharing='all_instances'. Changes accept color, output, enabled, cast_shadows
    and a complete size for the existing shape. FStorm also accepts nested fstorm
    controls: visible, gi_visible, double_sided, affect_diffuse, affect_glossy,
    solar, sun_model, sun_size. Solar edits preserve unspecified settings and
    require an untargeted sun. Sun RGB selects legacy mode; Kelvin is area-only.
    Dimensions use distance_unit; inspected dimensions are in scene units.
    A changed light/map/controller invalidates the token; inspect again.
    Move/aim nodes with transform tools; changing emitter class is not implicit.
    """
    return impl.edit(edits, distance_unit)
