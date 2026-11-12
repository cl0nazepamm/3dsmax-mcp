"""One engine for lights, sun, environment, camera, render settings and renders on any renderer.

Everything renderer-specific is data in a preset (_presets.py). This module turns neutral
specs into one native plugin_patch plan through the active preset's recipes (creation,
guarded edits, node transforms and the environment binding in one undo step, each value
verified by typed readback), and reads existing objects back through the same bindings.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from ...core import jobs
from ..inspection import _plugin_schema as api
from . import _presets as presets

# What a spec may say besides the recipe's own fields.
ENGINE_KEYS = {"name", "kind", "position", "aim_at", "direction", "up", "elevation", "azimuth", "intensity",
               "unit", "native", "replace", "sharing", "map"}
UNIT_ALIASES = {"cd/m2": "nits", "cd/m²": "nits", "nit": "nits", "lumen": "lm", "lumens": "lm", "candela": "cd",
                "candelas": "cd", "watt": "W", "watts": "W", "w": "W", "multiplier": "renderer", "x": "renderer"}
PHOTOMETRIC = ("lm", "cd", "nits")
FLOATS = {"float", "worldUnits", "angle", "percent", "colorChannel"}
INTS = {"int", "index", "radioIndex", "enum", "time"}
VECTORS = {"point2": 2, "point3": 3, "color": 3, "point4": 4, "color4": 4}
REFS = {"texturemap", "material", "node", "refTarget"}
DISTANCE_UNITS = {"mm": .001, "cm": .01, "m": 1., "in": .0254, "ft": .3048}
DEFAULT_NAMES = {"sun": "Sun", "moon": "Moon", "hdri": "Environment", "camera": "Camera"}
IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
MAX_LISTED = 40


def fail(code: str, message: str) -> Any:
    raise api.PluginGuardError(code, message)


# ── Vectors ──────────────────────────────────────────────────────

def _vector(value: Any, what: str) -> list[float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value)):
        fail("BAD_PARAM", f"{what} is three finite numbers (x, y, z).")
    return [float(x) for x in value]


def _normalize(vector: list[float], what: str) -> list[float]:
    length = math.sqrt(sum(v * v for v in vector))
    if length < 1e-9:
        fail("BAD_PARAM", f"{what} has no length.")
    return [v / length for v in vector]


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def look_rows(direction: list[float], up: list[float] | None = None) -> list[list[float]]:
    """Rotation rows of a node whose local -Z points along `direction` (lights and cameras)."""
    z = [-x for x in _normalize(direction, "direction")]
    # World Z is the roll reference, with Y at the poles.
    hint = up if up is not None else ([0.0, 1.0, 0.0] if abs(z[2]) > .999 else [0.0, 0.0, 1.0])
    x = _normalize(_cross(hint, z), "up (it is parallel to the direction)")
    return [x, _normalize(_cross(z, x), "up"), z]


def sun_travel(elevation: float, azimuth: float) -> list[float]:
    """The direction sunlight travels for a sun `elevation` degrees above the horizon and
    `azimuth` degrees clockwise from +Y (north) toward +X (east)."""
    el, az = math.radians(elevation), math.radians(azimuth)
    return [-math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), -math.sin(el)]


# ── Intensity ────────────────────────────────────────────────────

def _unit(name: Any) -> str:
    if not isinstance(name, str) or not name:
        fail("BAD_PARAM", "unit is a name such as 'lm', 'cd', 'nits', 'W' or 'renderer'.")
    return UNIT_ALIASES.get(name, UNIT_ALIASES.get(name.lower(), name))


def photometric(value: float, source: str, target: str, kind: str, dims: dict[str, float]) -> float | None:
    """`value` from one of lm / cd / nits to another for an ideal emitter of this shape.

    A flat emitter (rectangle, disk) is one-sided and Lambertian, a sphere and a point
    radiate evenly, a cylinder is a Lambertian tube. dims are in meters. None when the shape
    gives no answer (a point has no area, so no luminance).
    """
    if source == target:
        return value
    if kind in ("rectangle", "disk"):
        area = dims.get("width", 0) * dims.get("height", 0) if kind == "rectangle" else math.pi * dims.get("radius", 0) ** 2
        lumens_per_candela = math.pi
    elif kind == "sphere":
        area, lumens_per_candela = math.pi * dims.get("radius", 0) ** 2, 4 * math.pi
    elif kind == "cylinder":
        area, lumens_per_candela = 2 * dims.get("radius", 0) * dims.get("length", 0), math.pi ** 2
    elif kind == "point":
        area, lumens_per_candela = 0.0, 4 * math.pi
    else:
        return None
    if "nits" in (source, target) and area <= 0:
        return None
    candela = value / lumens_per_candela if source == "lm" else value * area if source == "nits" else value
    return candela * lumens_per_candela if target == "lm" else candela / area if target == "nits" else candela


# ── Parameter values ─────────────────────────────────────────────

def _element(prop: dict[str, Any]) -> str:
    kind = prop["type"]
    return kind[:-2] if kind.endswith("[]") else kind


def _scalar(kind: str, value: Any, where: str, neutral: bool) -> Any:
    if kind == "bool":
        if type(value) is not bool:
            fail("BAD_PARAM", f"{where} takes true or false.")
        return value
    number = isinstance(value, (int, float)) and type(value) is not bool and math.isfinite(value)
    if kind in INTS:
        if not number or int(value) != value:
            fail("BAD_PARAM", f"{where} takes a whole number.")
        return int(value)
    if kind in FLOATS:
        if not number:
            fail("BAD_PARAM", f"{where} takes a number.")
        return math.radians(value) if neutral and kind == "angle" else float(value)
    if kind in ("string", "filename"):
        if not isinstance(value, str):
            fail("BAD_PARAM", f"{where} takes text.")
        return value
    return fail("SCHEMA_CONFLICT", f"{where}: a {kind} parameter has no typed writer.")


def native_value(prop: dict[str, Any], value: Any, where: str, neutral: bool) -> Any:
    """A value in the form plugin_patch writes into `prop`; neutral angles are degrees."""
    kind = _element(prop)
    if kind in REFS:
        return value
    if prop["type"].endswith("[]") or kind in VECTORS:
        size = VECTORS.get(kind)
        if not isinstance(value, (list, tuple)) or (size is not None and len(value) != size and not prop["type"].endswith("[]")):
            fail("BAD_PARAM", f"{where} takes {size or 'a list of'} numbers.")
        inner = "float" if kind in VECTORS else kind
        return [_scalar(inner, item, where, neutral) for item in value]
    return _scalar(kind, value, where, neutral)


def neutral_value(prop: dict[str, Any], raw: Any) -> Any:
    if _element(prop) == "angle" and isinstance(raw, (int, float)):
        return math.degrees(raw)
    return raw


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-5 * max(1.0, abs(a), abs(b))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


# ── Context and presets ──────────────────────────────────────────

def context() -> dict[str, Any]:
    return api.native("native:lighting_context", {})


def preset_of(renderer: dict[str, Any] | None) -> dict[str, Any] | None:
    return presets.for_renderer(renderer["class_id"]) if renderer else None


def active_preset(ctx: dict[str, Any]) -> dict[str, Any]:
    renderer = ctx.get("renderer")
    if not renderer:
        fail("UNSUPPORTED", "No production renderer is assigned.")
    preset = preset_of(renderer)
    if preset is None:
        served = sorted(p["id"] for p in presets.all_presets().values() if p["renderers"])
        fail("UNSUPPORTED", f"No renderer preset serves {renderer['label']} (presets: {', '.join(served)}). "
             f"Switch with render_setup(renderer=...), add a preset file to {presets.user_dir()}, or edit "
             "its objects by their own parameters through sdk and plugin_patch.")
    return preset


def recipe_of(preset: dict[str, Any], kind: str) -> dict[str, Any]:
    recipe = preset["recipes"].get(kind)
    if recipe is None:
        fail("UNSUPPORTED", f"{preset['label']} has no '{kind}' in its preset. It offers: "
             f"{', '.join(sorted(preset['recipes']))}.")
    return recipe


def distance_scale(unit: str, ctx: dict[str, Any]) -> float:
    if unit == "scene":
        return 1.0
    if unit not in DISTANCE_UNITS or ctx["meters_per_unit"] <= 0:
        fail("BAD_PARAM", "distance_unit is scene, mm, cm, m, in or ft.")
    return DISTANCE_UNITS[unit] / ctx["meters_per_unit"]


class Schemas:
    """Class schemas (parameter names, types and IDs), read once per call."""

    def __init__(self) -> None:
        self._classes: dict[tuple[int, tuple[int, int]], dict[str, Any]] = {}

    def of(self, spec: dict[str, Any]) -> dict[str, Any]:
        key = (presets.SUPERCLASSES[spec["super"]], tuple(spec["class"]))
        if key not in self._classes:
            self._classes[key] = api.all_properties(class_ref={"superclass_id": key[0], "class_id": list(key[1])})
        return self._classes[key]

    @staticmethod
    def prop(schema: dict[str, Any], name: str) -> dict[str, Any]:
        found = [p for p in schema["properties"] if p["name"] == name]
        # A descriptor repeated identically (FStormSunLight's targeted) is one parameter.
        found = [p for i, p in enumerate(found) if p["property_ref"] not in [q["property_ref"] for q in found[:i]]]
        if len(found) != 1:
            fail("SCHEMA_CONFLICT", f"{schema['identity']['label']} has {len(found)} parameters named {name!r}; "
                 "the preset no longer matches this plugin version.")
        if found[0].get("read_only"):
            fail("SCHEMA_CONFLICT", f"{schema['identity']['label']}.{name} is read-only.")
        return found[0]


# ── Recipes ──────────────────────────────────────────────────────

def recipe_params(recipe: dict[str, Any]) -> dict[str, set[str]]:
    """Every parameter a recipe reads or writes, per object."""
    objects, main = recipe["objects"], presets.main_object(recipe)
    used: dict[str, set[str]] = {name: set() for name in objects}

    def add(name: str) -> None:
        obj, param = presets.split_param(name, objects, main)
        used[obj].add(param)

    for name in list(recipe.get("set") or ()) + list(recipe.get("match") or ()):
        add(name)
    bindings = list((recipe.get("fields") or {}).values()) + list(((recipe.get("intensity") or {}).get("units") or {}).values())
    for raw in bindings:
        binding = presets.binding_of(raw)
        if "param" in binding:
            add(binding["param"])
        for name in binding.get("with") or ():
            add(name)
    return used


def _links(recipe: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """part -> (object, parameter) that holds it, from the recipe's "@part" constants."""
    objects, main = recipe["objects"], presets.main_object(recipe)
    found = {}
    for name, value in (recipe.get("set") or {}).items():
        if isinstance(value, str) and value.startswith("@") and ":" not in value:
            found[value[1:]] = presets.split_param(name, objects, main)
    return found


def _environment_part(recipe: dict[str, Any], part: str, environment: dict[str, Any] | None) -> dict[str, Any] | None:
    """The scene's environment map as this recipe's part, when the recipe binds that part there."""
    if recipe.get("environment") != "@" + part or not environment:
        return None
    same = tuple(environment["class_ref"]["class_id"]) == tuple(recipe["objects"][part]["class"])
    return {"root": "environment"} if same else None


def _field_read(recipe: dict[str, Any], raw_binding: Any, values: dict[str, dict[str, Any]],
                schemas: dict[str, dict[str, Any]]) -> tuple[bool, Any]:
    """(active, neutral value) of one binding on existing objects."""
    binding = presets.binding_of(raw_binding)
    objects, main = recipe["objects"], presets.main_object(recipe)
    if "fixed" in binding:
        return True, binding["fixed"]
    for name, wanted in (binding.get("with") or {}).items():
        obj, param = presets.split_param(name, objects, main)
        if not _same(values.get(obj, {}).get(param), wanted):
            return False, None
    obj, param = presets.split_param(binding["param"], objects, main)
    if param not in values.get(obj, {}):
        return False, None
    raw = values[obj][param]
    index = binding.get("index")
    if index is not None:
        if not isinstance(raw, (list, tuple)) or not raw:
            return False, None
        if index == "all":
            if not all(_same(item, raw[0]) for item in raw):
                return True, None  # the elements differ: no single neutral value
            raw = raw[0]
        elif index < len(raw):
            raw = raw[index]
        else:
            return False, None
    value = neutral_value(Schemas.prop(schemas[obj], param), raw)
    if "values" in binding:
        names = [name for name, number in binding["values"].items() if number == value]
        return True, names[0] if names else None
    if binding.get("invert") and isinstance(value, bool):
        value = not value
    scale = binding.get("scale")
    if scale and isinstance(value, (int, float)) and not isinstance(value, bool):
        value = value / scale
    return True, value


def decode(recipe: dict[str, Any], values: dict[str, dict[str, Any]], schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Neutral fields of existing objects, read through the recipe's bindings."""
    out: dict[str, Any] = {}
    for field, raw in (recipe.get("fields") or {}).items():
        active, value = _field_read(recipe, raw, values, schemas)
        if active and value is not None:
            out[field] = value
    intensity = recipe.get("intensity")
    if intensity:
        ranked = sorted(intensity["units"].items(), key=lambda item: "with" not in presets.binding_of(item[1]))
        for unit, raw in ranked:
            active, value = _field_read(recipe, raw, values, schemas)
            if active and value is not None:
                out["intensity"], out["unit"] = value, unit
                break
    return out


def matches(recipe: dict[str, Any], class_id: Any, values: dict[str, Any] | None = None) -> bool:
    """Whether an object of `class_id` (with these main-object values, when known) is this recipe's."""
    main = recipe["objects"][presets.main_object(recipe)]
    if tuple(main["class"]) != tuple(class_id):
        return False
    return values is None or all(_same(values.get(name), wanted) for name, wanted in (recipe.get("match") or {}).items())


# ── The plan ─────────────────────────────────────────────────────

class Plan:
    """A plugin_patch payload under construction; a later write to a parameter replaces an earlier one."""

    def __init__(self, ctx: dict[str, Any]) -> None:
        self.ctx = ctx
        self.creates: list[dict[str, Any]] = []
        self.transforms: list[dict[str, Any]] = []
        self.guards: list[dict[str, Any]] = []
        self.environment: dict[str, Any] | None = None
        self._edits: dict[str, dict[str, Any]] = {}

    def create(self, ident: str, spec: dict[str, Any], schema: dict[str, Any], name: str = "",
               placement: dict[str, Any] | None = None) -> None:
        item = {"id": ident, "class_ref": {"superclass_id": presets.SUPERCLASSES[spec["super"]], "class_id": list(spec["class"])},
                "expected_schema": schema["schema_token"], **(placement or {})}
        if name:
            item["name"] = name
        self.creates.append(item)

    def write(self, owner: Any, prop: dict[str, Any], value: Any, sharing: str = "") -> None:
        """owner: the id of an object created in this plan, or an inspected existing object."""
        if isinstance(owner, str):
            edit = {"owner_ref": {"created": owner}, "property_ref": prop["property_ref"], "value": value}
            key = owner
        else:
            edit = {"owner_ref": owner["owner_ref"], "property_ref": prop["property_ref"], "value": value,
                    "expected_schema": owner["schema_token"], "expected_state": owner["state_token"], "sharing": sharing}
            key = str(owner["owner_ref"].get("handle"))
        self._edits[f"{key}:{prop['property_ref']['block_id']}:{prop['property_ref']['param_id']}"] = edit

    def guard(self, owner: dict[str, Any]) -> None:
        self.guards.append({"owner_ref": owner["owner_ref"], "expected_schema": owner["schema_token"],
                            "expected_state": owner["state_token"]})

    @property
    def empty(self) -> bool:
        return not (self.creates or self._edits or self.transforms or self.environment)

    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {"version": 1, "expected_context": self.ctx["context_token"], "creates": self.creates,
                                "edits": list(self._edits.values()), "transforms": self.transforms,
                                "guards": self.guards}
        if self.environment is not None:
            data["environment"] = self.environment
        return data


def _inspect(owner_ref: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    data = api.inspect(owner_ref=owner_ref, fields=fields or ["__identity_only__"], limit=256)
    data["values"] = {p["name"]: p.get("value") for p in data["properties"] if p.get("value_status") == "read"}
    return data


def find_node(name: str) -> dict[str, Any] | None:
    """The base object of the node called `name` (identity and owner_ref), or None."""
    try:
        return api.inspect(owner_ref={"node": {"name": name}, "scope": "base_object"}, fields=["__identity_only__"], limit=1)
    except Exception as exc:  # noqa: BLE001 - the bridge reports a missing node as an error payload
        if "NOT_FOUND" in str(exc):
            return None
        raise


def _matrix_read(node: dict[str, Any]) -> list[list[float]]:
    """A node's world matrix rows, read exactly (MAXScript prints six digits by default)."""
    from ...core.maxscript import node_by_name
    from ...server import client

    ref = f"(getAnimByHandle {int(node['handle'])})" if node.get("handle") else node_by_name(node["name"])
    script = (f"(local m = {ref}.transform; local s = \"\"; for r in #(m.row1, m.row2, m.row3, m.row4) do "
              "for v in #(r.x, r.y, r.z) do s += (formattedPrint v format:\".9g\") + \" \"; s)")
    numbers = [float(x) for x in str(client.send_command(script, cmd_type="maxscript").get("result", "")).split()]
    if len(numbers) != 12:
        fail("READ_FAILED", "Could not read the node's transform.")
    return [numbers[i:i + 3] for i in range(0, 12, 3)]


class Item:
    """One thing a call asks for: a recipe and the caller's values, created or updated."""

    def __init__(self, key: str, kind: str, recipe: dict[str, Any], spec: dict[str, Any], name: str) -> None:
        self.key, self.kind, self.recipe, self.spec, self.name = key, kind, recipe, spec, name
        self.main = presets.main_object(recipe)
        self.is_node = recipe["objects"][self.main]["super"] in ("light", "camera")
        self.existing: dict[str, dict[str, Any]] = {}   # object -> inspected state
        self.node: dict[str, Any] | None = None        # {handle, name} once known
        self.notes: list[str] = []
        self.created = False
        self.bound = False  # an existing map bound as the environment, as it is

    def ident(self, obj: str) -> str:
        return f"{self.key}.{obj}"

    def owner(self, obj: str) -> Any:
        return self.existing.get(obj) or self.ident(obj)

    def owner_ref(self, obj: str) -> dict[str, Any]:
        return self.existing[obj]["owner_ref"] if obj in self.existing else {"created": self.ident(obj)}


class Compiler:
    def __init__(self, ctx: dict[str, Any], preset: dict[str, Any], unit: str = "scene") -> None:
        self.ctx, self.preset = ctx, preset
        self.scale = distance_scale(unit, ctx)
        self.schemas = Schemas()
        self.plan = Plan(ctx)
        self.items: list[Item] = []

    # -- existing objects -------------------------------------------------

    def _load_existing(self, item: Item, main_ref: dict[str, Any], extra: dict[str, set[str]]) -> None:
        """Inspect the main object and the parts it links, with every parameter the recipe may touch."""
        used = recipe_params(item.recipe)
        for obj, names in extra.items():
            used[obj] |= names
        item.existing[item.main] = _inspect(main_ref, sorted(used[item.main]))
        links = _links(item.recipe)
        pending = [part for part in item.recipe["objects"] if part != item.main]
        while pending:
            part = pending.pop(0)
            bound = _environment_part(item.recipe, part, self.ctx.get("environment"))
            if bound is not None:  # the part is found where it is bound, not through a link from the main object
                item.existing[part] = _inspect(bound, sorted(used[part]))
                continue
            if item.recipe.get("environment") == "@" + part:
                item.existing[part] = {"missing": True, "values": {}}
                continue
            holder, param = links.get(part, (None, None))
            if holder not in item.existing:
                if holder is None or holder not in item.recipe["objects"]:
                    fail("SCHEMA_CONFLICT", f"The preset does not say where '{part}' is linked.")
                pending.append(part)
                continue
            ref = item.existing[holder]["values"].get(param)
            wanted = tuple(item.recipe["objects"][part]["class"])
            if not isinstance(ref, dict) or tuple(ref.get("class_ref", {}).get("class_id", ())) != wanted:
                item.existing[part] = {"missing": True, "values": {}}
                continue
            item.existing[part] = _inspect(ref, sorted(used[part]))

    def _values(self, item: Item) -> dict[str, dict[str, Any]]:
        return {obj: state.get("values", {}) for obj, state in item.existing.items()}

    def _schemas(self, item: Item) -> dict[str, dict[str, Any]]:
        return {obj: self.schemas.of(spec) for obj, spec in item.recipe["objects"].items()}

    # -- writes -----------------------------------------------------------

    def _collect(self, item: Item, creating: bool) -> tuple[dict[tuple[str, str], tuple[Any, bool]],
                                                            dict[tuple[str, str], dict[Any, Any]]]:
        """(whole-parameter writes, element writes) for an item; each value with whether it is neutral."""
        recipe, spec = item.recipe, item.spec
        objects, fields = recipe["objects"], recipe.get("fields") or {}
        whole: dict[tuple[str, str], tuple[Any, bool]] = {}
        partial: dict[tuple[str, str], dict[Any, Any]] = {}

        def target(name: str) -> tuple[str, str]:
            return presets.split_param(name, objects, item.main)

        if creating:
            for name, value in (recipe.get("set") or {}).items():
                if isinstance(value, str) and value.startswith("@"):
                    part, _, facet = value[1:].partition(":")
                    value = item.owner_ref(part)
                    if facet == "node":  # the scene node of an object created in this plan
                        value = {"created": value["created"] + ":node"}
                whole[target(name)] = (value, False)

        # A recipe may bind a placement word itself (a sun whose elevation is a parameter).
        given = {k: v for k, v in spec.items() if (k not in ENGINE_KEYS or k in fields) and v is not None}
        unknown = sorted(set(given) - set(fields))
        if unknown:
            fail("BAD_PARAM", f"{self.preset['label']} '{item.kind}' has no {', '.join(unknown)}. Its fields: "
                 f"{', '.join(sorted(fields)) or 'none'}; plus name, position, aim_at/direction, intensity/unit, native.")
        if "kelvin" in given and "rgb" in given:
            fail("BAD_PARAM", "Give kelvin or rgb, not both.")
        if creating:
            missing = [name for name in recipe.get("requires") or () if name not in given]
            if missing:
                fail("BAD_PARAM", f"A {item.kind} needs {', '.join(missing)}.")
            defaults = dict(recipe.get("defaults") or {})
            if "rgb" in given:
                defaults.pop("kelvin", None)
            given = {**defaults, **given}

        def bind(raw: Any, value: Any, label: str, scaled: bool) -> None:
            binding = presets.binding_of(raw)
            if "fixed" in binding:
                if not _same(value, binding["fixed"]):
                    fail("UNSUPPORTED", binding.get("why") or f"{label} can only be {binding['fixed']!r} here.")
                return
            if "values" in binding:
                if value not in binding["values"]:
                    fail("BAD_PARAM", f"{label} is one of {', '.join(binding['values'])}.")
                value = binding["values"][value]
            if "extensions" in binding:
                value = _file(value, binding["extensions"], label)
            number = isinstance(value, (int, float)) and not isinstance(value, bool)
            if number:
                if "min" in binding and value < binding["min"] or "max" in binding and value > binding["max"]:
                    fail("BAD_PARAM", f"{label} must be between {binding.get('min', '-inf')} and {binding.get('max', 'inf')}.")
                if scaled:
                    value = value * self.scale
                value = value * binding.get("scale", 1) if "scale" in binding else value
            if binding.get("invert"):
                if type(value) is not bool:
                    fail("BAD_PARAM", f"{label} takes true or false.")
                value = not value
            for name, wanted in (binding.get("with") or {}).items():
                whole[target(name)] = (wanted, False)
            key = target(binding["param"])
            if "index" in binding:
                partial.setdefault(key, {"count": binding.get("count", 2)})[binding["index"]] = value
            else:
                whole[key] = (value, True)

        for field, value in given.items():
            prop_obj, prop_name = target(presets.binding_of(fields[field]).get("param", ""))
            lengthy = False
            if "param" in presets.binding_of(fields[field]):
                lengthy = _element(Schemas.prop(self.schemas.of(objects[prop_obj]), prop_name)) == "worldUnits"
            bind(fields[field], value, field, lengthy)

        chosen = self._intensity(item, creating, given)
        if chosen is not None:
            bind(chosen[0], chosen[1], "intensity", False)

        for name, value in (spec.get("native") or {}).items():
            whole[target(name)] = (value, False)
        return whole, partial

    def _intensity(self, item: Item, creating: bool, given: dict[str, Any]) -> tuple[Any, float] | None:
        """(unit binding, native value) for the item's intensity, or None when nothing is to be written."""
        rule, spec = item.recipe.get("intensity"), item.spec
        asked = spec.get("intensity")
        if rule is None:
            if asked is not None:
                fail("UNSUPPORTED", f"{self.preset['label']} '{item.kind}' has no intensity.")
            return None
        units, default = rule["units"], rule.get("default")
        if asked is None:
            if spec.get("unit") is not None:
                fail("BAD_PARAM", "unit needs an intensity.")
            return (units[default["unit"]], default["value"]) if creating and default else None
        if isinstance(asked, dict):
            # {"lm": 800, "fstorm": 30}: the first unit or preset id this renderer can take.
            candidates = [(value, default["unit"] if key == self.preset["id"] and default else _unit(key))
                          for key, value in asked.items()]
        else:
            candidates = [(asked, _unit(spec["unit"]) if spec.get("unit") is not None else (default or {}).get("unit"))]
        dims = self._dims_m(item, given)
        for value, unit in candidates:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
                fail("BAD_PARAM", "intensity is a number of zero or more.")
            if unit == "renderer" and "renderer" not in units and default:
                unit = default["unit"]  # 'renderer' always means the renderer's own number
            if unit in units:
                return units[unit], value
            for native in units:
                if unit in PHOTOMETRIC and native in PHOTOMETRIC:
                    converted = photometric(value, unit, native, item.kind, dims)
                    if converted is not None:
                        item.notes.append(f"intensity {value:g} {unit} written as {converted:.6g} {native} "
                                          f"(ideal {item.kind} emitter)")
                        return units[native], converted
            approx = (rule.get("approx") or {}).get(unit)
            if approx:
                item.notes.append(f"intensity {value:g} {unit} written as {value * approx['factor']:.6g} "
                                  f"{approx['unit']}, approximately: {approx['basis']}")
                return units[approx["unit"]], value * approx["factor"]
        offered = set(units) | set(rule.get("approx") or {})
        # The photometric units convert into each other through the shape, but only into a
        # photometric unit the renderer itself takes: an approximate one (Octane's lm) is no bridge.
        if set(units) & set(PHOTOMETRIC):
            offered |= {"lm", "cd"} | (set() if item.kind == "point" else {"nits"})
        asked_units = ", ".join(str(unit) for _, unit in candidates)
        return fail("UNSUPPORTED", f"{self.preset['label']} '{item.kind}' takes intensity in {', '.join(sorted(offered))}, "
                    f"not {asked_units}. Use one of those, or intensity={{'lm': ..., '{self.preset['id']}': ...}} "
                    "to give this renderer its own number.")

    def _dims_m(self, item: Item, given: dict[str, Any]) -> dict[str, float]:
        """The emitter's size in meters, from the call or the existing light."""
        current = decode(item.recipe, self._values(item), self._schemas(item)) if item.existing else {}
        meters = self.ctx["meters_per_unit"]
        dims = {}
        for name in ("width", "height", "radius", "length"):
            if isinstance(given.get(name), (int, float)):
                dims[name] = given[name] * self.scale * meters
            elif isinstance(current.get(name), (int, float)):
                dims[name] = current[name] * meters
        return dims

    def _emit(self, item: Item, creating: bool) -> None:
        whole, partial = self._collect(item, creating)
        values = self._values(item)
        sharing = item.spec.get("sharing") or ""
        if sharing not in ("", "all_instances"):
            fail("BAD_PARAM", "sharing is omitted or 'all_instances'.")
        for (obj, param), elements in partial.items():
            prop = Schemas.prop(self.schemas.of(item.recipe["objects"][obj]), param)
            size = VECTORS.get(_element(prop)) or elements.pop("count")
            elements.pop("count", None)
            current = values.get(obj, {}).get(param)
            vector = list(current) if isinstance(current, (list, tuple)) and len(current) == size else [0.0] * size
            for index, value in elements.items():
                for i in (range(size) if index == "all" else [index]):
                    if i >= size:
                        fail("SCHEMA_CONFLICT", f"{param} has {size} elements; the preset writes element {i}.")
                    vector[i] = value
            whole[(obj, param)] = (vector, True)
        for (obj, param), (value, neutral) in whole.items():
            if item.existing.get(obj, {}).get("missing"):
                fail("UNSUPPORTED", f"'{item.name}' links a different {obj} than this preset builds; edit that "
                     "object by its own parameters.")
            prop = Schemas.prop(self.schemas.of(item.recipe["objects"][obj]), param)
            raw = native_value(prop, value, f"{item.kind}.{param}", neutral)
            if obj in item.existing and param in values.get(obj, {}) and _same(values[obj][param], raw):
                continue  # already so: nothing to write, and nothing to trip a controller guard
            self.plan.write(item.owner(obj), prop, raw, sharing)

    # -- placement --------------------------------------------------------

    def _direction(self, spec: dict[str, Any], position: list[float] | None,
                   current: list[float] | None = None) -> list[float] | None:
        """The direction the call asks for. `current` is the direction an existing node has,
        so an elevation or an azimuth given alone keeps the other."""
        angles = spec.get("elevation") is not None or spec.get("azimuth") is not None
        given = [k for k in ("direction", "aim_at") if spec.get(k) is not None] + (["elevation/azimuth"] if angles else [])
        if len(given) > 1:
            fail("BAD_PARAM", "Give one of direction, aim_at, or elevation with azimuth.")
        if spec.get("direction") is not None:
            return _normalize(_vector(spec["direction"], "direction"), "direction")
        if spec.get("aim_at") is not None:
            if position is None:
                fail("BAD_PARAM", "aim_at needs a position.")
            target = [x * self.scale for x in _vector(spec["aim_at"], "aim_at")]
            return _normalize([target[i] - position[i] for i in range(3)], "aim_at (it is the position)")
        if angles:
            elevation, azimuth = spec.get("elevation"), spec.get("azimuth")
            for value in (elevation, azimuth):
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    fail("BAD_PARAM", "elevation and azimuth are degrees.")
            if current is not None:  # where the light stands now, seen from where it shines
                if elevation is None:
                    elevation = math.degrees(math.asin(max(-1.0, min(1.0, -current[2]))))
                if azimuth is None:
                    azimuth = math.degrees(math.atan2(-current[0], -current[1])) % 360
            if elevation is None:
                fail("BAD_PARAM", "azimuth needs an elevation.")
            return sun_travel(float(elevation), float(180.0 if azimuth is None else azimuth))
        return None

    def _place(self, item: Item, creating: bool) -> dict[str, Any] | None:
        """Where the call puts the item: {"matrix": rows} or {"position": point}; None keeps it where it is.

        A position alone is used where the rotation is not the node's to set: a light aimed
        by where it stands, or one the renderer keeps on a look-at controller.
        """
        recipe, spec = item.recipe, item.spec
        aim = recipe.get("aim", "none")
        position = [x * self.scale for x in _vector(spec["position"], "position")] if spec.get("position") is not None else None
        bound = recipe.get("fields") or {}  # placement words the recipe stores as parameters are not an aim
        aimed = any(spec.get(k) is not None and k not in bound for k in ("direction", "aim_at", "elevation", "azimuth"))
        up = _normalize(_vector(spec["up"], "up"), "up") if spec.get("up") is not None else None
        if not item.is_node:
            if position is not None or aimed:
                fail("BAD_PARAM", f"{self.preset['label']} '{item.kind}' is not a scene object; it has no position or direction.")
            return None
        if aim == "none":
            if aimed:
                fail("UNSUPPORTED", f"{self.preset['label']} '{item.kind}' is not placed by direction. Its fields: "
                     f"{', '.join(sorted(recipe.get('fields') or {}))}.")
            if position is None and not creating:
                return None
            return {"position": position or [0.0, 0.0, 0.0]}
        if aim == "from_position":
            # The light shines at the world origin from where it stands.
            standing = None if creating or not aimed else _matrix_read(item.node)[3]
            now = _normalize([-x for x in standing], "position") if standing and any(standing) else None
            travel = self._direction(spec, position, now) if aimed else None
            if travel is None and position is not None:
                travel = _normalize([-x for x in position], "position (it is the world origin)")
            if travel is None:
                if creating:
                    fail("BAD_PARAM", f"A {item.kind} needs elevation and azimuth, or a direction.")
                return None
            if position is None:
                distance = float(recipe.get("distance_m", 10)) / self.ctx["meters_per_unit"]
                position = [-x * distance for x in travel]
            return {"position": position}
        held = None if creating or not aimed else _matrix_read(item.node)
        direction = self._direction(spec, position if position is not None else (held[3] if held else None),
                                    [-x for x in held[2]] if held else None)
        if creating:
            if direction is None and recipe.get("oriented"):
                fail("BAD_PARAM", f"A {item.kind} needs aim_at or direction" +
                     (" (or elevation and azimuth)." if item.kind in ("sun", "moon") else "."))
            return {"matrix": (look_rows(direction, up) if direction else IDENTITY) + [position or [0.0, 0.0, 0.0]]}
        if direction is None and position is None:
            return None
        if direction is None:
            return {"position": position}
        current = held or _matrix_read(item.node)
        return {"matrix": look_rows(direction, up) + [position or current[3]]}

    # -- items ------------------------------------------------------------

    def add(self, kind: str, spec: dict[str, Any], index: int) -> Item:
        if not isinstance(spec, dict):
            fail("BAD_PARAM", f"A {kind} is described by a dict of fields.")
        recipe = recipe_of(self.preset, kind)
        name = spec.get("name") or DEFAULT_NAMES.get(kind) or f"{kind.capitalize()}_{index + 1:02d}"
        if not isinstance(name, str):
            fail("BAD_PARAM", "name is text.")
        item = Item(f"i{len(self.items)}", kind, recipe, spec, name)
        if spec.get("map") is not None:
            return self._bind_map(item)
        native = spec.get("native") or {}
        if not isinstance(native, dict):
            fail("BAD_PARAM", "native is a dict of the renderer's own parameter names and values.")
        extra: dict[str, set[str]] = {obj: set() for obj in recipe["objects"]}
        for param in native:
            obj, plain = presets.split_param(param, recipe["objects"], item.main)
            extra[obj].add(plain)

        if item.is_node:
            found = find_node(name)
            if found is not None:
                identity = found["identity"]
                if not matches(recipe, identity["class_id"]):
                    fail("KIND_MISMATCH", f"'{name}' exists and is a {identity['label']}, not this renderer's "
                         f"{kind}. Delete it or use another name.")
                self._load_existing(item, found["owner_ref"], extra)
                if not matches(recipe, identity["class_id"], item.existing[item.main]["values"]):
                    fail("KIND_MISMATCH", f"'{name}' exists as another kind of {identity['label']}; a light's kind "
                         "is not changed by an edit. Delete it or use another name.")
                item.node = {"name": name}  # one node carries this name, or find_node had refused
        elif recipe.get("environment"):
            env_obj = recipe["environment"][1:]
            current = self.ctx.get("environment")
            if current and not spec.get("replace"):
                same = tuple(current["class_ref"]["class_id"]) == tuple(recipe["objects"][env_obj]["class"])
                if not same or env_obj != item.main:
                    fail("ENVIRONMENT_EXISTS", "The scene already has another environment map. Pass replace=True in "
                         "environment to replace it; the old map stays in the scene's materials.")
                self._load_existing(item, {"root": "environment"}, extra)

        creating = item.main not in item.existing
        item.created = creating
        if creating and recipe.get("environment") and self.ctx.get("environment") and not spec.get("replace"):
            fail("ENVIRONMENT_EXISTS", f"A {kind} brings its own environment map and the scene already has one. Pass "
                 "replace=True to replace it; the old map stays in the scene's materials.")
        for guard in recipe.get("guards") or ():
            self._guard(guard)
        placement = self._place(item, creating) if (item.is_node or creating) else None
        if creating:
            for obj, obj_spec in recipe["objects"].items():
                main = obj == item.main
                self.plan.create(item.ident(obj), obj_spec, self.schemas.of(obj_spec),
                                 name if main and item.is_node else "", placement if main and item.is_node else None)
            env = recipe.get("environment")
            if env:
                if self.plan.environment is not None:
                    fail("BAD_PARAM", "One environment per call.")
                self.plan.environment = {"owner_ref": {"created": item.ident(env[1:])},
                                         "replace_existing": bool(spec.get("replace"))}
        elif placement is not None:
            self.plan.transforms.append({"node": {"name": item.node["name"]}, **placement})
        self._emit(item, creating)
        self.items.append(item)
        return item

    def _bind_map(self, item: Item) -> Item:
        """Bind a texture map that already exists (a procedural sky, an inspected map) as the
        scene environment, as it is: its own settings are edited by its own parameters."""
        recipe, spec = item.recipe, item.spec
        if not recipe.get("environment"):
            fail("UNSUPPORTED", f"{self.preset['label']} lights the scene from a dome light, which this command "
                 "builds from a path; it does not bind an existing map.")
        extra = sorted(k for k, v in spec.items() if v is not None and k not in ("map", "replace", "kind", "name"))
        if extra or not isinstance(spec["map"], dict):
            fail("BAD_PARAM", "map is the owner_ref of an inspected texture map and is bound as it is" +
                 (f"; {', '.join(extra)} belong to the map's own parameters." if extra else "."))
        if self.ctx.get("environment") and not spec.get("replace"):
            fail("ENVIRONMENT_EXISTS", "The scene already has an environment map. Pass replace=True in environment "
                 "to replace it; the old map stays in the scene's materials.")
        for guard in recipe.get("guards") or ():
            self._guard(guard)
        if self.plan.environment is not None:
            fail("BAD_PARAM", "One environment per call.")
        self.plan.environment = {"owner_ref": spec["map"], "replace_existing": bool(spec.get("replace"))}
        item.bound = True
        self.items.append(item)
        return item

    def _guard(self, guard: dict[str, Any]) -> None:
        """A renderer setting the recipe depends on: checked now, and locked into the transaction."""
        state = _inspect({"root": "renderer"}, [guard["param"]])
        found = [p for p in state["properties"] if p["name"] == guard["param"]]
        pinned = "id" not in guard or (len(found) == 1 and found[0]["property_ref"].get("param_id") == guard["id"]
                                       and found[0]["property_ref"].get("block_id") == guard.get("block", 0))
        if (not pinned or guard["param"] not in state["values"]
                or not _same(state["values"][guard["param"]], guard["equals"])):
            fail("UNSUPPORTED", guard.get("message") or f"The renderer's {guard['param']} must be {guard['equals']!r}.")
        self.plan.guard(state)

    def settings(self, values: dict[str, Any]) -> list[str]:
        """Queue renderer settings (neutral names, or the renderer's own); returns the names written."""
        if not values:
            return []
        bound = self.preset.get("settings") or {}
        wanted: dict[str, tuple[Any, bool, Any]] = {}
        for name, value in values.items():
            binding = presets.binding_of(bound[name]) if name in bound else {"param": name}
            if "values" in binding:
                if value not in binding["values"]:
                    fail("BAD_PARAM", f"{name} is one of {', '.join(binding['values'])}.")
                value = binding["values"][value]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "min" in binding and value < binding["min"] or "max" in binding and value > binding["max"]:
                    fail("BAD_PARAM", f"{name} must be between {binding.get('min', '-inf')} and {binding.get('max', 'inf')}.")
                value = value * binding.get("scale", 1) if "scale" in binding else value
            if binding.get("invert") and type(value) is bool:
                value = not value
            for other, fixed in (binding.get("with") or {}).items():
                wanted.setdefault(other, (fixed, False, name))
            wanted[binding["param"]] = (value, name in bound, name)
        state = _inspect({"root": "renderer"}, sorted(wanted))
        known = {p["name"]: p for p in state["properties"]}
        for param, (value, neutral, asked) in wanted.items():
            if param not in known:
                offered = ", ".join(sorted(bound)) or "none"
                fail("BAD_PARAM", f"{self.preset['label']} has no setting '{asked}'. Neutral settings: {offered}; "
                     "or use one of the renderer's own parameter names (inspect_plugin_instance lists them).")
            raw = native_value(known[param], value, f"settings.{asked}", neutral)
            if param in state["values"] and _same(state["values"][param], raw):
                continue
            self.plan.write(state, known[param], raw)
        return sorted(values)


def _file(value: Any, extensions: list[str], label: str) -> str:
    """An existing file of an accepted type, as the absolute path the plugin stores and reads back."""
    if not isinstance(value, str) or not value:
        fail("BAD_PARAM", f"{label} is a file path.")
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() not in [ext.lower() for ext in extensions]:
        fail("BAD_PARAM", f"{label} takes {', '.join(extensions)} files.")
    if not path.is_file():
        fail("NOT_FOUND", f"{label}: no file at {path}")
    return str(path)


# ── Readback ─────────────────────────────────────────────────────

def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4) + 0.0  # + 0.0 turns -0.0 into 0.0
    if isinstance(value, (list, tuple)):
        return [_round(v) for v in value]
    return value


def read_objects(recipe: dict[str, Any], main_ref: dict[str, Any], schemas: Schemas,
                 environment: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(neutral fields, main-object values) of an existing recipe instance."""
    used = recipe_params(recipe)
    main = presets.main_object(recipe)
    states = {main: _inspect(main_ref, sorted(used[main]))}
    links = _links(recipe)
    for part, spec in recipe["objects"].items():
        if part == main:
            continue
        if recipe.get("environment") == "@" + part:
            bound = _environment_part(recipe, part, environment)
            if bound is not None:
                states[part] = _inspect(bound, sorted(used[part]))
            continue
        holder, param = links.get(part, (None, None))
        ref = states.get(holder, {}).get("values", {}).get(param) if holder else None
        if isinstance(ref, dict) and tuple(ref.get("class_ref", {}).get("class_id", ())) == tuple(spec["class"]):
            states[part] = _inspect(ref, sorted(used[part]))
    values = {obj: state["values"] for obj, state in states.items()}
    fields = decode(recipe, values, {obj: schemas.of(spec) for obj, spec in recipe["objects"].items()})
    return fields, values[main]


def summarize(kind: str, name: str | None, fields: dict[str, Any], matrix: Any = None,
              aim: str = "-z") -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind}
    if name:
        out["name"] = name
    out.update({k: _round(v) for k, v in fields.items()})
    if matrix is not None:
        position = list(matrix[3])
        out["position"] = _round(position)
        if aim == "-z":
            out["direction"] = _round([-x for x in matrix[2]])
        elif aim == "from_position" and any(abs(x) > 1e-9 for x in position):
            # It shines at the world origin from where it stands.
            travel = _normalize([-x for x in position], "position")
            out["direction"] = _round(travel)
            out["elevation"] = _round(math.degrees(math.asin(max(-1.0, min(1.0, -travel[2])))))
            out["azimuth"] = _round(math.degrees(math.atan2(-travel[0], -travel[1])) % 360)
    return out


def scene_nodes() -> list[dict[str, Any]]:
    """Every light and camera node: handle, superclass, name and world matrix rows."""
    from ...server import client

    script = ('(local s = stringstream ""; for o in objects where (superclassof o == light or superclassof o == camera) do '
              '(local m = o.transform; format "%|%|%|%|%|%|%\\n" (getHandleByAnim o) (superclassof o) m.row1 m.row2 m.row3 m.row4 o.name to:s); '
              "s as string)")
    text = str(client.send_command(script, cmd_type="maxscript").get("result", ""))
    nodes = []
    for line in text.splitlines():
        parts = line.split("|", 6)
        if len(parts) != 7:
            continue
        rows = [[float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:e[-+]?\d+)?", row)] for row in parts[2:6]]
        if any(len(row) != 3 for row in rows):
            continue
        nodes.append({"handle": int(re.sub(r"\D", "", parts[0]) or 0), "super": parts[1].strip().lower(),
                      "name": parts[6], "matrix": rows})
    return nodes


def recognize(preset: dict[str, Any], node: dict[str, Any], schemas: Schemas,
              environment: dict[str, Any] | None = None) -> dict[str, Any]:
    """A light or camera node as its neutral summary, or its class when no recipe covers it."""
    owner = {"node": {"handle": node["handle"]}, "scope": "base_object"}
    identity = api.inspect(owner_ref=owner, fields=["__identity_only__"], limit=1)["identity"]
    for kind, recipe in preset["recipes"].items():
        main = recipe["objects"][presets.main_object(recipe)]
        if main["super"] != node["super"] or not matches(recipe, identity["class_id"]):
            continue
        fields, main_values = read_objects(recipe, owner, schemas, environment)
        if matches(recipe, identity["class_id"], main_values):
            return summarize(kind, node["name"], fields, node["matrix"], recipe.get("aim", "none"))
    return {"name": node["name"], "class": identity["name"], "kind": None,
            "note": "not one of this renderer's preset kinds; read it with inspect_plugin_instance"}


def describe() -> dict[str, Any]:
    """The renderer, what its preset offers, and the lights, environment, cameras and settings in the scene."""
    ctx = context()
    renderer = ctx.get("renderer")
    preset = preset_of(renderer)
    out: dict[str, Any] = {
        "renderer": ({"name": renderer["name"], "label": renderer["label"], "preset": preset["id"] if preset else None}
                     if renderer else None),
        "switch_to": switch_choices(ctx["renderers"]),
        "meters_per_unit": round(ctx["meters_per_unit"], 6),
    }
    if presets.problems():
        out["preset_problems"] = presets.problems()
    if preset is None:
        out["note"] = ("No preset serves this renderer, so render_setup cannot build for it. Switch with "
                       "render_setup(renderer=...), or add a preset file to " + str(presets.user_dir()) + ".")
        return out
    # One line per kind: what it needs, what else it takes, its intensity units and how it is placed.
    kinds: dict[str, str] = {}
    for kind, recipe in preset["recipes"].items():
        needs = list(recipe.get("requires") or ())
        also = [name for name in sorted(recipe.get("fields") or {}) if name not in needs]
        parts = ([f"needs {', '.join(needs)}"] if needs else []) + ([f"takes {', '.join(also)}"] if also else [])
        rule = recipe.get("intensity")
        if rule:
            units = sorted(rule["units"]) + [f"{unit} (approximate)" for unit in sorted(rule.get("approx") or {})]
            default = rule.get("default")
            parts.append(f"intensity in {', '.join(units)}" + (f" (default {default['value']:g} {default['unit']})" if default else ""))
        place = recipe.get("aim", "none")
        main = recipe["objects"][presets.main_object(recipe)]["super"]
        parts.append("placed by position and aim_at or direction" if place == "-z" else
                     "placed by elevation and azimuth, or direction" if place == "from_position" else
                     "placed by position only" if main in ("light", "camera") else "not a scene object")
        kinds[kind] = "; ".join(parts)
    out["kinds"] = kinds
    out["settings"] = sorted(preset.get("settings") or {})
    out["quality"] = sorted(preset.get("quality") or {})
    if preset.get("notes"):
        out["notes"] = list(preset["notes"])
    schemas = Schemas()
    bound = preset.get("settings") or {}
    if bound:
        names = sorted({presets.binding_of(b)["param"] for b in bound.values()} |
                       {n for b in bound.values() for n in (presets.binding_of(b).get("with") or {})})
        state = _inspect({"root": "renderer"}, names)
        current = {}
        for name, raw in bound.items():
            binding = presets.binding_of(raw)
            active = all(_same(state["values"].get(k), v) for k, v in (binding.get("with") or {}).items())
            if active and binding["param"] in state["values"]:
                current[name] = _round(state["values"][binding["param"]])
        out["current_settings"] = current
    nodes = scene_nodes()
    listed = [recognize(preset, node, schemas, ctx.get("environment")) for node in nodes[:MAX_LISTED]]
    out["lights"] = [entry for entry, node in zip(listed, nodes) if node["super"] == "light"]
    out["cameras"] = [entry for entry, node in zip(listed, nodes) if node["super"] == "camera"]
    if len(nodes) > MAX_LISTED:
        out["not_listed"] = len(nodes) - MAX_LISTED
    env = ctx.get("environment")
    if env:
        out["environment"] = {"class_id": env["class_ref"]["class_id"], "enabled": ctx.get("environment_enabled")}
        for kind, recipe in preset["recipes"].items():
            target = recipe.get("environment")
            if target and target[1:] == presets.main_object(recipe) and matches(recipe, env["class_ref"]["class_id"]):
                fields, _ = read_objects(recipe, {"root": "environment"}, schemas)
                out["environment"] = {**summarize(kind, None, fields), "enabled": ctx.get("environment_enabled")}
    return out


# ── Applying ─────────────────────────────────────────────────────

def _preset_id(renderer: dict[str, Any]) -> str | None:
    return (presets.for_renderer(renderer["class_id"]) or {}).get("id")


def _plain(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def switch_choices(renderers: list[dict[str, Any]]) -> dict[str, str]:
    """What render_setup(renderer=...) takes, each with the renderer's label.

    A preset id names its renderer when one installed renderer has that preset. Where
    several share it (V-Ray and V-Ray GPU), each goes by its own name instead.
    """
    sharing: dict[str, int] = {}
    for renderer in renderers:
        if _preset_id(renderer):
            sharing[_preset_id(renderer)] = sharing.get(_preset_id(renderer), 0) + 1
    return {(_preset_id(r) if sharing[_preset_id(r)] == 1 else r["name"]): r["label"]
            for r in renderers if _preset_id(r)}


def find_renderers(requested: str, renderers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The installed renderers `requested` names: by name or label, else preset id, else the label's start.

    Punctuation and case are ignored. A label carries its version ("V-Ray GPU 7, update 4"),
    so its start names the renderer too: "V-Ray GPU", "vray gpu".
    """
    wanted = _plain(requested)
    if not wanted:
        return []
    return ([r for r in renderers if wanted in (_plain(r["name"]), _plain(r["label"]))]
            or [r for r in renderers if _preset_id(r) == requested.strip().lower()]
            or [r for r in renderers if _plain(r["label"]).startswith(wanted)])


def switch_renderer(requested: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Make `requested` (a preset id, or a renderer's name or label) the production renderer."""
    found = find_renderers(requested, ctx["renderers"])
    if len(found) != 1:
        names = ", ".join(r["name"] + (f" = {r['label']}" if _plain(r["label"]) != _plain(r["name"]) else "")
                          + (f" ({_preset_id(r)})" if _preset_id(r) else "") for r in ctx["renderers"])
        fail("NOT_FOUND" if not found else "AMBIGUOUS",
             f"renderer {requested!r} names {len(found)} installed renderers. Installed: {names}.")
    current = ctx.get("renderer")
    if current and tuple(current["class_id"]) == tuple(found[0]["class_id"]):
        return ctx
    result = api.native("native:assign_renderer", {"class_ref": {"superclass_id": presets.SUPERCLASSES["renderer"],
                                                                 "class_id": list(found[0]["class_id"])}})
    return result["context"]


def apply(*, renderer: str = "", quality: str = "", settings: dict[str, Any] | None = None,
          lights: list[dict[str, Any]] | None = None, sun: dict[str, Any] | None = None,
          environment: dict[str, Any] | None = None, camera: dict[str, Any] | None = None,
          distance_unit: str = "scene") -> dict[str, Any]:
    """Switch renderer, then create or update everything asked for in one transaction."""
    ctx = context()
    before = ctx.get("renderer")
    if renderer:
        ctx = switch_renderer(renderer, ctx)
    preset = active_preset(ctx)
    compiler = Compiler(ctx, preset, distance_unit)
    out: dict[str, Any] = {"renderer": {"name": ctx["renderer"]["name"], "preset": preset["id"]}}
    if renderer:
        out["renderer"]["switched"] = not before or tuple(before["class_id"]) != tuple(ctx["renderer"]["class_id"])

    wanted_settings: dict[str, Any] = {}
    if quality:
        levels = preset.get("quality") or {}
        if quality not in levels:
            fail("BAD_PARAM", f"{preset['label']} has no quality '{quality}'. Levels: {', '.join(sorted(levels)) or 'none'}.")
        wanted_settings.update(levels[quality])
    wanted_settings.update(settings or {})
    written = compiler.settings(wanted_settings)

    slots: list[str] = []  # where each item is reported, in the order they were added
    for index, spec in enumerate(lights or []):
        kind = (spec or {}).get("kind") if isinstance(spec, dict) else None
        if not isinstance(kind, str):
            fail("BAD_PARAM", "Each light needs a kind, e.g. {'kind': 'sphere', 'radius': 5, ...}.")
        compiler.add(kind, spec, index)
        slots.append("lights")
    for slot, spec, default_kind in (("sun", sun, "sun"), ("environment", environment, "hdri"), ("camera", camera, "camera")):
        if spec is not None:
            compiler.add((spec.get("kind") if isinstance(spec, dict) else None) or default_kind, spec, 0)
            slots.append(slot)

    if compiler.plan.empty:
        if written:
            out["settings"] = "unchanged"
        return out
    applied = api.native("native:plugin_patch", compiler.plan.payload())
    resources = {r["id"]: r for r in applied.get("resources") or []}
    moved = {t["node_ref"]["name"]: t["matrix"] for t in applied.get("transforms") or []}
    schemas = compiler.schemas
    for item, slot in zip(compiler.items, slots):
        if item.bound:
            out[slot] = {"kind": item.kind, "action": "bound", "map": item.spec["map"].get("class_ref")}
            continue
        main_id = item.ident(item.main)
        resource = resources.get(main_id)
        if resource is not None:
            item.node = resource.get("node_ref") or item.node
            matrix = resource.get("matrix")
            owner = resource["owner_ref"]
        else:
            matrix = moved.get((item.node or {}).get("name"))
            owner = item.existing[item.main]["owner_ref"]
        try:
            fields, _ = read_objects(item.recipe, owner, schemas, (applied.get("context") or {}).get("environment"))
            entry = summarize(item.kind, (item.node or {}).get("name") or (item.name if item.is_node else None), fields,
                              matrix, item.recipe.get("aim", "none"))
        except Exception as exc:  # noqa: BLE001 - the change is committed and verified; only this report failed
            entry = {"kind": item.kind, "name": item.name, "committed": True, "read_error": str(exc)}
        entry["action"] = "created" if item.created else "updated"
        if item.notes:
            entry["notes"] = item.notes
        if slot == "lights":
            out.setdefault("lights", []).append(entry)
        else:
            out[slot] = entry
    if written:
        bound = preset.get("settings") or {}
        names = sorted({presets.binding_of(bound[n])["param"] if n in bound else n for n in wanted_settings})
        state = _inspect({"root": "renderer"}, names)
        out["settings"] = {name: _round(state["values"].get(presets.binding_of(bound[name])["param"] if name in bound else name))
                           for name in sorted(wanted_settings)}
    out["undo"] = "one step" + (" (the renderer switch itself is not undoable)" if out["renderer"].get("switched") else "")
    return out


# ── Rendering ────────────────────────────────────────────────────

def render_dir() -> Path:
    folder = Path(tempfile.gettempdir()) / "3dsmax-mcp" / "renders"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def render(spec: Any, job: str = "") -> dict[str, Any]:
    """Render one frame and return where it was written; a long render returns a job to wait on."""
    from ...server import client

    if job:
        outcome = jobs.resume(client, "render_setup", job, wait_seconds=jobs.MAX_WAIT_SECONDS)
    else:
        spec = {} if spec is True else dict(spec)
        unknown = set(spec) - {"camera", "size", "output", "frame", "wait_seconds"}
        if unknown:
            fail("BAD_PARAM", f"render takes camera, size, output, frame and wait_seconds, not {', '.join(sorted(unknown))}.")
        size = spec.get("size") or [1280, 720]
        if (not isinstance(size, (list, tuple)) or len(size) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in size)):
            fail("BAD_PARAM", "size is [width, height] in pixels.")
        output = spec.get("output") or str(render_dir() / f"render_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.jpg")
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"width": size[0], "height": size[1], "output_path": str(output).replace("\\", "/")}
        camera = spec.get("camera")
        if camera is not None:
            payload["camera"] = camera if isinstance(camera, (str, dict, int)) else getattr(camera, "name", str(camera))
        if spec.get("frame") is not None:
            payload["frame"] = int(spec["frame"])
        outcome = jobs.start(client, "render_setup", "native:render_scene", payload,
                             wait_seconds=float(spec.get("wait_seconds", jobs.MAX_WAIT_SECONDS)), timeout=3600,
                             context={"output": payload["output_path"]})
    if outcome.running:
        return {**outcome.running, "hint": f"Still rendering. Call render_setup(job='{outcome.running['job']}') to wait "
                                           "again; never start the render a second time."}
    import json as _json

    result = outcome.response.get("result", {})
    result = _json.loads(result) if isinstance(result, str) else dict(result)
    file = result.pop("outputFile", None) or outcome.context.get("output")
    if file:
        result["file"] = file
        if not Path(file).is_file():
            result["warning"] = "The renderer reported success but wrote no file at this path."
    return result
