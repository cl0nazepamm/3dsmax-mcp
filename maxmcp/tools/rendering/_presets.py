"""Renderer presets: what each renderer calls the things every renderer has.

A preset is a JSON file in maxmcp/assets/renderers, or in the user's
%LOCALAPPDATA%/3dsmax-mcp/renderers (which wins on the same id). It maps one neutral
vocabulary (light kinds, sizes, intensity units, color, sun, environment, camera, render
settings) onto a renderer's classes and parameters. render_setup interprets presets and
holds no renderer-specific code, so supporting another renderer is adding a file.

Preset file:

    {"preset": 1, "id": "octane", "label": "OctaneRender",
     "extends": "photometric",                     another preset to start from (optional);
                                                   every preset also starts from "common"
     "renderers": [[2717442453, 1319335807]],      production renderer class IDs it serves
     "verified": "how and where the bindings were checked",
     "notes": ["what an agent must know and cannot read from the fields"],
     "recipes": {"sphere": {...}, "sun": {...}, "hdri": {...}, "camera": {...}},
     "settings": {"samples": "kernel_maxSamples"},  neutral render setting -> binding
     "quality": {"draft": {"samples": 64}},         named levels of neutral settings
     "enums": [{"super": 48, "class": [a, b], "block": 0, "id": 1, "param": "type",
                "values": {"rectangle": 0}, "evidence": "where the meaning comes from"}]}

Recipe (how one kind of thing is built, recognized, read and edited):

    {"extends": "_area",                 another recipe of this preset to start from
     "objects": {"light": {"class": [a, b], "super": "light"},
                 "emission": {"class": [c, d], "super": "texmap"}},
                                         the first object is the main one; a light or
                                         camera main object gets the node and transform
     "match": {"type": 2},               main-object values that identify this recipe
     "set": {"targeted": false, "emission": "@emission", "emission.normalize": true},
                                         written at creation; "@name" links an object and
                                         "@name:node" its scene node
     "aim": "-z",                        "-z" (the node looks down its -Z), "from_position"
                                         (the light points at the world origin from where
                                         it stands), "none"
     "oriented": true,                   creation needs aim_at or direction
     "requires": ["width", "height"],    fields creation must be given
     "defaults": {"kelvin": 6500},       field values used at creation when not given
     "fields": {"radius": "size0"},      neutral field -> binding (below)
     "intensity": {"units": {"lm": {"param": "multiplier", "with": {"normalizeColor": 1}}},
                   "default": {"value": 1500, "unit": "lm"},
                   "approx": {"lm": {"unit": "W", "factor": 0.0586, "basis": "why"}}},
     "environment": "@env",              the object bound to the scene environment slot
     "guards": [{"param": "bg.source", "equals": 0, "message": "..."}],
                                         renderer settings that must hold; checked and
                                         locked into the same transaction
     "notes": [...]}

Binding: "param" or "object.param", or a dict with

    param      the parameter ("object.param" for a part)
    with       other parameters written with it (a mode switch); on readback the field
               is the active one only while they hold these values
    scale      native = neutral * scale
    index      element of a vector parameter (0..3, or "all")
    count      how many elements an array parameter has (default 2); vectors know theirs
    invert     the parameter is the opposite boolean
    values     neutral name -> integer for an enum field
    min, max   refuse neutral values outside
    extensions the file types a path field accepts, e.g. [".exr", ".hdr"]; the file must exist
    fixed      the renderer has no such switch: only this value is accepted ("why" says so)

Angles are degrees on the neutral side and converted when the parameter stores radians.
"""

from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...assets import ASSETS

PRESET_VERSION = 1
BUILTIN_DIR = ASSETS / "renderers"
COMMON = "common"
SUPERCLASSES = {"light": 48, "camera": 32, "texmap": 3088, "material": 3072, "renderer": 3840}
RECIPE_KEYS = {"objects", "match", "set", "aim", "oriented", "requires", "defaults", "fields", "intensity",
               "environment", "guards", "notes", "distance_m"}
BINDING_KEYS = {"param", "with", "scale", "index", "count", "invert", "values", "min", "max", "fixed", "why",
                "extensions"}
AIMS = {"-z", "from_position", "none"}


class PresetError(ValueError):
    """A preset file that cannot be used; the message names the file and the problem."""


def user_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    return (Path(root) if root else Path.home() / "AppData" / "Local") / "3dsmax-mcp" / "renderers"


def _merge(base: Any, over: Any) -> Any:
    """`over` on top of `base`: dicts merge key by key, anything else replaces; null deletes a key."""
    if isinstance(base, dict) and isinstance(over, dict):
        merged = dict(base)
        for key, value in over.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = _merge(base.get(key), value)
        return merged
    return copy.deepcopy(over)


def binding_of(value: Any) -> dict[str, Any]:
    """A binding in its dict form."""
    return {"param": value} if isinstance(value, str) else dict(value)


def split_param(name: str, objects: Any, main: str) -> tuple[str, str]:
    """("object", "param") of a recipe parameter reference; parameter names may contain dots themselves."""
    head, dot, tail = name.partition(".")
    if dot and head in objects:
        return head, tail
    return main, name


def main_object(recipe: dict[str, Any]) -> str:
    return next(iter(recipe["objects"]))


def _check_binding(where: str, binding: Any) -> None:
    if isinstance(binding, str):
        return
    if not isinstance(binding, dict):
        raise PresetError(f"{where}: a binding is a parameter name or an object")
    unknown = set(binding) - BINDING_KEYS
    if unknown:
        raise PresetError(f"{where}: unknown binding keys {sorted(unknown)}")
    if ("fixed" in binding) == ("param" in binding):
        raise PresetError(f"{where}: a binding has either 'param' or 'fixed'")
    if "index" in binding and binding["index"] not in (0, 1, 2, 3, "all"):
        raise PresetError(f"{where}: index is 0..3 or 'all'")
    values = binding.get("values")
    if values is not None and not all(isinstance(v, int) and not isinstance(v, bool) for v in values.values()):
        raise PresetError(f"{where}: enum values are integers")


def _class_id(where: str, value: Any) -> tuple[int, int]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(part, int) and not isinstance(part, bool) and 0 <= part <= 0xFFFFFFFF for part in value)):
        raise PresetError(f"{where}: a class ID is two unsigned 32-bit integers")
    return int(value[0]), int(value[1])


def _resolve_recipe(where: str, name: str, recipes: dict[str, Any], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    recipe = recipes[name]
    if not isinstance(recipe, dict):
        raise PresetError(f"{where}: recipe {name!r} is not an object")
    parent = recipe.get("extends")
    own = {k: copy.deepcopy(v) for k, v in recipe.items() if k != "extends"}
    if parent is None:
        return own
    if parent not in recipes or parent in seen or parent == name:
        raise PresetError(f"{where}: recipe {name!r} extends {parent!r}, which is missing or circular")
    return _merge(_resolve_recipe(where, parent, recipes, seen + (name,)), own)


def _validate_recipe(where: str, recipe: dict[str, Any]) -> None:
    unknown = set(recipe) - RECIPE_KEYS
    if unknown:
        raise PresetError(f"{where}: unknown recipe keys {sorted(unknown)}")
    objects = recipe.get("objects")
    if not isinstance(objects, dict) or not objects:
        raise PresetError(f"{where}: a recipe needs objects")
    for key, spec in objects.items():
        if "." in key or not isinstance(spec, dict) or spec.get("super") not in SUPERCLASSES:
            raise PresetError(f"{where}: object {key!r} needs a class and a super of {sorted(SUPERCLASSES)}")
        spec["class"] = _class_id(f"{where}.objects.{key}", spec.get("class"))
    if recipe.get("aim", "none") not in AIMS:
        raise PresetError(f"{where}: aim is one of {sorted(AIMS)}")
    for name, value in (recipe.get("set") or {}).items():
        if isinstance(value, str) and value.startswith("@") and value[1:].partition(":")[0] not in objects:
            raise PresetError(f"{where}.set.{name}: unknown object {value!r}")
    fields = recipe.get("fields") or {}
    for field, binding in fields.items():
        _check_binding(f"{where}.fields.{field}", binding)
    intensity = recipe.get("intensity")
    if intensity is not None:
        units = intensity.get("units") or {}
        if not units:
            raise PresetError(f"{where}.intensity: needs units")
        for unit, binding in units.items():
            _check_binding(f"{where}.intensity.units.{unit}", binding)
        default = intensity.get("default")
        if default is not None and default.get("unit") not in units:
            raise PresetError(f"{where}.intensity.default: its unit must be one of the units")
        for unit, rule in (intensity.get("approx") or {}).items():
            if rule.get("unit") not in units or not isinstance(rule.get("factor"), (int, float)) or not rule.get("basis"):
                raise PresetError(f"{where}.intensity.approx.{unit}: needs a native unit, a factor and a basis")
    env = recipe.get("environment")
    if env is not None and (not isinstance(env, str) or not env.startswith("@") or env[1:] not in objects):
        raise PresetError(f"{where}.environment: names one of the objects as '@name'")
    for missing in set(recipe.get("requires") or ()) | set(recipe.get("defaults") or ()):
        if missing not in fields:
            raise PresetError(f"{where}: {missing!r} is required or defaulted but is not one of the fields")


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PresetError(f"{path}: {exc}") from None
    if not isinstance(data, dict) or data.get("preset") != PRESET_VERSION:
        raise PresetError(f"{path}: expected a preset of version {PRESET_VERSION}")
    if not isinstance(data.get("id"), str) or not data["id"]:
        raise PresetError(f"{path}: a preset needs an id")
    data["source"] = str(path)
    return data


def _inherited(raw: dict[str, dict[str, Any]], preset_id: str, seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """The preset with what it extends underneath it; lists (notes, enums) add up."""
    data = raw[preset_id]
    parent = data.get("extends") or (COMMON if preset_id != COMMON and COMMON in raw else None)
    if parent is None:
        return copy.deepcopy(data)
    if parent not in raw or parent in seen or parent == preset_id:
        raise PresetError(f"{data['source']}: extends {parent!r}, which is missing or circular")
    base = _inherited(raw, parent, seen + (preset_id,))
    merged = dict(copy.deepcopy(data))
    # A recipe, setting or quality level of the same name replaces the inherited one whole.
    for key in ("recipes", "settings", "quality"):
        merged[key] = {**(base.get(key) or {}), **copy.deepcopy(data.get(key) or {})}
    for key in ("notes", "enums"):
        merged[key] = list(base.get(key) or []) + list(data.get(key) or [])
    return merged


def _build(raw: dict[str, dict[str, Any]], preset_id: str) -> dict[str, Any]:
    data = _inherited(raw, preset_id)
    name = Path(data["source"]).name
    data["renderers"] = [_class_id(f"{name}: renderers", item) for item in data.get("renderers") or []]
    recipes = {}
    for kind in data["recipes"]:
        if kind.startswith("_"):
            continue  # a base other recipes extend, not a kind of its own
        resolved = _resolve_recipe(name, kind, data["recipes"])
        _validate_recipe(f"{name}: {kind}", resolved)
        recipes[kind] = resolved
    data["recipes"] = recipes
    for setting, binding in data["settings"].items():
        _check_binding(f"{name}: settings.{setting}", binding)
    for level, values in data["quality"].items():
        unknown = set(values) - set(data["settings"])
        if unknown:
            raise PresetError(f"{name}: quality.{level} uses settings the preset does not bind: {sorted(unknown)}")
    for entry in data["enums"]:
        entry["class"] = _class_id(f"{name}: enums", entry.get("class"))
        if not isinstance(entry.get("values"), dict) or not entry.get("evidence") or not entry.get("param"):
            raise PresetError(f"{name}: an enum needs param, values and evidence")
    return data


@lru_cache(maxsize=1)
def _loaded(stamp: tuple[tuple[str, int], ...]) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    raw: dict[str, dict[str, Any]] = {}
    found_problems: list[str] = []
    for path_text, _ in stamp:
        try:
            data = _read(Path(path_text))
        except PresetError as exc:
            found_problems.append(str(exc))
            continue
        raw[data["id"]] = data  # later (user) files replace the built-in of the same id
    presets: dict[str, dict[str, Any]] = {}
    for preset_id in raw:
        try:
            presets[preset_id] = _build(raw, preset_id)
        except PresetError as exc:
            found_problems.append(str(exc))
    return presets, tuple(found_problems)


def _stamp() -> tuple[tuple[str, int], ...]:
    files: list[tuple[str, int]] = []
    for folder in (BUILTIN_DIR, user_dir()):
        try:
            entries = sorted(folder.glob("*.json"))
        except OSError:
            continue
        for path in entries:
            try:
                files.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                pass
    return tuple(files)


def all_presets() -> dict[str, dict[str, Any]]:
    """Every usable preset by id. An edited file is read again on the next call."""
    return _loaded(_stamp())[0]


def problems() -> list[str]:
    """Preset files that were skipped, each with the reason."""
    return list(_loaded(_stamp())[1])


def for_renderer(class_id: Any) -> dict[str, Any] | None:
    """The preset that serves a production renderer class, or None."""
    wanted = tuple(class_id) if class_id is not None else ()
    for preset in all_presets().values():
        if wanted in preset["renderers"]:
            return preset
    return None


def enum_domains(superclass: int, class_id: Any) -> list[dict[str, Any]]:
    """Named integer values the presets record for one class's parameters."""
    wanted = tuple(class_id)
    found: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for preset in all_presets().values():
        for entry in preset.get("enums") or []:
            key = (entry.get("block"), entry.get("id"), entry["param"])
            if entry.get("super") == superclass and entry["class"] == wanted and key not in seen:
                seen.add(key)
                found.append(entry)
    return found
