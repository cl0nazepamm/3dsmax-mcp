"""Renderer-independent light specifications compiled to native typed plans.

Bindings use exact class/PB IDs and are checked against live descriptors before
creation. Renderer intensity modes remain explicit; no invented unit conversion.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from . import plugin_schema as api
from .plugin_semantics import VRAY_LIGHT, VRAY_SHAPES, VRAY_UNITS, OCTANE_SHAPES, VRAY_IMAGE_MAPPING
from .plugin_semantics import (CORONA_RENDERER, CORONA_LIGHT, CORONA_SUN, CORONA_MOON, CORONA_SKY, CORONA_BITMAP,
                               CORONA_SHAPES, CORONA_UNITS, CORONA_COLOR_MODES, CORONA_SUN_COLOR_MODES, CORONA_BITMAP_MAPPING)

LIGHT, MAP, RENDERER = 48, 3088, 3840
OCTANE_LIGHT = (592523983, 1640440069)
OCTANE_EMISSION = (249824500, 396179973)
OCTANE_ENV = (1197826109, 1274825817)
OCTANE_IMAGE = (1865767904, 1645758520)
VRAY_IMAGE = (1734939723, 46203261)
PHOTOMETRIC = {
    "point": (842489804, 184704240), "rectangle": (911244690, 274340423),
    "disk": (1540123970, 206521102), "sphere": (2091464066, 448490290),
    "cylinder": (1190540515, 171080367),
}
RENDERER_FAMILIES = {
    (1941615238, 2012806412): "vray", (1770671000, 1323107829): "vray",
    (2717442453, 1319335807): "octane", (1, 0): "photometric",
    CORONA_RENDERER: "corona",
}
FAMILY_NAMES = {"vray", "octane", "photometric", "corona"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

Vector3 = Annotated[list[float], Field(min_length=3, max_length=3)]


class LightColor(Strict):
    kelvin: float | None = Field(default=None, ge=500, le=30000)
    rgb: Vector3 | None = None
    space: Literal["rendering"] = "rendering"

    @model_validator(mode="after")
    def one_color(self):
        if (self.kelvin is None) == (self.rgb is None):
            raise ValueError("Supply exactly one of kelvin or rgb.")
        if self.rgb is not None and any(x < 0 for x in self.rgb):
            raise ValueError("RGB components must be nonnegative scene-linear values.")
        return self


class LightOutput(Strict):
    value: float = Field(ge=0)
    unit: Literal["renderer", "lm", "cd", "cd/m2"]


class LightSize(Strict):
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    radius: float | None = Field(default=None, gt=0)
    length: float | None = Field(default=None, gt=0)


class LightOrientation(Strict):
    aim_at: Vector3 | None = None
    direction: Vector3 | None = None
    up: Vector3 | None = None

    @model_validator(mode="after")
    def one_direction(self):
        if (self.aim_at is None) == (self.direction is None):
            raise ValueError("Supply aim_at or direction, exclusively.")
        return self


class EnvironmentSource(Strict):
    kind: Literal["hdri", "existing_map"] = "hdri"
    path: str | None = None
    owner_ref: dict[str, Any] | None = None
    source_color_space: str | None = None
    rotation: float = 0
    replace_existing: StrictBool = False

    @model_validator(mode="after")
    def one_source(self):
        if self.kind == "hdri" and (not self.path or self.owner_ref is not None):
            raise ValueError("HDRI requires path, without owner_ref.")
        if self.kind == "existing_map" and (not self.owner_ref or self.path is not None):
            raise ValueError("existing_map requires owner_ref, without path.")
        return self


class LightSpec(Strict):
    name: str = ""
    kind: Literal["area", "point", "environment", "directional"]
    body: Literal["sun", "moon"] | None = None  # directional only; defaults to sun
    shape: Literal["rectangle", "disk", "sphere", "cylinder"] | None = None
    size: LightSize | None = None
    position: Vector3 | None = None
    orientation: LightOrientation | None = None
    color: LightColor | None = None
    output: LightOutput
    enabled: StrictBool = True
    cast_shadows: StrictBool = True
    environment: EnvironmentSource | None = None

    @model_validator(mode="after")
    def compatible_fields(self):
        if self.kind != "directional" and self.body is not None:
            raise ValueError("Only directional lights take a body (sun or moon).")
        if self.kind == "environment":
            if self.environment is None or any(x is not None for x in (self.shape, self.size, self.position, self.orientation, self.color)):
                raise ValueError("Environment requires an environment source, without finite emitter/color fields.")
        elif self.kind == "directional":
            if self.orientation is None or any(x is not None for x in (self.shape, self.size, self.environment)):
                raise ValueError("Directional lights require orientation, without shape, size or environment source.")
        else:
            if self.environment is not None:
                raise ValueError("Only environment lights accept an environment source.")
            if self.kind == "point":
                if self.shape is not None or self.size is not None or self.orientation is not None:
                    raise ValueError("Point emitters have no shape, size or orientation.")
            else:
                required = {"rectangle": {"width", "height"}, "disk": {"radius"},
                            "sphere": {"radius"}, "cylinder": {"radius", "length"}}
                present = set(self.size.model_dump(exclude_none=True)) if self.size else set()
                if self.shape not in required or present != required[self.shape]:
                    raise ValueError("Emitter size must contain exactly the dimensions required by its shape.")
                if self.shape in {"rectangle", "disk", "cylinder"} and self.orientation is None:
                    raise ValueError("Directional emitters require orientation.")
        return self


def class_ref(ids: tuple[int, int], superclass=LIGHT) -> dict:
    return {"superclass_id": superclass, "class_id": list(ids)}


def prop(param_id: int, block_id=0) -> dict:
    return {"route": "pb2", "block_id": block_id, "param_id": param_id}


def renderer(context: dict, requested: str) -> tuple[str, dict]:
    available = context["renderers"]
    if requested == "current":
        chosen = context["renderer"]
    else:
        candidates = [r for r in available if requested.lower() in {r["name"].lower(), r["label"].lower()}]
        if not candidates and requested in FAMILY_NAMES:
            candidates = [r for r in available if RENDERER_FAMILIES.get(tuple(r["class_id"])) == requested]
        if len(candidates) != 1:
            raise ValueError("Choose an exact renderer name from lighting_capabilities; renderer selection is ambiguous or unavailable.")
        chosen = candidates[0]
    if not chosen:
        raise ValueError("No production renderer is assigned.")
    family = RENDERER_FAMILIES.get(tuple(chosen["class_id"]))
    if family is None:
        raise ValueError(f"No verified lighting provider for {chosen['label']}; generic plugin inspection remains available.")
    return family, chosen


def distance_scale(unit: str, context: dict) -> float:
    if unit == "scene":
        return 1.0
    factors = {"mm": .001, "cm": .01, "m": 1., "in": .0254, "ft": .3048}
    if unit not in factors or context["meters_per_unit"] <= 0:
        raise ValueError("Use scene, mm, cm, m, in or ft with a valid system unit scale.")
    return factors[unit] / context["meters_per_unit"]


def normalize(vector):
    length = math.sqrt(sum(v*v for v in vector))
    if length < 1e-9:
        raise ValueError("Direction/up vector is degenerate.")
    return [v / length for v in vector]


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def world_matrix(spec: LightSpec, scale: float) -> list:
    position = [x * scale for x in (spec.position or (0, 0, 0))]
    if spec.orientation is None:
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1], position]
    orientation = spec.orientation
    direction = orientation.direction or [orientation.aim_at[i]*scale-position[i] for i in range(3)]
    # Max light emission points down local -Z. Rows are its world basis.
    z = [-x for x in normalize(direction)]
    # Omitted roll hint uses world Z, with a stable Y fallback at the poles.
    # An explicitly contradictory up vector still fails instead of changing roll.
    up = orientation.up if orientation.up is not None else ([0, 1, 0] if abs(z[2]) > .999 else [0, 0, 1])
    x = normalize(cross(up, z))
    y = normalize(cross(z, x))
    return [x, y, z, position]


class Plan:
    def __init__(self, context):
        self.payload = {"version": 1, "expected_context": context["context_token"], "creates": [], "edits": []}
        self.schemas: dict[str, dict] = {}
        self.class_schemas: dict[tuple, dict] = {}
        self.lights = []
        self.existing: dict[str, dict] = {}  # plan ids bound to pre-existing owners (no create)

    def create(self, resource_id: str, ids, superclass=LIGHT, name="", matrix=None):
        ref = class_ref(ids, superclass)
        cache_key = (superclass, *ids)
        if cache_key not in self.class_schemas:
            self.class_schemas[cache_key] = api.all_properties(class_ref=ref)
        schema = self.class_schemas[cache_key]
        self.schemas[resource_id] = schema
        item = {"id": resource_id, "class_ref": ref, "expected_schema": schema["schema_token"], "name": name}
        if matrix is not None:
            item["matrix"] = matrix
        self.payload["creates"].append(item)
        return resource_id

    def set(self, resource_id: str, name: str | tuple[int, int], value, expected_type: str | None = None):
        schema = self.schemas[resource_id]
        found = [p for p in schema["properties"] if (p["name"] == name if isinstance(name, str)
                 else p["property_ref"] == prop(name[1], name[0]))]
        if len(found) != 1 or expected_type is not None and found[0]["type"] != expected_type:
            raise ValueError(f"SCHEMA_CONFLICT: {schema['identity']['label']} binding {name!r} is missing, ambiguous or changed type.")
        actual_type = found[0]["type"]
        allowed = {"bool"} if type(value) is bool else {"int", "index", "radioIndex", "enum"} if type(value) is int else {"float", "worldUnits", "angle", "percent", "colorChannel"} if type(value) is float else {"string", "filename"} if isinstance(value, str) else {"texturemap", "material", "node", "refTarget"} if isinstance(value, dict) or value is None else None
        if allowed is not None and actual_type not in allowed:
            raise ValueError(f"SCHEMA_CONFLICT: {name!r} is {actual_type}, incompatible with this provider binding.")
        if found[0].get("read_only"):
            raise ValueError(f"SCHEMA_CONFLICT: {name!r} is read-only.")
        self.payload["edits"].append({"owner_ref": {"created": resource_id},
                                     "property_ref": found[0]["property_ref"], "value": value})


def compile_lights(specs: list[LightSpec], context: dict, family: str, unit="scene") -> Plan:
    if not specs or len(specs) > 32:
        raise ValueError("Create 1..32 lights per transaction.")
    scale = distance_scale(unit, context)
    plan = Plan(context)
    for i, spec in enumerate(specs):
        key = f"light_{i}"
        if spec.kind == "environment":
            compile_environment(plan, key, spec, family)
            plan.lights.append({"id": key, "kind": "environment", "family": family})
            continue
        if spec.kind == "directional":
            if family != "corona":
                raise ValueError("Directional sun/moon lights currently have a verified provider for Corona only.")
            compile_corona_directional(plan, key, spec, scale)
            plan.lights.append({"id": key, "kind": "directional", "family": family})
            continue
        shape = spec.shape or "point"
        color = spec.color or LightColor(kelvin=6500)
        size = {k: v*scale for k, v in (spec.size.model_dump(exclude_none=True) if spec.size else {}).items()}
        if family == "photometric":
            if spec.output.unit != "cd":
                raise ValueError("Photometric provider currently accepts cd; its intensity property always stores candelas.")
            plan.create(key, PHOTOMETRIC[shape], name=spec.name, matrix=world_matrix(spec, scale))
            for name, value in {"on": spec.enabled, "castShadows": spec.cast_shadows, "useMultiplier": False,
                                "intensityType": 1, "intensity": spec.output.value, "useKelvin": color.kelvin is not None}.items():
                plan.set(key, name, value)
            plan.set(key, "kelvin" if color.kelvin is not None else "rgb", color.kelvin if color.kelvin is not None else list(color.rgb))
            if shape == "rectangle":
                plan.set(key, "light_width", size["width"]); plan.set(key, "light_length", size["height"])
            elif shape != "point":
                plan.set(key, "light_radius", size["radius"])
                if shape == "cylinder": plan.set(key, "light_length", size["length"])
        elif family == "vray":
            if shape not in VRAY_SHAPES or spec.output.unit not in VRAY_UNITS:
                raise ValueError("V-Ray supports rectangle/disk/sphere emitters with renderer, lm or cd/m2 output.")
            plan.create(key, VRAY_LIGHT, name=spec.name, matrix=world_matrix(spec, scale))
            for name, value in {"type": VRAY_SHAPES[shape], "on": spec.enabled, "targeted": False,
                                "castShadows": spec.cast_shadows, "normalizeColor": VRAY_UNITS[spec.output.unit],
                                "multiplier": spec.output.value, "color_mode": int(color.kelvin is not None),
                                "texmap_on": False, "doubleSided": False, "invisible": False}.items():
                plan.set(key, name, value)
            plan.set(key, "color_temperature" if color.kelvin is not None else "color", color.kelvin if color.kelvin is not None else list(color.rgb))
            if shape == "rectangle":
                # V-Ray Length is local X/U; Width is local Y/V (full extents).
                plan.set(key, "sizeLength", size["width"]); plan.set(key, "sizeWidth", size["height"])
            else: plan.set(key, "size0", size["radius"])
        elif family == "octane":
            if shape not in OCTANE_SHAPES or spec.output.unit != "renderer" or color.kelvin is None:
                raise ValueError("Octane analytic lights currently support area shapes, Kelvin and explicit renderer power.")
            emission = key + "_emission"
            plan.create(key, OCTANE_LIGHT, name=spec.name, matrix=world_matrix(spec, scale))
            plan.create(emission, OCTANE_EMISSION, MAP)
            for name, value in {"analyticLightType": OCTANE_SHAPES[shape], "enabled": spec.enabled,
                                "target_mode": False, "emission": {"created": emission}, "normalize": True}.items():
                plan.set(key, name, value)
            for name, value in {"power": spec.output.value, "temperature": color.kelvin, "normalize": True,
                                "surfaceBrightness": False, "keepInstancePower": True,
                                "castShadows": spec.cast_shadows, "illumination": True,
                                "doubleSided": False, "efficiency or texture_input_type": 0,
                                "efficiency or texture_value": .025}.items():
                plan.set(emission, name, value)
            if shape == "rectangle": plan.set(key, "quadAnalyticLightSize", [size["width"], size["height"]])
            elif shape == "disk": plan.set(key, "diskAnalyticLightSize", [2*size["radius"], 2*size["radius"]])
            elif shape == "sphere": plan.set(key, "sphereAnalyticLightRadius", size["radius"])
            else:
                plan.set(key, "tubeAnalyticLightCapRadius", size["radius"]); plan.set(key, "tubeAnalyticLightLength", size["length"])
        elif family == "corona":
            if shape not in CORONA_SHAPES or spec.output.unit not in CORONA_UNITS:
                raise ValueError("Corona supports rectangle/disk/sphere/cylinder emitters with renderer, lm or cd output.")
            if not spec.cast_shadows:
                raise ValueError("Corona lights always cast shadows; there is no per-light shadow switch.")
            plan.create(key, CORONA_LIGHT, name=spec.name, matrix=world_matrix(spec, scale))
            for name, value in {"shape": CORONA_SHAPES[shape], "on": spec.enabled, "targeted": False,
                                "intensityUnits": CORONA_UNITS[spec.output.unit], "intensity": spec.output.value,
                                "colorMode": CORONA_COLOR_MODES["kelvin" if color.kelvin is not None else "rendering_rgb"],
                                "twosidedEmission": False}.items():
                plan.set(key, name, value)
            plan.set(key, "blackbodyTemp" if color.kelvin is not None else "color", color.kelvin if color.kelvin is not None else list(color.rgb))
            # Corona width is the local X extent for rectangles and the radius otherwise;
            # height is the local Y extent for rectangles and the length for cylinders.
            if shape == "rectangle":
                plan.set(key, "width", size["width"]); plan.set(key, "height", size["height"])
            else:
                plan.set(key, "width", size["radius"])
                if shape == "cylinder": plan.set(key, "height", size["length"])
        plan.lights.append({"id": key, "kind": spec.kind, "family": family})
    return plan


def compile_environment(plan: Plan, key: str, spec: LightSpec, family: str):
    source = spec.environment
    if spec.output.unit != "renderer":
        raise ValueError("Environment output uses an explicit renderer multiplier.")
    if family == "photometric":
        raise ValueError("Scanline environment lighting needs a separate validated skylight provider; a background map alone is not illumination.")
    if family == "corona":
        return compile_corona_environment(plan, key, spec)
    if source.kind == "existing_map":
        if source.rotation != 0 or source.source_color_space is not None:
            raise ValueError("Inspect and edit an existing map's rotation/color settings explicitly; it may be shared.")
        image_ref = source.owner_ref
    else:
        path = Path(source.path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Environment asset not found: {path}")
        suffix = path.suffix.lower()
        if suffix not in {".exr", ".hdr"}:
            raise ValueError("HDRI creation currently accepts scene-linear EXR/HDR; use an inspected existing map for other encodings.")
        image = key + "_image"
        if family == "octane":
            plan.create(image, OCTANE_IMAGE, MAP)
            plan.set(image, "filename", str(path))
            plan.set(image, "gamma", 1.0)
            if source.source_color_space: plan.set(image, "colorSpace", source.source_color_space)
        else:
            plan.create(image, VRAY_IMAGE, MAP)
            plan.set(image, "HDRIMapName", str(path))
            plan.set(image, "mapType", VRAY_IMAGE_MAPPING["spherical"])
            plan.set(image, "color_space", 0)  # Linear samples: no input transfer curve.
            plan.set(image, "gamma", 1.0)
            plan.set(image, "horizontalRotation", math.radians(source.rotation))
            if source.source_color_space:
                raise ValueError("V-Ray input primaries override needs a verified mapping; use an inspected existing map for an explicit color space.")
        image_ref = {"created": image}
    if family == "vray":
        plan.create(key, VRAY_LIGHT, name=spec.name)
        for name, value in {"type": 1, "on": spec.enabled, "targeted": False, "normalizeColor": 0,
                            "multiplier": spec.output.value, "color_mode": 0, "color": [1., 1., 1.],
                            "texmap": image_ref, "texmap_on": True, "dome_spherical": True,
                            "dome_finite": False, "castShadows": spec.cast_shadows}.items():
            plan.set(key, name, value)
    else:
        if "environment" in plan.payload:
            raise ValueError("Only one scene environment binding can be created in a batch.")
        if not spec.enabled:
            raise ValueError("Disabled scene-environment creation is not supported; create or edit its binding explicitly.")
        plan.create(key, OCTANE_ENV, MAP)
        plan.set(key, "texture_input_type", 3)
        plan.set(key, "texture_tex", image_ref)
        plan.set(key, "power", spec.output.value)
        if source.rotation:
            plan.set(key, "rotation", [source.rotation / 360., 0.])
        plan.payload["environment"] = {"owner_ref": {"created": key}, "replace_existing": source.replace_existing}


def compile_corona_directional(plan: Plan, key: str, spec: LightSpec, scale: float):
    """CoronaSun / CoronaMoon: shapeless directional emitters that shine down the node's
    local -Z (verified by render). Intensity is Corona's own multiplier. The sun keeps
    Corona's realistic colour unless a Kelvin or RGB colour is given; the moon only has
    an RGB filter. Size multiplier, phase and sky linking stay at plugin defaults."""
    body = spec.body or "sun"
    if spec.output.unit != "renderer":
        raise ValueError("Corona sun/moon intensity is a renderer multiplier; use unit renderer.")
    if not spec.cast_shadows:
        raise ValueError("Corona lights always cast shadows; there is no per-light shadow switch.")
    color = spec.color
    if body == "sun":
        plan.create(key, CORONA_SUN, name=spec.name, matrix=world_matrix(spec, scale))
        mode = "realistic" if color is None else "kelvin" if color.kelvin is not None else "rendering_rgb"
        for name, value in {"on": spec.enabled, "targeted": False, "intensity": spec.output.value,
                            "colorMode": CORONA_SUN_COLOR_MODES[mode]}.items():
            plan.set(key, name, value)
        if mode == "kelvin": plan.set(key, "blackbodyTemperature", color.kelvin)
        elif mode == "rendering_rgb": plan.set(key, "colorDirect", list(color.rgb))
    else:
        if color is not None and color.kelvin is not None:
            raise ValueError("CoronaMoon has an RGB colour filter only; supply rgb or omit colour.")
        plan.create(key, CORONA_MOON, name=spec.name, matrix=world_matrix(spec, scale))
        for name, value in {"on": spec.enabled, "targeted": False, "intensity": spec.output.value}.items():
            plan.set(key, name, value)
        if color is not None: plan.set(key, "colorFilter", list(color.rgb))


def compile_corona_environment(plan: Plan, key: str, spec: LightSpec):
    """Corona lights the scene from the 3ds Max environment slot itself: a CoronaBitmap
    (HDRI) or an existing map such as CoronaSky is bound there directly, without a dome
    node or wrapper. The slot has no multiplier, so output stays at the explicit 1.0."""
    source = spec.environment
    if "environment" in plan.payload:
        raise ValueError("Only one scene environment binding can be created in a batch.")
    if not spec.enabled:
        raise ValueError("Disabled scene-environment creation is not supported; create or edit its binding explicitly.")
    if spec.output.value != 1:
        raise ValueError("The Corona scene environment has no multiplier; bind at 1.0 and edit the map's own intensity (CoronaSky intensityMultiplier, or a CoronaColorCorrect) explicitly.")
    if source.kind == "existing_map":
        if source.rotation != 0 or source.source_color_space is not None:
            raise ValueError("Inspect and edit an existing map's rotation/color settings explicitly; it may be shared.")
        plan.existing[key] = source.owner_ref
        plan.payload["environment"] = {"owner_ref": source.owner_ref, "replace_existing": source.replace_existing}
        return
    path = Path(source.path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Environment asset not found: {path}")
    if path.suffix.lower() not in {".exr", ".hdr"}:
        raise ValueError("HDRI creation currently accepts scene-linear EXR/HDR; use an inspected existing map for other encodings.")
    if source.rotation != 0:
        raise ValueError("Corona HDRI rotation is not a verified binding yet; rotate the created CoronaBitmap explicitly after inspecting it.")
    if source.source_color_space:
        raise ValueError("Corona input primaries override needs a verified mapping; use an inspected existing map for an explicit color space.")
    plan.create(key, CORONA_BITMAP, MAP)
    plan.set(key, "filename", str(path))
    plan.set(key, "enviroMapping", CORONA_BITMAP_MAPPING["spherical"])
    plan.set(key, "gamma", 1.0)  # Linear samples: no input transfer curve.
    plan.payload["environment"] = {"owner_ref": {"created": key}, "replace_existing": source.replace_existing}


def capabilities(requested="current", detail="summary") -> dict:
    if detail not in {"summary", "full"}: raise ValueError("detail must be summary or full")
    context = api.native("native:lighting_context", {})
    try: family, chosen = renderer(context, requested)
    except ValueError as error:
        return {"supported": False, "reason": str(error), "context": context, "fallback": "inspect_plugin_class/instance(schema_version=2), plugin_patch"}
    result = {"renderer": chosen, "provider": family, "context_token": context["context_token"],
              "meters_per_unit": context["meters_per_unit"], "color_management": context["color_management"],
              "renderers": [{"name": r["name"], "label": r["label"], "provider": RENDERER_FAMILIES.get(tuple(r["class_id"]))} for r in context["renderers"]],
              "kinds": ["area"] + (["point"] if family == "photometric" else ["environment"]) + (["directional"] if family == "corona" else []),
              **({"directional_bodies": ["sun", "moon"]} if family == "corona" else {}),
              "area_shapes": list(PHOTOMETRIC)[1:] if family == "photometric" else list(OCTANE_SHAPES) if family == "octane" else list(CORONA_SHAPES) if family == "corona" else ["rectangle", "disk", "sphere"],
              "output_units": ["cd"] if family == "photometric" else ["renderer"] if family == "octane" else list(CORONA_UNITS) if family == "corona" else list(VRAY_UNITS),
              "color_modes": ["kelvin"] if family == "octane" else ["kelvin", "rendering_rgb"],
              "environment_route": None if family == "photometric" else "scene_environment_map" if family in {"octane", "corona"} else "dome_node",
              "environment_sources": [] if family == "photometric" else ["linear_exr_hdr", "existing_map_including_procedural_sky"],
              "color_policy": {"float_images": "linear samples; no extra input gamma", "primaries": "renderer input color-space policy or explicit source_color_space where supported", "rendering_space": context["color_management"].get("rendering_space"), "exposure_changes": False},
              "verification": "development_pending_live_validation",
              "unsupported": ["automatic renderer switching", "implicit controller replacement", "automatic exposure", "unverified physical conversions"]}
    if family == "corona":
        result["notes"] = ["Corona lights always cast shadows (cast_shadows=false is refused).",
                           "Environment binds the map itself to the scene environment slot at output 1.0.",
                           "Directional kind creates CoronaSun (body sun, default) or CoronaMoon (body moon); output is Corona's multiplier, orientation is the emission direction. Sky linking, size multiplier and moon phase stay at plugin defaults."]
    if detail == "full": result["light_spec_schema"] = LightSpec.model_json_schema()
    return result


def create(specs, requested="current", unit="scene", expected_context=None) -> dict:
    specs = [x if isinstance(x, LightSpec) else LightSpec.model_validate(x) for x in specs]
    context = api.native("native:lighting_context", {})
    if expected_context is not None and context["context_token"] != expected_context:
        raise api.PluginGuardError("STALE_CONTEXT", "renderer, units, color settings or environment binding changed.")
    family, chosen = renderer(context, requested)
    plan = compile_lights(specs, context, family, unit)
    applied = api.native("native:plugin_patch", plan.payload)
    resources = {r["id"]: r for r in applied["resources"]}
    actual = []
    for item in plan.lights:
        resource = resources.get(item["id"])
        if resource is None:
            if item["id"] not in plan.existing:
                raise RuntimeError(f"Native commit returned no resource for {item['id']!r}.")
            resource = {"id": item["id"], "owner_ref": dict(plan.existing[item["id"]])}
        resource["owner_ref"] = {**resource["owner_ref"], "root_binding":
            {"node": resource["node_ref"], "scope": "base_object"} if resource.get("node_ref") else {"root": "environment"}}
        entry = {"light_ref": {"owner_ref": resource["owner_ref"], "node_ref": resource.get("node_ref"), "provider": family}}
        try: entry["state"] = summary(inspect_one(resource["owner_ref"], family))
        except Exception as error: entry.update(committed=True, inspection_error=str(error))
        actual.append(entry)
    return {"status": applied["status"], "renderer": chosen, "lights": actual, "resources": applied["resources"],
            "verification": applied["verification"], "transaction": applied["transaction"],
            "requested": [s.model_dump(exclude_none=True) for s in specs],
            "compatible_with_production": chosen["class_id"] == context["renderer"]["class_id"]}


def inspect_one(owner_ref: dict, family: str | None = None) -> dict:
    identity = api.inspect(owner_ref=owner_ref, fields=["__identity_only__"], limit=1)
    ids = tuple(identity["identity"]["class_id"])
    detected = "vray" if ids == VRAY_LIGHT else "octane" if ids in {OCTANE_LIGHT, OCTANE_ENV} else "photometric" if ids in PHOTOMETRIC.values() else "corona" if ids in {CORONA_LIGHT, CORONA_SUN, CORONA_MOON, CORONA_BITMAP, CORONA_SKY} else None
    if family is not None and family != detected:
        raise ValueError("Light reference provider does not match its actual class.")
    family = detected
    fields = {
        "vray": ["type", "on", "targeted", "castShadows", "color_mode", "color_temperature", "color", "normalizeColor", "multiplier",
                 "sizeWidth", "sizeLength", "size0", "texmap", "texmap_on", "dome_spherical", "dome_finite", "doubleSided", "invisible"],
        "photometric": ["on", "castShadows", "rgb", "useKelvin", "kelvin", "intensity", "intensityType", "useMultiplier", "multiplier",
                        "light_width", "light_length", "light_radius"],
        "octane": ["enabled", "analyticLightType", "quadAnalyticLightSize", "diskAnalyticLightSize", "sphereAnalyticLightRadius",
                   "tubeAnalyticLightCapRadius", "tubeAnalyticLightLength", "emission", "normalize", "power", "texture_tex", "texture_input_type", "rotation"],
        "corona": (["on", "shape", "intensity", "intensityUnits", "colorMode", "color", "blackbodyTemp", "texmap", "width", "height",
                    "targeted", "twosidedEmission", "visibleDirectly", "directionality"] if ids == CORONA_LIGHT
                   else ["on", "intensity", "colorMode", "colorDirect", "blackbodyTemperature", "sizeMultiplier", "targeted"] if ids == CORONA_SUN
                   else ["on", "intensity", "colorFilter", "sizeMultiplier", "phase", "targeted"] if ids == CORONA_MOON
                   else ["filename", "enviroMapping", "gamma", "colorSpace", "wAngle"] if ids == CORONA_BITMAP
                   else ["intensityMultiplier", "skyModel", "turbidity", "sunSelectionMode", "selectedSun", "cloudsEnable"]),
    }.get(family, ["__identity_only__"])
    data = api.inspect(owner_ref=identity["owner_ref"], fields=fields, limit=64)
    ids = tuple(data["identity"]["class_id"])
    values = {p["name"]: p for p in data["properties"] if p.get("value_status") == "read"}
    value = lambda name: values.get(name, {}).get("value")
    state = {"provider": family, "owner_ref": data["owner_ref"], "schema_token": data["schema_token"],
             "state_token": data["state_token"], "bindings": data["properties"]}
    if family == "vray":
        shape = next((k for k, v in VRAY_SHAPES.items() if v == value("type")), None)
        state.update(kind="environment" if shape == "environment" else "area" if shape else None,
                     shape=shape, enabled=value("on"), cast_shadows=value("castShadows"),
                     color={"kelvin": value("color_temperature")} if value("color_mode") == 1 else {"rgb": value("color"), "space": "rendering"},
                     output={"value": value("multiplier"), "unit": next((k for k, v in VRAY_UNITS.items() if v == value("normalizeColor")), None)})
        state["size"] = {"width": value("sizeLength"), "height": value("sizeWidth")} if shape == "rectangle" else {"radius": value("size0")} if shape in {"disk", "sphere"} else None
        state["source_ref"] = value("texmap")
        state["texture_enabled"] = value("texmap_on")
    elif family == "photometric":
        shape = next(k for k, v in PHOTOMETRIC.items() if v == ids)
        state.update(kind="point" if shape == "point" else "area", shape=shape, enabled=value("on"),
                     cast_shadows=value("castShadows"), output={"value": value("intensity"), "unit": "cd"},
                     color={"kelvin": value("kelvin")} if value("useKelvin") else {"rgb": value("rgb"), "space": "rendering"})
        state["size"] = {"width": value("light_width"), "height": value("light_length")} if shape == "rectangle" else {"radius": value("light_radius")} if shape != "point" else None
        if shape == "cylinder": state["size"]["length"] = value("light_length")
        state["output"]["multiplier_enabled"] = value("useMultiplier")
        state["output"]["multiplier"] = value("multiplier")
    elif family == "octane":
        if ids == OCTANE_ENV:
            state.update(kind="environment", output={"value": value("power"), "unit": "renderer"}, source_ref=value("texture_tex"))
        else:
            shape = next((k for k, v in OCTANE_SHAPES.items() if v == value("analyticLightType")), None)
            state.update(kind="area" if shape else None, shape=shape, enabled=value("enabled"), emission_ref=value("emission"))
            if value("emission") is not None and tuple(value("emission")["class_ref"]["class_id"]) == OCTANE_EMISSION:
                emission = api.inspect(owner_ref=value("emission"), fields=["power", "temperature", "normalize", "surfaceBrightness", "castShadows", "powerVT", "temperatureVT", "normalizeVT", "surfaceBrightnessVT", "castShadowsVT"], limit=25)
                emission_values = {p["name"]: p.get("value") for p in emission["properties"]}
                state.update(output={"value": emission_values.get("power"), "unit": "renderer", "normalize": emission_values.get("normalize"), "surface_brightness": emission_values.get("surfaceBrightness")}, color={"kelvin": emission_values.get("temperature")}, cast_shadows=emission_values.get("castShadows"), emission=emission)
                state["overrides"] = {k: v for k,v in emission_values.items() if k.endswith("VT") and v is not None}
            dims = value("quadAnalyticLightSize") if shape == "rectangle" else value("diskAnalyticLightSize")
            state["size"] = {"width": dims[0], "height": dims[1]} if shape == "rectangle" and dims and len(dims) == 2 else {"radius": value("sphereAnalyticLightRadius")} if shape == "sphere" else {"radius": value("tubeAnalyticLightCapRadius"), "length": value("tubeAnalyticLightLength")} if shape == "cylinder" else {"diameters": dims} if shape == "disk" else None
    elif family == "corona":
        if ids == CORONA_LIGHT:
            shape = next((k for k, v in CORONA_SHAPES.items() if v == value("shape")), None)
            mode = value("colorMode")
            unit = next((k for k, v in CORONA_UNITS.items() if v == value("intensityUnits")), "lx" if value("intensityUnits") == 3 else None)
            state.update(kind="area" if shape else None, shape=shape, enabled=value("on"), cast_shadows=True,
                         color={"kelvin": value("blackbodyTemp")} if mode == 1 else {"rgb": value("color"), "space": "rendering"} if mode == 0 else {"texmap": value("texmap")},
                         output={"value": value("intensity"), "unit": unit})
            state["size"] = ({"width": value("width"), "height": value("height")} if shape == "rectangle"
                             else {"radius": value("width"), "length": value("height")} if shape == "cylinder"
                             else {"radius": value("width")} if shape else None)
            state["two_sided"] = value("twosidedEmission")
            state["directionality"] = value("directionality")
        elif ids in {CORONA_SUN, CORONA_MOON}:
            body = "sun" if ids == CORONA_SUN else "moon"
            if body == "sun":
                mode = value("colorMode")
                color = ({"kelvin": value("blackbodyTemperature")} if mode == 1 else
                         {"rgb": value("colorDirect"), "space": "rendering"} if mode == 0 else {"realistic": True})
            else:
                color = {"rgb": value("colorFilter"), "space": "rendering", "filter": True}
            state.update(kind="directional", body=body, enabled=value("on"), cast_shadows=True, color=color,
                         output={"value": value("intensity"), "unit": "renderer"}, size_multiplier=value("sizeMultiplier"))
            if body == "moon": state["phase"] = value("phase")
        else:
            # The map itself is the environment light; there is no wrapper node.
            state.update(kind="environment",
                         output={"value": value("intensityMultiplier") if ids == CORONA_SKY else 1.0, "unit": "renderer"})
            state["environment_source"] = {"class_ref": {"superclass_id": MAP, "class_id": list(ids)},
                                           "values": {p["name"]: p.get("value") for p in data["properties"] if p.get("value_status") == "read"}}
    state["decoded"] = family is not None and state.get("kind") is not None
    state["unreadable"] = [{"name": p["name"], "status": p.get("value_status")} for p in data["properties"] if p.get("value_status") != "read"]
    state["light_token"] = {"owner_ref": data["owner_ref"], "schema_token": data["schema_token"], "state_token": data["state_token"]}
    if "emission" in state:
        state["light_token"]["emission"] = {k: state["emission"][k] for k in ("owner_ref", "schema_token", "state_token")}
    source = state.get("source_ref")
    if source:
        source_ids = tuple(source["class_ref"]["class_id"])
        source_fields = ["HDRIMapName", "color_space", "gamma", "mapType", "rgbColorSpace", "ocioColorSpace", "horizontalRotation"] if source_ids == VRAY_IMAGE else ["filename", "colorSpace", "gamma", "gammaVT", "projection", "transform"] if source_ids == OCTANE_IMAGE else []
        if source_fields:
            source_data = api.inspect(owner_ref=source, fields=source_fields, limit=25)
            state["environment_source"] = {"class_ref": source["class_ref"], "values": {p["name"]: p["value"] for p in source_data["properties"] if p.get("value_status") == "read"}}
            state["light_token"]["source"] = {k: source_data[k] for k in ("owner_ref", "schema_token", "state_token")}
        else:
            state["environment_source"] = {"class_ref": source["class_ref"], "status": "custom_graph_inspect_owner_ref"}
    return state


def summary(state: dict) -> dict:
    return {k: v for k, v in state.items() if k not in {"bindings", "emission", "schema_token", "state_token"}}


def edit(edits: list[dict], unit="scene") -> dict:
    if not edits or len(edits) > 32:
        raise ValueError("Supply 1..32 light edits.")
    context = api.native("native:lighting_context", {})
    scale = distance_scale(unit, context)
    payload = {"version": 1, "expected_context": context["context_token"], "edits": [], "guards": []}
    inspected = []
    for item in edits:
        if set(item) - {"light_ref", "expected_light", "changes", "sharing"}:
            raise ValueError("Unknown light edit field.")
        ref = item["light_ref"]
        state = inspect_one(ref["owner_ref"], ref.get("provider"))
        if item.get("expected_light") != state["light_token"]:
            raise api.PluginGuardError("STALE_LIGHT", "inspect again; the light or linked emission has changed.")
        token = state["light_token"]
        for guard in [token, *[token[k] for k in ("emission", "source") if k in token]]:
            payload["guards"].append({"owner_ref": guard["owner_ref"], "expected_schema": guard["schema_token"], "expected_state": guard["state_token"]})
        if not state["decoded"]:
            raise ValueError("This light cannot be decoded by a supported provider.")
        changes = item["changes"]
        if not changes or set(changes) - {"color", "output", "enabled", "cast_shadows", "size"}:
            raise ValueError("Change color, output, enabled, cast_shadows or the full size of the existing shape.")
        if item.get("sharing") not in {None, "all_instances"}:
            raise ValueError("sharing must be omitted or all_instances.")
        family, shape = state["provider"], state.get("shape")

        def assign(name, value, emission=False):
            if emission and "emission" not in state:
                raise ValueError("This emission graph is not the supported Black Body class; inspect and edit its exact properties.")
            if emission and state.get("overrides", {}).get(name + "VT") is not None:
                raise ValueError(f"CONTROLLED_PARAMETER: {name} is overridden by a texture; edit that graph explicitly.")
            owner = state["emission"] if emission else state
            properties = owner["properties"] if emission else owner["bindings"]
            found = [p for p in properties if p["name"] == name and p.get("value_status") == "read"]
            if len(found) != 1:
                raise ValueError(f"SCHEMA_CONFLICT: cannot edit {name!r} without an exact readable binding.")
            payload["edits"].append({"owner_ref": owner["owner_ref"], "property_ref": found[0]["property_ref"],
                                      "value": value, "expected_schema": owner["schema_token"],
                                      "expected_state": owner["state_token"], "sharing": item.get("sharing", "")})

        for name in ("enabled", "cast_shadows"):
            if name not in changes:
                continue
            if type(changes[name]) is not bool:
                raise ValueError(f"{name} must be boolean.")
            if family in {"octane", "corona"} and state["kind"] == "environment":
                raise ValueError("Environment binding enable/shadow changes are not emitter parameters.")
            if family == "corona" and name == "cast_shadows":
                if changes[name] is False: raise ValueError("Corona lights always cast shadows; there is no per-light shadow switch.")
                continue
            assign("enabled" if family == "octane" and name == "enabled" else "on" if name == "enabled" else "castShadows",
                   changes[name], emission=family == "octane" and name == "cast_shadows")
        if "output" in changes:
            output = LightOutput.model_validate(changes["output"])
            if family == "vray":
                if output.unit not in VRAY_UNITS or state["kind"] == "environment" and output.unit != "renderer":
                    raise ValueError("Unsupported output unit for this V-Ray emitter.")
                assign("normalizeColor", VRAY_UNITS[output.unit]); assign("multiplier", output.value)
            elif family == "photometric":
                if output.unit != "cd": raise ValueError("Photometric intensity accepts cd.")
                assign("useMultiplier", False); assign("intensityType", 1); assign("intensity", output.value)
            elif family == "corona":
                if state["kind"] == "environment":
                    if output.unit != "renderer" or "intensityMultiplier" not in {p["name"] for p in state["bindings"]}:
                        raise ValueError("Only a CoronaSky environment exposes an intensity multiplier; a CoronaBitmap environment has none.")
                    assign("intensityMultiplier", output.value)
                elif state["kind"] == "directional":
                    if output.unit != "renderer": raise ValueError("Corona sun/moon intensity is a renderer multiplier.")
                    assign("intensity", output.value)
                else:
                    if output.unit not in CORONA_UNITS: raise ValueError("Corona output accepts renderer, lm or cd.")
                    assign("intensityUnits", CORONA_UNITS[output.unit]); assign("intensity", output.value)
            else:
                if output.unit != "renderer": raise ValueError("Octane output accepts renderer power.")
                assign("power", output.value, emission=state["kind"] != "environment")
        if "color" in changes:
            if state["kind"] == "environment": raise ValueError("Environment color comes from its source map.")
            color = LightColor.model_validate(changes["color"])
            if family == "vray":
                assign("color_mode", int(color.kelvin is not None))
                assign("color_temperature" if color.kelvin is not None else "color", color.kelvin if color.kelvin is not None else list(color.rgb))
            elif family == "photometric":
                assign("useKelvin", color.kelvin is not None)
                assign("kelvin" if color.kelvin is not None else "rgb", color.kelvin if color.kelvin is not None else list(color.rgb))
            elif family == "corona" and state.get("body") == "sun":
                assign("colorMode", CORONA_SUN_COLOR_MODES["kelvin" if color.kelvin is not None else "rendering_rgb"])
                assign("blackbodyTemperature" if color.kelvin is not None else "colorDirect", color.kelvin if color.kelvin is not None else list(color.rgb))
            elif family == "corona" and state.get("body") == "moon":
                if color.kelvin is not None: raise ValueError("CoronaMoon has an RGB colour filter only.")
                assign("colorFilter", list(color.rgb))
            elif family == "corona":
                assign("colorMode", CORONA_COLOR_MODES["kelvin" if color.kelvin is not None else "rendering_rgb"])
                assign("blackbodyTemp" if color.kelvin is not None else "color", color.kelvin if color.kelvin is not None else list(color.rgb))
            else:
                if color.kelvin is None: raise ValueError("This Octane emission provider accepts Kelvin.")
                assign("temperature", color.kelvin, emission=True)
        if "size" in changes:
            if state["kind"] != "area": raise ValueError("Only area emitters have a size.")
            # Reuse the construction contract to reject partial/mismatched sizes.
            size = LightSpec.model_validate({"kind": "area", "shape": shape, "size": changes["size"],
                    "orientation": {"direction": [0, 0, -1]}, "output": {"value": 1, "unit": "renderer"}}).size
            dims = {k: v*scale for k, v in size.model_dump(exclude_none=True).items()}
            if family == "vray":
                if shape == "rectangle": assign("sizeLength", dims["width"]); assign("sizeWidth", dims["height"])
                else: assign("size0", dims["radius"])
            elif family == "photometric":
                if shape == "rectangle": assign("light_width", dims["width"]); assign("light_length", dims["height"])
                else:
                    assign("light_radius", dims["radius"])
                    if shape == "cylinder": assign("light_length", dims["length"])
            elif family == "corona":
                if shape == "rectangle": assign("width", dims["width"]); assign("height", dims["height"])
                else:
                    assign("width", dims["radius"])
                    if shape == "cylinder": assign("height", dims["length"])
            elif shape == "rectangle": assign("quadAnalyticLightSize", [dims["width"], dims["height"]])
            elif shape == "disk": assign("diskAnalyticLightSize", [2*dims["radius"], 2*dims["radius"]])
            elif shape == "sphere": assign("sphereAnalyticLightRadius", dims["radius"])
            else: assign("tubeAnalyticLightCapRadius", dims["radius"]); assign("tubeAnalyticLightLength", dims["length"])
        inspected.append((state["owner_ref"], family))
    applied = api.native("native:plugin_patch", payload)
    # Native commit has already verified every assignment. Preserve that status
    # if optional semantic follow-up inspection encounters a plugin getter error.
    result = {"status": applied["status"], "verification": applied["verification"], "transaction": applied["transaction"], "lights": []}
    for ref, family in inspected:
        try: result["lights"].append(summary(inspect_one(ref, family)))
        except Exception as error: result["lights"].append({"owner_ref": ref, "inspection_error": str(error), "committed": True})
    return result
