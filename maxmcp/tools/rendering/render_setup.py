"""render_setup: the one command for lights, sun, environment, camera, render settings and renders."""

from __future__ import annotations

from typing import Any

from ...server import mcp
from . import _render_setup as engine


@mcp.tool()
def render_setup(
    renderer: str = "",
    quality: str = "",
    settings: dict[str, Any] | None = None,
    lights: list[dict[str, Any]] | None = None,
    sun: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    camera: dict[str, Any] | None = None,
    render: dict[str, Any] | bool | None = None,
    distance_unit: str = "scene",
    job: str = "",
) -> dict:
    """Light, frame and render the scene: the same call on every renderer.

    The script names what it wants in neutral terms; the active renderer's preset turns
    that into its own classes and parameters (Octane, FStorm, V-Ray, Corona, Scanline, ART).
    Never write renderer-specific light classes by hand. Every argument is optional and
    only what is given changes, in one undo step, verified by readback:

      render_setup()                              what this renderer offers and what the
                                                  scene has: kinds with their fields and
                                                  units, notes, lights, cameras, settings
      renderer="octane"                           make it the production renderer (a preset
                                                  id or a renderer name; not undoable)
      quality="draft"|"preview"|"final"           the preset's sampling level
      settings={"samples": 600, "denoise": True}  neutral names, or the renderer's own
                                                  parameter names for anything else
      lights=[{"name": "Lamp", "kind": "sphere", "radius": 4, "position": [0, 0, 150],
               "intensity": 800, "unit": "lm", "kelvin": 2700}]
          kind: rectangle (width, height), disk (radius), sphere (radius), cylinder (radius,
          length), point. position, and aim_at or direction for the flat and tube shapes.
          Color is kelvin or rgb (linear, in the rendering space). Also enabled,
          cast_shadows, visible, double_sided where the renderer has them.
      sun={"elevation": 40, "azimuth": 220}       degrees above the horizon, and clockwise
                                                  from +Y (north) toward +X (180, south,
                                                  when omitted); or direction. One of the
                                                  two given alone keeps the other.
                                                  intensity is a multiplier (1 = the
                                                  renderer's physical sun).
      environment={"path": "C:/hdri/sky.exr", "intensity": 1, "rotation": 90}
                                                  an EXR or HDR lighting the scene;
                                                  replace=True to replace another one
      camera={"name": "Cam", "position": [300, -400, 150], "aim_at": [0, 0, 100], "fov": 55}
      render={"camera": "Cam", "size": [1280, 720]}
                                                  renders that camera (the active viewport
                                                  when no camera is given) to a JPG and
                                                  shows you the image. output= sets the file.

    A name that already exists is updated, with only the fields given: run the same script
    again to adjust, e.g. lights=[{"name": "Lamp", "kind": "sphere", "intensity": 400}].
    Lights without a name are always created. Delete with mx.delete.

    intensity: give unit (lm, cd, nits, W, or renderer for the renderer's own number); it
    defaults to the kind's unit. A unit the renderer lacks is converted when the shape
    defines it and the reply says so; otherwise the call fails naming the units it takes.
    For one script on several renderers: intensity={"lm": 800, "fstorm": 30} uses the
    first entry (a unit or a preset id) the active renderer takes.
    native={"parameter": value} writes any other parameter of the object by its own name.
    Distances are scene units unless distance_unit is mm, cm, m, in or ft.

    A render that outlives its wait returns {"status": "running", "job": ...}: call
    render_setup(job=...) to wait again and never start it twice. Render only when the
    user asked for one. A renderer without a preset is refused with the list of presets;
    presets are JSON files, and one dropped into %LOCALAPPDATA%/3dsmax-mcp/renderers adds
    a renderer without code.
    """
    asked = any(value is not None for value in (settings, lights, sun, environment, camera))
    if job:
        return {"render": _show(engine.render(None, job))}
    if not (renderer or quality or asked or render):
        return engine.describe()
    out: dict[str, Any] = {}
    if renderer or quality or asked:
        out = engine.apply(renderer=renderer, quality=quality, settings=settings, lights=lights, sun=sun,
                           environment=environment, camera=camera, distance_unit=distance_unit)
    if render:
        out["render"] = _show(engine.render(render))
    return out


def _show(result: dict[str, Any]) -> dict[str, Any]:
    """Attach a finished render to the run_python reply, so the image arrives with the result."""
    from ...mx import _runtime

    if result.get("file") and "warning" not in result and result.get("status") != "running":
        if _runtime.show(result["file"]):
            result["shown"] = True
    return result
