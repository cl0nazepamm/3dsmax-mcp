"""Builder mode: spec-gated staged asset construction.

img2threejs-style discipline in Max terms: a machine-readable sculpt spec lives
as AppData on a root assembly Dummy (same mechanism as the tyFlow wiring
ledger), `builder_gate` measures the real scene against it deterministically
(zero model tokens), and `record` is the only door between passes.  Agent
vision on capture_multi_view grids replaces img2threejs's VLM layer and runs
only after the hard gates pass.

Pass order: spec -> blockout -> form -> material -> detail -> finish -> complete.
Gates are cumulative: every pass re-checks all earlier gates in one census
round trip.  The workflow contract (naming anchors, projection recipe, vision
rubric) is documented in skills/3dsmax-mcp-dev/builder.md.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import tempfile
import time
from typing import Any, Literal

from ..coerce import DictValue, StrList
from ..helpers.maxscript import safe_string
from ..server import client

BUILDER_APPDATA_ID = 1112294482  # "BLDR"
LEDGER_VERSION = 2
PASSES = ["blockout", "form", "material", "detail", "finish"]
VERDICTS = {"continue", "refine-spec", "refine-scene", "request-input"}
VIA = {"modifier", "editpoly", "map", "geometry", "projection", "boolean", "spline"}
COMPLEXITY_FLOORS = {"simple": (3, 0), "moderate": (6, 6), "complex": (10, 12)}
DEFAULT_TOLERANCE_PCT = 8.0
MAX_TOLERANCE_PCT = 25.0
DEFAULT_VISUAL_THRESHOLD = 0.8
MAX_ATTEMPTS = 3
HISTORY_CAP = 40
MIN_EVIDENCE_CHARS = 10
MAX_SESSION_NAME = 64
MAX_ITEM_NAME = 80
MAX_COMPONENTS = 128
MAX_MATERIALS = 64
MAX_DETAILS = 256
MAX_SPEC_BYTES = 100_000
SPEC_FIELDS = (
    "object", "reference", "units", "complexity", "tolerance_pct",
    "components", "materials", "details", "budget", "review", "assumptions",
)
COMPONENT_FIELDS = {
    "name", "kind", "dims", "axis_dims", "center", "material", "primitive",
    "nested", "ground", "floating", "parent", "ratios", "symmetry", "mirror_of",
    "mirror_axis", "nested_in", "touches",
}
MATERIAL_FIELDS = {"name", "class", "params"}
DETAIL_FIELDS = {"id", "on", "via", "count", "description", "priority"}
BUDGET_FIELDS = {"tris", "min_tris"}
REVIEW_FIELDS = {"threshold"}
_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-]+$")
_GENERIC_DETAIL_RE = re.compile(r"^(?:detail|det|feature|part)\s*\d*$", re.IGNORECASE)
UNIT_SUFFIXES = {
    "generic": "",
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "in": "in",
    "ft": "ft",
}
PASS_VIEWS = {
    "blockout": ["front", "right", "top", "back"],
    "form": ["front", "right", "left", "top"],
    "material": ["front", "right", "back", "top"],
    "detail": ["front", "right", "left", "top"],
    "finish": ["front", "right", "back", "top"],
}
ALLOWED_VIEWS = {"front", "back", "left", "right", "top", "bottom", "perspective"}

# Self-absolving vocabulary: evidence that needs these words describes work that
# is not done. `record continue` refuses them — they are refine words.
HEDGE_WORDS = (
    "stylized", "stylization", "proxy", "proxies", "placeholder",
    "chunky", "good enough", "close enough", "for now", "acceptable for",
)

# Shaping gate (form pass): a geometry component still classed as one of these
# with no shaping work on it reads as a blockout box, not a form.
PRIMITIVE_CLASSES = {
    "box", "cylinder", "sphere", "geosphere", "cone", "tube", "torus", "plane",
    "pyramid", "prism", "capsule", "chamferbox", "chamfercyl", "oiltank",
    "spindle", "gengon", "l_ext", "c_ext", "hose", "teapot",
}
# Modifier classes that do not change the silhouette; anything else counts as
# shaping work.  Permissive on purpose — the gate exists to catch bare
# primitives, and the vision rubric still judges the rest.
NONSHAPING_MOD_CLASSES = {
    "uvwmap", "unwrap_uvw", "uvw_xform", "mapscaler", "materialmodifier",
    "material_by_element", "smooth", "normalmodifier", "edit_normals",
    "vertexpaint", "paintlayermod", "turn_to_poly", "turn_to_mesh",
    "turn_to_patch", "renderable_spline", "camera_map",
}

EDITABLE_BASE_CLASSES = {
    "editable_mesh", "editable_poly", "editable_patch", "triobject", "polyobj",
}

GATE_TOOLS = {
    "coverage": ["create_object", "set_parent"],
    "proportion": ["transform_object", "edit_vertices"],
    "placement": ["transform_object", "set_parent"],
    "relation": ["transform_object", "set_parent"],
    "overlap": ["transform_object", "edit_vertices"],
    "blockout": ["create_object", "transform_object"],
    "degenerate": ["transform_object", "collapse_modifier_stack"],
    "shaping": ["boolean_operation", "draw_spline", "edit_vertices", "add_modifier"],
    "material": ["assign_material", "set_material_properties"],
    "detail": ["boolean_operation", "draw_spline", "edit_vertices", "create_texture_map"],
    "budget": ["collapse_modifier_stack"],
    "hygiene": ["manage_layers", "set_parent", "delete_objects"],
}


def _shaping_of(
    comp: dict[str, Any],
    node: dict[str, Any],
    baseline: dict[str, Any] | None = None,
) -> str:
    """Return the form work added after the accepted blockout.

    A converted Editable Poly is not inherently proof of a good form.  The
    accepted blockout signature makes the check temporal: a component must
    gain a Boolean, gain an enabled shaping modifier, measurably change its
    evaluated surface/volume, or carry an explicit primitive exemption.
    """
    if comp.get("primitive"):
        return "declared-primitive"
    base_cls = (node.get("baseclass") or node["class"]).lower()
    current_bools = {str(value).lower() for value in node.get("boolops", [])}
    enabled = list(node.get("modenabled", []))
    current_mods = {
        str(value).lower()
        for index, value in enumerate(node.get("modcls", []))
        if str(value).lower() not in NONSHAPING_MOD_CLASSES
        and (index >= len(enabled) or bool(enabled[index]))
    }

    if baseline is None:
        # Migration fallback for an in-flight v1 session.  New sessions always
        # record a blockout baseline before form unlocks.
        if base_cls not in PRIMITIVE_CLASSES and base_cls not in EDITABLE_BASE_CLASSES:
            return "base"
        if current_bools:
            return "boolean"
        if current_mods:
            return "modifier"
        return ""

    old_bools = {str(value).lower() for value in baseline.get("boolops", [])}
    old_enabled = list(baseline.get("modenabled", []))
    old_mods = {
        str(value).lower()
        for index, value in enumerate(baseline.get("modcls", []))
        if str(value).lower() not in NONSHAPING_MOD_CLASSES
        and (index >= len(old_enabled) or bool(old_enabled[index]))
    }
    if current_bools - old_bools:
        return "boolean"
    if current_mods - old_mods:
        return "modifier"
    for metric in ("area", "volume"):
        before = float(baseline.get(metric) or 0.0)
        after = float(node.get(metric) or 0.0)
        if before > 1e-9 and abs(after - before) / before > 0.005:
            return "geometry-change"
    return ""


# ---------------------------------------------------------------------------
# Ledger


def _empty_state() -> dict[str, Any]:
    return {
        "pass": "spec",
        "completed": False,
        "blocked": False,
        "spec_unlocked": True,
        "spec_revision": 0,
        "check_seq": 0,
        "attempts": {},
        "failure": {},
        "last_check": {},
        "form_baseline": {},
        "migration_from": 0,
        "resume_token": "",
        "final_review": {},
        "history": [],
    }


def _parse_ledger(raw: Any) -> dict[str, Any] | None:
    """Defensive parse; None means no usable ledger (unlike tyFlow, absence
    matters: builder tools refuse to run on nodes they did not initialize)."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("kind") != "builder":
        return None
    try:
        source_version = int(data.get("v") or 1)
    except (TypeError, ValueError):
        return None
    if source_version > LEDGER_VERSION or source_version < 1:
        return None
    data["v"] = LEDGER_VERSION
    state = data.get("state")
    if not isinstance(state, dict):
        if source_version == LEDGER_VERSION:
            return None
        data["state"] = _empty_state()
    else:
        base = _empty_state()
        base.update({k: state[k] for k in base if k in state})
        data["state"] = base
    if source_version < LEDGER_VERSION:
        # V2 adds required centers, snapshot-bound reviews, and a blockout
        # baseline.  An old in-flight ledger must be revalidated rather than
        # silently inheriting weaker proof.
        data["state"].update(
            {
                "pass": "spec",
                "completed": False,
                "blocked": False,
                "spec_unlocked": True,
                "last_check": {},
                "failure": {},
                "form_baseline": {},
                "migration_from": source_version,
            }
        )
    valid_passes = {"spec", "complete", *PASSES}
    parsed_state = data["state"]
    if parsed_state.get("pass") not in valid_passes:
        return None
    for key, expected in (
        ("attempts", dict), ("failure", dict), ("last_check", dict),
        ("form_baseline", dict), ("final_review", dict), ("history", list),
    ):
        if not isinstance(parsed_state.get(key), expected):
            return None
    for key in ("components", "materials", "details"):
        if not isinstance(data.get(key), list):
            if source_version == LEDGER_VERSION:
                return None
            data[key] = []
    if not isinstance(data.get("budget"), dict):
        if source_version == LEDGER_VERSION:
            return None
        data["budget"] = {}
    return data


def _ledger_literal(ledger: dict[str, Any]) -> str:
    """Compact ledger JSON as a MAXScript double-quoted literal (same escaping
    as the tyFlow ledger writer)."""
    compact = json.dumps(ledger, separators=(",", ":"), sort_keys=True)
    escaped = compact.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_ledger(root_name: str, ledger: dict[str, Any]) -> None:
    safe = safe_string(root_name)
    script = f"""(
local matches = for o in objects where (stricmp o.name "{safe}") == 0 collect o
if matches.count == 0 then (
    "__ERROR__|Root not found: {safe}"
) else if matches.count > 1 then (
    "__ERROR__|Multiple nodes are named {safe}"
) else (
    local root = matches[1]
    local old = getAppData root {BUILDER_APPDATA_ID}
    try (
        try (deleteAppData root {BUILDER_APPDATA_ID}) catch ()
        setAppData root {BUILDER_APPDATA_ID} {_ledger_literal(ledger)}
        "OK"
    ) catch (
        try (
            deleteAppData root {BUILDER_APPDATA_ID}
            if old != undefined do setAppData root {BUILDER_APPDATA_ID} old
        ) catch ()
        "__ERROR__|Ledger write failed: " + (getCurrentException())
    )
)
)"""
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    if raw.strip() != "OK":
        raise RuntimeError(f"builder ledger write was not acknowledged: {raw!r}")


def _read_ledger(root_name: str) -> dict[str, Any]:
    safe = safe_string(root_name)
    script = f"""(
local matches = for o in objects where (stricmp o.name "{safe}") == 0 collect o
if matches.count == 0 then "__ERROR__|Root not found: {safe}" else if matches.count > 1 then (
    "__ERROR__|Multiple nodes are named {safe}"
) else (
    local root = matches[1]
    local ad = getAppData root {BUILDER_APPDATA_ID}
    if ad == undefined then "__ERROR__|{safe} has no builder ledger" else ad
)
)"""
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    ledger = _parse_ledger(raw)
    if ledger is None:
        raise RuntimeError(f"{root_name} has an invalid builder ledger")
    return ledger


def _history_add(ledger: dict[str, Any], entry: dict[str, Any]) -> None:
    entry["t"] = int(time.time())
    history = ledger["state"]["history"]
    history.append(entry)
    del history[:-HISTORY_CAP]


def _root_name(name: str) -> str:
    name = name.strip()
    return name if name.upper().startswith("BLD_") else f"BLD_{name}"


def _public_state(ledger: dict[str, Any], *, include_history: bool = False) -> dict[str, Any]:
    """Return the small state surface an agent needs for its next decision."""
    state = ledger["state"]
    out = {
        "pass": state["pass"],
        "completed": bool(state["completed"]),
        "blocked": bool(state["blocked"]),
        "spec_revision": int(state.get("spec_revision") or 0),
    }
    failure = state.get("failure") or {}
    if failure.get("pass") == state["pass"]:
        out["failure_streak"] = int(failure.get("streak") or 0)
    if state.get("migration_from"):
        out["migration_required"] = int(state["migration_from"])
    if state.get("completed") and state.get("final_review"):
        review = state["final_review"]
        out["final_review"] = {
            key: review[key]
            for key in ("review_id", "file", "views", "visual_score")
            if key in review
        }
    if include_history:
        out["history"] = list(state.get("history") or [])
    return out


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _spec_fingerprint(ledger: dict[str, Any]) -> str:
    return _stable_hash({key: ledger.get(key) for key in SPEC_FIELDS})


def _public_spec(ledger: dict[str, Any]) -> dict[str, Any]:
    """Return the authoritative blueprint only when recovery is explicitly requested."""
    return copy.deepcopy({key: ledger.get(key) for key in SPEC_FIELDS})


def _scene_fingerprint(ledger: dict[str, Any], census: dict[str, Any]) -> str:
    """Hash only gate-relevant scene truth; ledger history never affects it."""
    nodes = []
    for node in sorted(census["node_list"], key=lambda item: item["name"].lower()):
        nodes.append(
            {
                "name": node["name"].lower(),
                "class": node["class"],
                "base": node.get("baseclass", ""),
                "parent": node["parent"].lower(),
                "layer": node["layer"],
                "bbmin": [round(float(v), 5) for v in node["bbmin"]],
                "bbmax": [round(float(v), 5) for v in node["bbmax"]],
                "pos": [round(float(v), 5) for v in node["pos"]],
                "rotation": [round(float(v), 6) for v in node.get("rotation", [])],
                "tris": int(node.get("tris") or 0),
                "verts": int(node.get("verts") or 0),
                "area": round(float(node.get("area") or 0.0), 6),
                "volume": round(float(node.get("volume") or 0.0), 6),
                "mat": node["mat"].lower(),
                "matclass": node["matclass"].lower(),
                "modifiers": [
                    (
                        str(name).lower(),
                        str(node.get("modcls", [])[index]).lower()
                        if index < len(node.get("modcls", [])) else "",
                        bool(node.get("modenabled", [])[index])
                        if index < len(node.get("modenabled", [])) else True,
                    )
                    for index, name in enumerate(node.get("mods", []))
                ],
                "boolops": sorted(str(v).lower() for v in node.get("boolops", [])),
                "scale": [round(float(v), 5) for v in node["scale"]],
                "hidden": bool(node.get("hidden")),
                "renderable": bool(node.get("renderable", True)),
                "wirecolor": [round(float(v), 3) for v in node.get("wirecolor", [])],
                "handle": str(node.get("handle") or ""),
                "maps": sorted(
                    (str(item["name"]).lower(), str(item["class"]).lower())
                    for item in census["maps"].get(node["name"].lower(), [])
                ),
            }
        )
    return _stable_hash(
        {
            "spec": _spec_fingerprint(ledger),
            "unit_scale": round(float(census.get("unit_scale") or 1.0), 8),
            "root": {
                "pos": [round(float(v), 5) for v in census["root"]["pos"]],
                "scale": [round(float(v), 5) for v in census["root"].get("scale", [])],
                "rotation": [round(float(v), 6) for v in census["root"].get("rotation", [])],
                "hidden": bool(census["root"].get("hidden")),
                "layer": str(census["root"].get("layer") or ""),
            },
            "nodes": nodes,
            "material_params": sorted(
                (str(owner).lower(), str(material).lower(), str(param).lower(), str(value))
                for (owner, material, param), value in census.get("mparams", {}).items()
            ),
            "scene_roots": sorted(
                (
                    str(item.get("handle") or ""),
                    str(item["name"]).lower(),
                    str(item["class"]).lower(),
                )
                for item in census["scene_roots"]
            ),
        }
    )


def _file_digest(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _png_digest(path: str) -> str:
    """Return a digest only for a non-empty PNG with a valid IHDR."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        if os.path.getsize(path) < 33:
            return ""
        with open(path, "rb") as stream:
            header = stream.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            return ""
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        if width <= 0 or height <= 0:
            return ""
    except OSError:
        return ""
    return _file_digest(path)


def _discard_temp_capture(path: str, *, keep: str = "") -> None:
    """Delete only a capture produced under the OS temp root."""
    if not path or os.path.abspath(path) == os.path.abspath(keep or ""):
        return
    try:
        resolved = os.path.abspath(path)
        temp_root = os.path.abspath(tempfile.gettempdir())
        if os.path.commonpath([resolved, temp_root]) != temp_root:
            return
        if os.path.isfile(resolved):
            os.remove(resolved)
    except (OSError, ValueError):
        pass


def _form_signature(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseclass": str(node.get("baseclass") or node.get("class") or "").lower(),
        "tris": int(node.get("tris") or 0),
        "verts": int(node.get("verts") or 0),
        "area": round(float(node.get("area") or 0.0), 6),
        "volume": round(float(node.get("volume") or 0.0), 6),
        "modcls": [str(value).lower() for value in node.get("modcls", [])],
        "modenabled": [bool(value) for value in node.get("modenabled", [])],
        "boolops": sorted(str(value).lower() for value in node.get("boolops", [])),
    }


def _anchor_matches(value: str, detail_id: str) -> bool:
    """Match a whole anchor token (`fuller`, `fuller_cut`), never substrings."""
    return _whole_token(value, detail_id)


# ---------------------------------------------------------------------------
# Spec validation


def _violation(
    gate: str,
    message: str,
    component: str = "",
    *,
    code: str = "",
    actual: Any = None,
    target: Any = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"gate": gate, "message": message}
    if component:
        out["component"] = component
    if code:
        out["code"] = code
    if actual is not None:
        out["actual"] = actual
    if target is not None:
        out["target"] = target
    return out


def _as_float3(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def _normalize_class(value: str) -> str:
    return re.sub(r"[\s_]", "", value).lower()


def _valid_item_name(value: Any, *, limit: int = MAX_ITEM_NAME) -> bool:
    text = str(value or "")
    return bool(
        text
        and text == text.strip()
        and len(text) <= limit
        and _NAME_RE.fullmatch(text)
    )


def _whole_token(value: str, token: str) -> bool:
    escaped = re.escape(token.strip())
    return bool(escaped) and re.search(
        rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])",
        value,
        re.IGNORECASE,
    ) is not None


def _evidence_hedges(value: str) -> list[str]:
    """Find refine-language as whole phrases, excluding explicit negation."""
    hits: list[str] = []
    for phrase in HEDGE_WORDS:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(value):
            prefix = value[max(0, match.start() - 24):match.start()]
            if re.search(r"\b(?:no|not|without)\s+(?:\w+\s+){0,2}$", prefix, re.IGNORECASE):
                continue
            hits.append(phrase)
            break
    return hits


def _merge_named_items(
    existing: list[Any],
    updates: Any,
    *,
    key: str,
) -> tuple[list[Any], int]:
    if not isinstance(updates, list):
        raise ValueError(f"patch.{key}s must be a list")
    result = copy.deepcopy(existing)
    index = {
        str(item.get(key)).lower(): idx
        for idx, item in enumerate(result)
        if isinstance(item, dict) and item.get(key)
    }
    changed = 0
    for update in updates:
        if not isinstance(update, dict) or not update.get(key):
            raise ValueError(f"patch.{key}s entries need {key}")
        token = str(update[key]).lower()
        if token in index:
            merged = dict(result[index[token]])
            merged.update(update)
            result[index[token]] = merged
        else:
            index[token] = len(result)
            result.append(copy.deepcopy(update))
        changed += 1
    return result, changed


def _apply_spec_update(
    ledger: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    """Apply whole-section replacements plus compact keyed patches to a copy."""
    candidate = copy.deepcopy(ledger)
    violations: list[dict[str, Any]] = []
    stats = {"components": 0, "materials": 0, "details": 0, "removed": 0}
    fields = {
        "components", "materials", "details", "budget", "reference", "complexity",
        "tolerance_pct", "object", "units", "review", "assumptions",
    }
    unknown = sorted(set(payload) - fields - {"patch", "remove"})
    if unknown:
        violations.append(_violation("spec", f"unknown spec field(s): {unknown}", code="unknown-field"))

    for field in fields:
        if field in payload:
            candidate[field] = copy.deepcopy(payload[field])

    patch = payload.get("patch")
    if patch is not None:
        if not isinstance(patch, dict):
            violations.append(_violation("spec", "patch must be an object", code="bad-patch"))
        else:
            try:
                if "components" in patch:
                    candidate["components"], stats["components"] = _merge_named_items(
                        candidate.get("components", []), patch["components"], key="name"
                    )
                if "materials" in patch:
                    candidate["materials"], stats["materials"] = _merge_named_items(
                        candidate.get("materials", []), patch["materials"], key="name"
                    )
                if "details" in patch:
                    candidate["details"], stats["details"] = _merge_named_items(
                        candidate.get("details", []), patch["details"], key="id"
                    )
                extra = sorted(set(patch) - {"components", "materials", "details"})
                if extra:
                    raise ValueError(f"unknown patch section(s): {extra}")
            except ValueError as exc:
                violations.append(_violation("spec", str(exc), code="bad-patch"))

    remove = payload.get("remove")
    if remove is not None:
        if not isinstance(remove, dict):
            violations.append(_violation("spec", "remove must be an object", code="bad-remove"))
        else:
            for section, key in (("components", "name"), ("materials", "name"), ("details", "id")):
                if section not in remove:
                    continue
                values = remove[section]
                if not isinstance(values, list):
                    violations.append(
                        _violation("spec", f"remove.{section} must be a list", code="bad-remove")
                    )
                    continue
                doomed = {str(value).lower() for value in values}
                before = len(candidate.get(section, []))
                candidate[section] = [
                    item
                    for item in candidate.get(section, [])
                    if not isinstance(item, dict) or str(item.get(key) or "").lower() not in doomed
                ]
                stats["removed"] += before - len(candidate[section])
            extra = sorted(set(remove) - {"components", "materials", "details"})
            if extra:
                violations.append(
                    _violation("spec", f"unknown remove section(s): {extra}", code="bad-remove")
                )
    return candidate, violations, stats


def _validate_spec(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject underspecified or trivially gameable blueprints before modeling."""
    v: list[dict[str, Any]] = []
    complexity = str(ledger.get("complexity") or "moderate").lower()
    if complexity not in COMPLEXITY_FLOORS:
        v.append(_violation("spec", f"complexity must be one of {sorted(COMPLEXITY_FLOORS)}"))
        complexity = "moderate"
    min_comps, min_details = COMPLEXITY_FLOORS[complexity]

    raw_comps = ledger.get("components")
    raw_mats = ledger.get("materials")
    raw_details = ledger.get("details")
    budget = ledger.get("budget")
    comps = raw_comps if isinstance(raw_comps, list) else []
    mats = raw_mats if isinstance(raw_mats, list) else []
    details = raw_details if isinstance(raw_details, list) else []
    if not isinstance(raw_comps, list):
        v.append(_violation("spec", "components must be a list"))
    if not isinstance(raw_mats, list):
        v.append(_violation("spec", "materials must be a list"))
    if not isinstance(raw_details, list):
        v.append(_violation("spec", "details must be a list"))
    if not isinstance(budget, dict):
        v.append(_violation("spec", "budget must be an object"))
        budget = {}
    if not str(ledger.get("object") or "").strip() and not str(ledger.get("reference") or "").strip():
        v.append(_violation("spec", "object description or reference image is required"))
    if len(str(ledger.get("object") or "")) > 500:
        v.append(_violation("spec", "object description is capped at 500 characters"))
    if len(comps) > MAX_COMPONENTS:
        v.append(_violation("spec", f"components are capped at {MAX_COMPONENTS}"))
    if len(mats) > MAX_MATERIALS:
        v.append(_violation("spec", f"materials are capped at {MAX_MATERIALS}"))
    if len(details) > MAX_DETAILS:
        v.append(_violation("spec", f"details are capped at {MAX_DETAILS}"))
    units = str(ledger.get("units") or "cm").lower()
    if units not in UNIT_SUFFIXES:
        v.append(_violation("spec", f"units must be one of {sorted(UNIT_SUFFIXES)}"))

    mat_names: set[str] = set()
    comp_names: set[str] = set()
    for mat in mats:
        if not isinstance(mat, dict) or not mat.get("name") or not mat.get("class"):
            v.append(_violation("spec", f"material needs name + class: {mat!r}"))
            continue
        extra_fields = sorted(set(mat) - MATERIAL_FIELDS)
        if extra_fields:
            v.append(_violation("spec", f"material {mat.get('name')}: unknown field(s) {extra_fields}"))
        mname = str(mat["name"])
        mclass = str(mat["class"])
        if not _valid_item_name(mname):
            v.append(_violation("spec", f"material name must be <= {MAX_ITEM_NAME} safe characters: {mname}"))
        if mname.lower() in mat_names:
            v.append(_violation("spec", f"duplicate material name: {mname}"))
        mat_names.add(mname.lower())
        if _normalize_class(mclass) == "physical":
            v.append(
                _violation(
                    "spec",
                    f"material {mname}: class 'Physical' is a camera; use 'PhysicalMaterial'",
                    code="material-class",
                    actual=mclass,
                    target="PhysicalMaterial",
                )
            )
        params = mat.get("params")
        if params is not None and not isinstance(params, dict):
            v.append(_violation("spec", f"material {mname}: params must be an object"))
        elif isinstance(params, dict):
            if len(params) > 32:
                v.append(_violation("spec", f"material {mname}: params are capped at 32"))
            for param_name in params:
                if not _valid_item_name(param_name):
                    v.append(
                        _violation(
                            "spec",
                            f"material {mname}: invalid or overlong param name {param_name!r}",
                        )
                    )

    for comp in comps:
        if not isinstance(comp, dict) or not comp.get("name"):
            v.append(_violation("spec", f"component needs a name: {comp!r}"))
            continue
        extra_fields = sorted(set(comp) - COMPONENT_FIELDS)
        if extra_fields:
            v.append(_violation("spec", f"component {comp.get('name')}: unknown field(s) {extra_fields}"))
        cname = str(comp["name"])
        if not _valid_item_name(cname):
            v.append(
                _violation(
                    "spec",
                    f"name must be <= {MAX_ITEM_NAME} letters/digits/space/_/-",
                    cname,
                )
            )
        if cname.lower() in comp_names:
            v.append(_violation("spec", "duplicate component name", cname))
        comp_names.add(cname.lower())
        kind = str(comp.get("kind") or "geometry").lower()
        if kind not in {"geometry", "helper", "shape"}:
            v.append(_violation("spec", f"kind must be geometry|helper|shape, got {kind}", cname))
        dims = _as_float3(comp.get("dims"))
        if kind == "geometry":
            if dims is None or min(dims) <= 0 or not all(math.isfinite(value) for value in dims):
                v.append(_violation("spec", "geometry dims must be 3 finite positive numbers", cname))
        elif comp.get("dims") is not None and (
            dims is None
            or min(dims) < 0
            or max(dims) <= 0
            or not all(math.isfinite(value) for value in dims)
        ):
            v.append(
                _violation(
                    "spec",
                    f"{kind} dims, when supplied, must be 3 finite non-negative numbers with some extent",
                    cname,
                )
            )
        center = _as_float3(comp.get("center"))
        if kind == "geometry" and (center is None or not all(math.isfinite(value) for value in center)):
            v.append(
                _violation(
                    "spec",
                    "geometry component needs center:[x,y,z] relative to the builder root",
                    cname,
                    code="missing-center",
                )
            )
        elif comp.get("center") is not None and (
            center is None or not all(math.isfinite(value) for value in center)
        ):
            v.append(_violation("spec", "center must be 3 finite numbers", cname))
        axis_dims = comp.get("axis_dims")
        parsed_axis_dims = _as_float3(axis_dims) if axis_dims is not None else None
        if axis_dims is not None and (
            parsed_axis_dims is None
            or min(parsed_axis_dims) <= 0
            or not all(math.isfinite(value) for value in parsed_axis_dims)
        ):
            v.append(_violation("spec", "axis_dims must be 3 finite positive world-axis sizes", cname))
        if kind == "geometry" and not comp.get("material"):
            v.append(_violation("spec", "geometry component needs a material ref", cname))
        if "primitive" in comp and not isinstance(comp["primitive"], bool):
            v.append(_violation("spec", "primitive must be true or false", cname))
        if "nested" in comp and not isinstance(comp["nested"], bool):
            v.append(_violation("spec", "nested must be true or false", cname))
        for boolean_key in ("ground", "floating"):
            if boolean_key in comp and not isinstance(comp[boolean_key], bool):
                v.append(_violation("spec", f"{boolean_key} must be true or false", cname))

    for comp in comps:
        if not isinstance(comp, dict) or not comp.get("name"):
            continue
        cname = str(comp["name"])
        own = cname.lower()
        mat_ref = str(comp.get("material") or "")
        if mat_ref and mat_ref.lower() not in mat_names:
            v.append(_violation("spec", f"material ref not in materials: {mat_ref}", cname))
        parent = str(comp.get("parent") or "")
        if parent:
            if parent.lower() == own:
                v.append(_violation("spec", "component cannot parent itself", cname, code="self-relation"))
            elif parent.lower() != "root" and parent.lower() not in comp_names:
                v.append(_violation("spec", f"parent references unknown component: {parent}", cname))
        ratios = comp.get("ratios")
        if ratios is not None:
            if not isinstance(ratios, dict):
                v.append(_violation("spec", "ratios must be an object of component->number", cname))
            else:
                for other, ratio in ratios.items():
                    other_name = str(other).lower()
                    if other_name == own:
                        v.append(_violation("spec", "ratio cannot reference itself", cname, code="self-relation"))
                    elif other_name not in comp_names:
                        v.append(_violation("spec", f"ratio references unknown component: {other}", cname))
                    try:
                        numeric = float(ratio)
                        if numeric <= 0 or not math.isfinite(numeric):
                            raise ValueError
                    except (TypeError, ValueError):
                        v.append(_violation("spec", f"ratio to {other} must be finite and positive", cname))
        sym = comp.get("symmetry")
        if sym is not None and str(sym).lower() not in {"x", "y", "z"}:
            v.append(_violation("spec", "symmetry must be x, y, or z", cname))
        mirror = str(comp.get("mirror_of") or "")
        if mirror:
            if mirror.lower() == own:
                v.append(_violation("spec", "mirror_of cannot reference itself", cname, code="self-relation"))
            elif mirror.lower() not in comp_names:
                v.append(_violation("spec", f"mirror_of references unknown component: {mirror}", cname))
            if str(comp.get("mirror_axis") or "x").lower() not in {"x", "y", "z"}:
                v.append(_violation("spec", "mirror_axis must be x, y, or z", cname))
        nested_in = str(comp.get("nested_in") or "")
        if comp.get("nested") and not nested_in:
            v.append(
                _violation(
                    "spec",
                    "nested:true requires nested_in naming the containing component",
                    cname,
                    code="ambiguous-nesting",
                )
            )
        if nested_in:
            if nested_in.lower() == own:
                v.append(_violation("spec", "nested_in cannot reference itself", cname, code="self-relation"))
            elif nested_in.lower() not in comp_names:
                v.append(_violation("spec", f"nested_in references unknown component: {nested_in}", cname))
        touches = comp.get("touches")
        if touches is not None:
            if not isinstance(touches, list):
                v.append(_violation("spec", "touches must be a list of component names", cname))
            else:
                lowered = [str(other).lower() for other in touches]
                if len(lowered) != len(set(lowered)):
                    v.append(_violation("spec", "touches contains duplicates", cname))
                for other, other_name in zip(touches, lowered):
                    if other_name == own:
                        v.append(_violation("spec", "touches cannot reference itself", cname, code="self-relation"))
                    elif other_name not in comp_names:
                        v.append(_violation("spec", f"touches references unknown component: {other}", cname))
        if complexity != "simple" and kind_is_geometry(comp):
            relational = any(
                comp.get(key)
                for key in (
                    "ratios", "symmetry", "mirror_of", "ground", "touches",
                    "parent", "nested_in",
                )
            )
            if not relational:
                v.append(
                    _violation(
                        "spec",
                        "needs a relational constraint (ratio/symmetry/mirror/ground/touch/parent)",
                        cname,
                    )
                )

    parent_of = {
        str(comp["name"]).lower(): str(comp.get("parent") or "").lower()
        for comp in comps
        if isinstance(comp, dict) and comp.get("name")
        and str(comp.get("parent") or "").lower() not in {"", "root"}
    }
    reported_cycles: set[frozenset[str]] = set()
    for start_name in parent_of:
        chain: list[str] = []
        current = start_name
        while current in parent_of:
            if current in chain:
                cycle = chain[chain.index(current):] + [current]
                identity = frozenset(cycle)
                if identity not in reported_cycles:
                    reported_cycles.add(identity)
                    v.append(
                        _violation(
                            "spec",
                            f"parent cycle: {' -> '.join(cycle)}",
                            start_name,
                            code="parent-cycle",
                        )
                    )
                break
            chain.append(current)
            current = parent_of[current]

    detail_ids: set[str] = set()
    critical_details = 0
    for det in details:
        if not isinstance(det, dict) or not det.get("id") or not det.get("on"):
            v.append(_violation("spec", f"detail needs id + on: {det!r}"))
            continue
        extra_fields = sorted(set(det) - DETAIL_FIELDS)
        if extra_fields:
            v.append(_violation("spec", f"detail {det.get('id')}: unknown field(s) {extra_fields}"))
        did = str(det["id"])
        if not _valid_item_name(did):
            v.append(_violation("spec", f"detail id must be <= {MAX_ITEM_NAME} safe characters: {did}"))
        if did.lower() in detail_ids:
            v.append(_violation("spec", f"duplicate detail id: {did}"))
        if did.lower() in comp_names:
            v.append(_violation("spec", f"detail id collides with component name: {did}"))
        detail_ids.add(did.lower())
        if str(det["on"]).lower() not in comp_names:
            v.append(_violation("spec", f"detail {did}: 'on' references unknown component: {det['on']}"))
        via = str(det.get("via") or "").lower()
        if via not in VIA:
            v.append(_violation("spec", f"detail {did}: via must be one of {sorted(VIA)}"))
        priority = str(det.get("priority") or "").lower()
        if priority and priority not in {"critical", "important", "support"}:
            v.append(_violation("spec", f"detail {did}: priority must be critical|important|support"))
        if priority == "critical":
            critical_details += 1
        if complexity != "simple":
            if len(did.strip()) < 3 or _GENERIC_DETAIL_RE.fullmatch(did.strip()):
                v.append(_violation("spec", f"detail id must name the observed feature, not '{did}'"))
            if len(str(det.get("description") or "").strip()) < 8:
                v.append(_violation("spec", f"detail {did}: description must state the observed target"))
            elif len(str(det.get("description") or "")) > 240:
                v.append(_violation("spec", f"detail {did}: description is capped at 240 characters"))
            if not priority:
                v.append(_violation("spec", f"detail {did}: priority is required at {complexity} complexity"))
            if "count" not in det:
                v.append(_violation("spec", f"detail {did}: count is required at {complexity} complexity"))
        count = det.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            v.append(_violation("spec", f"detail {did}: count must be a positive integer"))

    geometry = [c for c in comps if isinstance(c, dict) and kind_is_geometry(c)]
    geometry_comps = len(geometry)
    if geometry_comps < min_comps:
        v.append(
            _violation(
                "spec",
                f"{complexity} complexity needs >= {min_comps} geometry components, got {geometry_comps}",
            )
        )
    if len(details) < min_details:
        v.append(
            _violation(
                "spec",
                f"{complexity} complexity needs >= {min_details} detail inventory entries, got {len(details)}",
            )
        )
    if complexity != "simple" and details and critical_details == 0:
        v.append(_violation("spec", "mark at least one identity-defining detail priority:critical"))
    primitive_count = sum(1 for comp in geometry if comp.get("primitive") is True)
    primitive_limit = max(1, math.floor(geometry_comps * 0.35))
    if complexity != "simple" and primitive_count > primitive_limit:
        v.append(
            _violation(
                "spec",
                f"primitive exemptions {primitive_count} exceed {primitive_limit}; shape the identity masses",
                code="primitive-abuse",
            )
        )

    raw_max_tris = budget.get("tris", 0)
    extra_budget_fields = sorted(set(budget) - BUDGET_FIELDS)
    if extra_budget_fields:
        v.append(_violation("spec", f"budget has unknown field(s) {extra_budget_fields}"))
    if (
        not isinstance(raw_max_tris, int)
        or isinstance(raw_max_tris, bool)
        or raw_max_tris <= 0
    ):
        max_tris = 0
        v.append(_violation("spec", "budget.tris must be a positive integer"))
    else:
        max_tris = raw_max_tris
    if budget.get("min_tris") is not None:
        floor_tris = budget["min_tris"]
        if (
            not isinstance(floor_tris, int)
            or isinstance(floor_tris, bool)
            or floor_tris <= 0
            or floor_tris >= max_tris
        ):
            v.append(_violation("spec", "budget.min_tris must be positive and below budget.tris"))

    reference = str(ledger.get("reference") or "")
    if reference and not os.path.isfile(reference):
        v.append(_violation("spec", f"reference image not found on disk: {reference}"))
    try:
        raw_tolerance = ledger.get("tolerance_pct", DEFAULT_TOLERANCE_PCT)
        tolerance = float(DEFAULT_TOLERANCE_PCT if raw_tolerance is None else raw_tolerance)
        if not math.isfinite(tolerance) or tolerance <= 0 or tolerance > MAX_TOLERANCE_PCT:
            raise ValueError
    except (TypeError, ValueError):
        v.append(_violation("spec", f"tolerance_pct must be finite and in (0,{MAX_TOLERANCE_PCT}]"))
    review = ledger.get("review")
    if review is not None and not isinstance(review, dict):
        v.append(_violation("spec", "review must be an object"))
    elif isinstance(review, dict) and "threshold" in review:
        try:
            threshold = float(review["threshold"])
            if not math.isfinite(threshold) or threshold < DEFAULT_VISUAL_THRESHOLD or threshold > 1.0:
                raise ValueError
        except (TypeError, ValueError):
            v.append(
                _violation(
                    "spec",
                    f"review.threshold must be finite and between {DEFAULT_VISUAL_THRESHOLD} and 1.0",
                )
            )
    if isinstance(review, dict):
        extra_review_fields = sorted(set(review) - REVIEW_FIELDS)
        if extra_review_fields:
            v.append(_violation("spec", f"review has unknown field(s) {extra_review_fields}"))
    assumptions = ledger.get("assumptions", [])
    if not isinstance(assumptions, list):
        v.append(_violation("spec", "assumptions must be a list"))
    elif (
        len(assumptions) > 32
        or any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in assumptions)
    ):
        v.append(_violation("spec", "assumptions must be 1-32 non-empty strings of at most 240 characters"))
    return v


def kind_is_geometry(comp: dict[str, Any]) -> bool:
    return str(comp.get("kind") or "geometry").lower() == "geometry"


# ---------------------------------------------------------------------------
# Census: one MAXScript round trip reading everything the gates need


def _parse_triple(token: str) -> list[float]:
    parts = token.split(",")
    out = []
    for p in parts[:3]:
        try:
            out.append(float(p))
        except ValueError:
            out.append(0.0)
    while len(out) < 3:
        out.append(0.0)
    return out


def _safe_float(token: Any, default: float = 0.0) -> float:
    try:
        value = float(token)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _parse_numbers(token: str, count: int, defaults: list[float]) -> list[float]:
    values = []
    for part in token.split(",")[:count]:
        values.append(_safe_float(part))
    return values + defaults[len(values):count]


def _decode_list_token(token: str) -> str:
    """Decode compact comma-list items emitted by ``bldListClean``."""
    return token.replace("%2C", ",").replace("%25", "%").replace("<pipe>", "|")


def _census(
    root_name: str,
    spec_materials: list[dict[str, Any]],
    units: str = "cm",
    *,
    include_material_params: bool = True,
    include_maps: bool = True,
) -> dict[str, Any]:
    safe = safe_string(root_name)
    suffix = UNIT_SUFFIXES.get(str(units).lower(), "cm")
    unit_expr = "1.0" if not suffix else f'units.decodeValue "1.0{suffix}"'
    probes = []
    for mat in spec_materials if include_material_params else []:
        mname = safe_string(str(mat.get("name") or ""))
        for key in (mat.get("params") or {}):
            k = safe_string(str(key))
            probes.append(
                f'for pn in queue where pn.material != undefined and (stricmp pn.material.name "{mname}") == 0 do '
                f'(try (format "MPARAM|%|{mname}|{k}|%\\n" (bldClean pn.name) '
                f'((getProperty pn.material "{k}") as string) to:out) '
                f'catch (format "MPARAM|%|{mname}|{k}|__ERR__\\n" (bldClean pn.name) to:out))'
            )
    probe_block = "\n".join(f"    {line}" for line in probes)
    map_block = ""
    if include_maps:
        map_block = """
        if n.material != undefined do (
            try (
                local seenMaps = #()
                local seenMaterials = #()
                bldWalkMaterial n.name n.material 0 seenMaterials seenMaps out
            ) catch ()
        )"""
    script = f"""(
fn bldClean s = (
    local t = s as string
    t = substituteString t "|" "<pipe>"
    t = substituteString t "\\n" " "
    substituteString t "\\r" ""
)
fn bldListClean s = (
    local t = bldClean s
    t = substituteString t "%" "%25"
    substituteString t "," "%2C"
)
fn bldWalkMaps owner tex depth visited out = (
    if tex != undefined and depth <= 12 and (findItem visited tex) == 0 do (
        append visited tex
        format "MAP|%|%|%\\n" (bldClean owner) (bldClean tex.name) ((classof tex) as string) to:out
        try (
            for ti = 1 to (getNumSubTexmaps tex) do (
                local child = getSubTexmap tex ti
                if child != undefined do bldWalkMaps owner child (depth + 1) visited out
            )
        ) catch ()
    )
)
fn bldWalkMaterial owner mat depth visitedMats visitedMaps out = (
    if mat != undefined and depth <= 12 and (findItem visitedMats mat) == 0 do (
        append visitedMats mat
        try (
            for ti = 1 to (getNumSubTexmaps mat) do (
                local tex = getSubTexmap mat ti
                if tex != undefined do bldWalkMaps owner tex 0 visitedMaps out
            )
        ) catch ()
        try (
            for mi = 1 to (getNumSubMtls mat) do (
                local childMaterial = getSubMtl mat mi
                if childMaterial != undefined do (
                    bldWalkMaterial owner childMaterial (depth + 1) visitedMats visitedMaps out
                )
            )
        ) catch ()
    )
)
local matches = for o in objects where (stricmp o.name "{safe}") == 0 collect o
if matches.count == 0 then (
    "__ERROR__|Root not found: {safe}"
) else if matches.count > 1 then (
    "__ERROR__|Multiple nodes are named {safe}"
) else (
    local root = matches[1]
    local out = stringstream ""
    local unitScale = try ({unit_expr}) catch (1.0)
    format "UNIT|%|%\\n" unitScale (bldClean (units.SystemType as string)) to:out
    local rr = root.rotation
    local rootLayer = try (bldClean root.layer.name) catch ("")
    format "ROOT|%|%,%,%|%,%,%|%,%,%,%|%|%\\n" (bldClean root.name) root.pos.x root.pos.y root.pos.z root.scale.x root.scale.y root.scale.z rr.x rr.y rr.z rr.w root.isHidden rootLayer to:out
    local queue = #()
    for c in root.children do append queue c
    local qi = 1
    while qi <= queue.count do (
        local n = queue[qi]
        qi += 1
        for c in n.children do append queue c
        local bbmin = n.min
        local bbmax = n.max
        local tris = 0
        local verts = 0
        local geomArea = 0.0
        local geomVolume = 0.0
        local snapMesh = undefined
        try (tris = (GetTriMeshFaceCount n)[1]) catch ()
        try (verts = getNumVerts n) catch ()
        try (
            snapMesh = snapshotAsMesh n
            local snapFaces = getNumFaces snapMesh
            local objectTM = n.objectTransform
            for fi = 1 to snapFaces do (
                local face = getFace snapMesh fi
                local p1 = (getVert snapMesh (face.x as integer)) * objectTM
                local p2 = (getVert snapMesh (face.y as integer)) * objectTM
                local p3 = (getVert snapMesh (face.z as integer)) * objectTM
                geomArea += (length (cross (p2 - p1) (p3 - p1))) * 0.5
                geomVolume += (dot p1 (cross p2 p3)) / 6.0
            )
            geomVolume = abs geomVolume
        ) catch ()
        if snapMesh != undefined do try (delete snapMesh) catch ()
        local mname = ""
        local mclass = ""
        if n.material != undefined do (
            mname = bldClean n.material.name
            mclass = (classof n.material) as string
        )
        local mods = ""
        local bops = ""
        local mcls = ""
        local menabled = ""
        for m in n.modifiers do (
            mods += (bldListClean m.name) + ","
            mcls += ((classof m) as string) + ","
            menabled += (if m.enabled then "1" else "0") + ","
            if m.enabled and (classof m) == BooleanMod do (
                try (
                    local bcnt = m.GetNumOperands()
                    for oi = 2 to bcnt do (
                        local onm = ""
                        m.GetFlatOperandName oi &onm
                        bops += (bldListClean onm) + ","
                    )
                ) catch ()
            )
        )
        local lname = ""
        try (lname = bldClean n.layer.name) catch ()
        local bcls = ""
        try (bcls = (classof n.baseobject) as string) catch ()
        local wr = n.wireColor
        local rq = n.rotation
        local nh = try ((getHandleByAnim n) as string) catch ("")
        format "NODE|%|%|%|%|%|%,%,%|%,%,%|%,%,%|%|%|%|%|%,%,%|%|%|%|%|%|%|%|%|%|%,%,%|%,%,%,%|%\\n" (bldClean n.name) ((classof n) as string) ((superclassof n) as string) (bldClean (if n.parent != undefined then n.parent.name else "")) lname n.pos.x n.pos.y n.pos.z bbmin.x bbmin.y bbmin.z bbmax.x bbmax.y bbmax.z (tris as string) mname mclass mods n.scale.x n.scale.y n.scale.z bops mcls bcls (verts as string) menabled geomArea geomVolume n.isHidden n.renderable wr.r wr.g wr.b rq.x rq.y rq.z rq.w nh to:out
{map_block}
    )
{probe_block}
    for o in objects where o.parent == undefined and o != root do (
        local oh = try ((getHandleByAnim o) as string) catch ("")
        format "SROOT|%|%|%\\n" oh (bldClean o.name) ((classof o) as string) to:out
    )
    out as string
)
)"""
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])

    census: dict[str, Any] = {
        "root": {
            "name": root_name,
            "pos": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "rotation": [0.0, 0.0, 0.0, 1.0],
            "hidden": False,
            "layer": "_builder",
        },
        "node_list": [],
        "nodes_by_name": {},
        "maps": {},
        "mparams": {},
        "scene_roots": [],
        "unit_scale": 1.0,
        "system_units": "unknown",
    }
    for line in raw.splitlines():
        if not line.strip():
            continue
        kind, _, rest = line.partition("|")
        if kind == "ROOT":
            f = rest.split("|")
            if len(f) >= 2:
                census["root"] = {
                    "name": f[0].replace("<pipe>", "|"),
                    "pos": _parse_triple(f[1]),
                    "scale": _parse_triple(f[2]) if len(f) > 2 else [1.0, 1.0, 1.0],
                    "rotation": _parse_numbers(f[3], 4, [0.0, 0.0, 0.0, 1.0])
                    if len(f) > 3 else [0.0, 0.0, 0.0, 1.0],
                    "hidden": f[4].lower() == "true" if len(f) > 4 else False,
                    "layer": f[5].replace("<pipe>", "|") if len(f) > 5 else "",
                }
        elif kind == "UNIT":
            scale, _, system_units = rest.partition("|")
            try:
                census["unit_scale"] = max(float(scale), 1e-12)
            except ValueError:
                census["unit_scale"] = 1.0
            census["system_units"] = system_units.replace("<pipe>", "|")
        elif kind == "NODE":
            f = rest.split("|")
            if len(f) < 13:
                continue
            node = {
                "name": f[0].replace("<pipe>", "|"),
                "class": f[1],
                "super": f[2],
                "parent": f[3].replace("<pipe>", "|"),
                "layer": f[4],
                "pos": _parse_triple(f[5]),
                "bbmin": _parse_triple(f[6]),
                "bbmax": _parse_triple(f[7]),
                "tris": int(float(f[8])) if f[8].replace(".", "", 1).lstrip("-").isdigit() else 0,
                "mat": f[9].replace("<pipe>", "|"),
                "matclass": f[10],
                "mods": [_decode_list_token(m) for m in f[11].split(",") if m],
                "scale": _parse_triple(f[12]),
                "boolops": [_decode_list_token(b) for b in f[13].split(",") if b] if len(f) > 13 else [],
                "modcls": [m for m in f[14].split(",") if m] if len(f) > 14 else [],
                "baseclass": f[15] if len(f) > 15 else "",
                "verts": int(float(f[16])) if len(f) > 16 and f[16].replace(".", "", 1).lstrip("-").isdigit() else 0,
                "modenabled": [value == "1" for value in f[17].split(",") if value] if len(f) > 17 else [],
                "area": _safe_float(f[18]) if len(f) > 18 else 0.0,
                "volume": _safe_float(f[19]) if len(f) > 19 else 0.0,
                "hidden": f[20].lower() == "true" if len(f) > 20 else False,
                "renderable": f[21].lower() != "false" if len(f) > 21 else True,
                "wirecolor": _parse_triple(f[22]) if len(f) > 22 else [0.0, 0.0, 0.0],
                "rotation": _parse_numbers(f[23], 4, [0.0, 0.0, 0.0, 1.0])
                if len(f) > 23 else [0.0, 0.0, 0.0, 1.0],
                "handle": f[24] if len(f) > 24 else "",
            }
            census["node_list"].append(node)
            census["nodes_by_name"].setdefault(node["name"].lower(), []).append(node)
        elif kind == "MAP":
            f = rest.split("|")
            if len(f) >= 3:
                census["maps"].setdefault(f[0].replace("<pipe>", "|").lower(), []).append(
                    {"name": f[1].replace("<pipe>", "|"), "class": f[2]}
                )
        elif kind == "MPARAM":
            f = rest.split("|", 3)
            if len(f) >= 4:
                census["mparams"][(f[0].lower(), f[1].lower(), f[2].lower())] = f[3]
        elif kind == "SROOT":
            f = rest.split("|")
            if len(f) >= 3:
                census["scene_roots"].append(
                    {
                        "handle": f[0],
                        "name": f[1].replace("<pipe>", "|"),
                        "class": f[2],
                    }
                )
    return census


# ---------------------------------------------------------------------------
# Gate evaluation (pure Python, zero model tokens)


def _dims(node: dict[str, Any]) -> list[float]:
    return sorted(abs(node["bbmax"][i] - node["bbmin"][i]) for i in range(3))


def _center(node: dict[str, Any]) -> list[float]:
    return [(node["bbmin"][i] + node["bbmax"][i]) / 2.0 for i in range(3)]


def _rel_ok(measured: float, target: float, tol_pct: float) -> bool:
    if target == 0:
        return abs(measured) < 1e-6
    return abs(measured - target) / abs(target) <= tol_pct / 100.0


def _boxes_touch(a: dict[str, Any], b: dict[str, Any], gap: float) -> bool:
    return all(
        a["bbmin"][i] - gap <= b["bbmax"][i] and b["bbmin"][i] - gap <= a["bbmax"][i]
        for i in range(3)
    )


def _axis_dims(node: dict[str, Any]) -> list[float]:
    return [abs(node["bbmax"][i] - node["bbmin"][i]) for i in range(3)]


def _intersection_ratio(a: dict[str, Any], b: dict[str, Any]) -> float:
    overlap = [
        max(0.0, min(a["bbmax"][i], b["bbmax"][i]) - max(a["bbmin"][i], b["bbmin"][i]))
        for i in range(3)
    ]
    intersection = overlap[0] * overlap[1] * overlap[2]
    av = math.prod(max(0.0, a["bbmax"][i] - a["bbmin"][i]) for i in range(3))
    bv = math.prod(max(0.0, b["bbmax"][i] - b["bbmin"][i]) for i in range(3))
    smaller = min(av, bv)
    return intersection / smaller if smaller > 1e-9 else 0.0


def _belongs_to_component(
    candidate: dict[str, Any],
    component_name: str,
    by_name: dict[str, list[dict[str, Any]]],
) -> bool:
    parent = str(candidate.get("parent") or "").lower()
    target = component_name.lower()
    seen: set[str] = set()
    while parent and parent not in seen:
        if parent == target:
            return True
        seen.add(parent)
        parents = by_name.get(parent, [])
        if len(parents) != 1:
            break
        parent = str(parents[0].get("parent") or "").lower()
    return False


def _failure_signature(violations: list[dict[str, Any]]) -> str:
    return _stable_hash(
        sorted(
            (
                str(item.get("gate") or ""),
                str(item.get("code") or ""),
                str(item.get("component") or ""),
                re.sub(r"[-+]?\d+(?:\.\d+)?", "#", str(item.get("message") or "")),
            )
            for item in violations
        )
    )[:12]


def _failure_severity(violations: list[dict[str, Any]]) -> float:
    """Cheap normalized distance used to distinguish progress from a plateau."""
    total = 0.0
    for item in violations:
        actual = item.get("actual")
        target = item.get("target")
        if isinstance(actual, (int, float)) and isinstance(target, (int, float)):
            total += abs(float(actual) - float(target)) / max(abs(float(target)), 1.0)
        elif (
            isinstance(actual, list)
            and isinstance(target, list)
            and len(actual) == len(target)
            and all(isinstance(value, (int, float)) for value in actual + target)
        ):
            total += sum(
                abs(float(measured) - float(wanted)) / max(abs(float(wanted)), 1.0)
                for measured, wanted in zip(actual, target)
            )
        else:
            total += 1.0
    return round(total, 6)


def _review_threshold(ledger: dict[str, Any]) -> float:
    review = ledger.get("review")
    if isinstance(review, dict):
        try:
            return max(
                DEFAULT_VISUAL_THRESHOLD,
                float(review.get("threshold", DEFAULT_VISUAL_THRESHOLD)),
            )
        except (TypeError, ValueError):
            pass
    return DEFAULT_VISUAL_THRESHOLD


def _next_action(pass_name: str, violations: list[dict[str, Any]]) -> dict[str, Any]:
    if violations:
        gates = sorted({str(item.get("gate") or "") for item in violations})
        components = sorted({str(item["component"]) for item in violations if item.get("component")})
        tools: list[str] = []
        for gate in gates:
            for tool in GATE_TOOLS.get(gate, []):
                if tool not in tools:
                    tools.append(tool)
        out: dict[str, Any] = {"do": "fix", "then": "builder_gate.check"}
        if components:
            out["components"] = components
        if tools:
            out["tools"] = tools[:5]
        return out
    return {
        "do": "review-capture",
        "then": "builder_gate.record",
        "requires": ["review_id", "visual_score", "evidence"],
        "pass": pass_name,
    }


def _compact_metrics(
    metrics: dict[str, Any],
    violations: list[dict[str, Any]],
    report: str,
) -> dict[str, Any]:
    if report == "full":
        return metrics
    dirty = {str(item["component"]) for item in violations if item.get("component")}
    compact: dict[str, Any] = {
        "found": int(metrics.get("found_components") or 0),
        "expected": int(metrics.get("expected_components") or 0),
        "tris": int(metrics.get("total_tris") or 0),
        "units": metrics.get("units", "cm"),
    }
    if dirty:
        compact["dirty"] = {
            name: value
            for name, value in metrics.get("components", {}).items()
            if name in dirty
        }
    return compact


def _compare_param(spec_value: Any, measured: str) -> bool:
    measured = measured.strip()
    if isinstance(spec_value, (list, tuple)):
        numeric_text = re.sub(r"\bpoint\d+\b", "", measured, flags=re.IGNORECASE)
        bracketed = re.search(r"\[([^]]+)\]", numeric_text)
        if bracketed:
            numeric_text = bracketed.group(1)
        nums = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", numeric_text)
        if len(nums) < len(spec_value):
            return False
        try:
            return all(
                abs(float(number) - float(target))
                <= max(0.02, abs(float(target)) * 0.05)
                for number, target in zip(nums, spec_value)
            )
        except (TypeError, ValueError):
            return False
    try:
        return abs(float(measured) - float(spec_value)) <= max(0.02, abs(float(spec_value)) * 0.05)
    except (TypeError, ValueError):
        return str(spec_value).strip().lower() == measured.lower()


def _evaluate(ledger: dict[str, Any], census: dict[str, Any]) -> tuple[list[dict], list[str], dict]:
    """Returns (violations, warnings, metrics) for the current pass; gates are
    cumulative so pass N re-checks everything below it."""
    state = ledger["state"]
    pass_name = state["pass"]
    idx = PASSES.index(pass_name)
    raw_tolerance = ledger.get("tolerance_pct", DEFAULT_TOLERANCE_PCT)
    tol_pct = float(DEFAULT_TOLERANCE_PCT if raw_tolerance is None else raw_tolerance)
    unit_scale = max(float(census.get("unit_scale") or 1.0), 1e-12)
    comps = [c for c in ledger["components"] if isinstance(c, dict) and c.get("name")]
    root_pos = census["root"]["pos"]
    by_name = census["nodes_by_name"]

    viols: list[dict] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {
        "components": {},
        "pass": pass_name,
        "units": str(ledger.get("units") or "cm"),
        "system_units": census.get("system_units", "unknown"),
        "expected_components": len(comps),
        "found_components": 0,
    }
    root_scale = list(census["root"].get("scale") or [1.0, 1.0, 1.0])
    root_rotation = list(census["root"].get("rotation") or [0.0, 0.0, 0.0, 1.0])
    if len(root_scale) != 3 or any(abs(float(value) - 1.0) > 0.001 for value in root_scale):
        viols.append(
            _violation(
                "placement",
                "builder root scale must remain [1,1,1]; transform the assembly components, not the ledger root",
                census["root"].get("name", "builder root"),
                code="root-scale",
                actual=root_scale,
                target=[1.0, 1.0, 1.0],
            )
        )
    if (
        len(root_rotation) != 4
        or any(abs(float(value)) > 0.0001 for value in root_rotation[:3])
        or abs(abs(float(root_rotation[3])) - 1.0) > 0.0001
    ):
        viols.append(
            _violation(
                "placement",
                "builder root rotation must remain identity so blueprint centers stay in a stable frame",
                census["root"].get("name", "builder root"),
                code="root-rotation",
                actual=root_rotation,
                target=[0.0, 0.0, 0.0, 1.0],
            )
        )

    found: dict[str, dict[str, Any]] = {}
    for comp in comps:
        cname = str(comp["name"])
        matches = by_name.get(cname.lower(), [])
        if not matches:
            viols.append(_violation("coverage", "no node with this name under root", cname))
        elif len(matches) > 1:
            viols.append(_violation("coverage", f"{len(matches)} nodes share this name under root", cname))
        else:
            node = matches[0]
            # A Shape-based node with mesh output (extruded/lathed/swept spline)
            # is real geometry; a bare profile spline is not.
            comp_kind = str(comp.get("kind") or "geometry").lower()
            node_super = str(node["super"] or "").lower()
            wrong_kind = False
            if comp_kind == "geometry":
                wrong_kind = node_super != "geometryclass" and not (
                    node_super == "shape" and node["tris"] > 0
                )
            elif comp_kind == "shape":
                wrong_kind = node_super != "shape"
            elif comp_kind == "helper":
                wrong_kind = "helper" not in node_super
            if wrong_kind:
                viols.append(
                    _violation(
                        "coverage",
                        f"node is {node['class']} ({node['super']}), not declared kind:{comp_kind}"
                        + (
                            " (spline with no mesh output — add Extrude/Lathe/Sweep or declare kind:shape)"
                            if comp_kind == "geometry" and node_super == "shape"
                            else ""
                        ),
                        cname,
                    )
                )
            else:
                found[cname.lower()] = node

    metrics["found_components"] = len(found)

    assembly_dim = 1.0
    if found:
        lo = [min(n["bbmin"][i] for n in found.values()) for i in range(3)]
        hi = [max(n["bbmax"][i] for n in found.values()) for i in range(3)]
        assembly_dim = max(max(hi[i] - lo[i] for i in range(3)), 1e-6)
    placement_fraction = min(0.02, tol_pct / 100.0)
    assembly_floor = max(unit_scale * 1e-4, 0.002 * assembly_dim)

    def component_tolerance(node: dict[str, Any]) -> float:
        return max(assembly_floor, max(_axis_dims(node), default=0.0) * placement_fraction)

    def pair_gap(first: dict[str, Any], second: dict[str, Any]) -> float:
        local_size = min(
            max(_axis_dims(first), default=0.0),
            max(_axis_dims(second), default=0.0),
        )
        return max(assembly_floor, local_size * placement_fraction)

    valid_nested: set[str] = set()
    for comp in comps:
        cname = str(comp["name"])
        node = found.get(cname.lower())
        if node is None:
            continue
        world_dims = _dims(node)
        dims = [value / unit_scale for value in world_dims]
        axis_dims = [value / unit_scale for value in _axis_dims(node)]
        center = [(_center(node)[i] - root_pos[i]) / unit_scale for i in range(3)]
        node_tol = component_tolerance(node)
        metrics["components"][cname] = {
            "dims": [round(d, 3) for d in dims],
            "tris": node["tris"],
            "verts": node.get("verts", 0),
            "area": round(float(node.get("area") or 0.0), 3),
            "volume": round(float(node.get("volume") or 0.0), 3),
            "center": [round(value, 3) for value in center],
        }
        if node.get("hidden"):
            viols.append(
                _violation(
                    "coverage",
                    "declared component is hidden and cannot appear in review capture",
                    cname,
                    code="hidden-component",
                )
            )
        if kind_is_geometry(comp) and not node.get("renderable", True):
            viols.append(
                _violation(
                    "coverage",
                    "declared geometry is non-renderable and cannot be framed as proof",
                    cname,
                    code="nonrenderable-component",
                )
            )
        spec_dims = _as_float3(comp.get("dims"))
        if spec_dims:
            target = sorted(spec_dims)
            for m, t in zip(dims, target):
                if not _rel_ok(m, t, tol_pct):
                    viols.append(
                        _violation(
                            "proportion",
                            f"sorted dims {[round(d, 2) for d in dims]} vs spec "
                            f"{[round(t2, 2) for t2 in target]} (tol {tol_pct}%)",
                            cname,
                            code="dims",
                            actual=[round(value, 3) for value in dims],
                            target=[round(value, 3) for value in target],
                        )
                    )
                    break
        expected_axis_dims = _as_float3(comp.get("axis_dims"))
        if expected_axis_dims and any(
            not _rel_ok(measured, target, tol_pct)
            for measured, target in zip(axis_dims, expected_axis_dims)
        ):
            viols.append(
                _violation(
                    "proportion",
                    "world-axis dimensions do not match axis_dims",
                    cname,
                    code="axis-dims",
                    actual=[round(value, 3) for value in axis_dims],
                    target=[round(value, 3) for value in expected_axis_dims],
                )
            )
        expected_center = _as_float3(comp.get("center"))
        if expected_center and any(
            abs(measured - target) > node_tol / unit_scale
            for measured, target in zip(center, expected_center)
        ):
            viols.append(
                _violation(
                    "placement",
                    f"center is outside {node_tol / unit_scale:.3f} {ledger.get('units') or 'cm'} tolerance",
                    cname,
                    code="center",
                    actual=[round(value, 3) for value in center],
                    target=[round(value, 3) for value in expected_center],
                )
            )
        expected_parent = str(comp.get("parent") or "")
        if expected_parent:
            target_parent = census["root"]["name"] if expected_parent.lower() == "root" else expected_parent
            if node["parent"].lower() != target_parent.lower():
                viols.append(
                    _violation(
                        "placement",
                        f"parent is '{node['parent'] or '(scene root)'}', spec says '{target_parent}'",
                        cname,
                        code="parent",
                        actual=node["parent"],
                        target=target_parent,
                    )
                )
        for other, ratio in (comp.get("ratios") or {}).items():
            onode = found.get(str(other).lower())
            if onode is None:
                continue
            longest, olongest = _dims(node)[2], _dims(onode)[2]
            if olongest <= 1e-6:
                continue
            measured = longest / olongest
            metrics["components"][cname][f"ratio_to_{other}"] = round(measured, 3)
            if not _rel_ok(measured, float(ratio), tol_pct):
                viols.append(
                    _violation(
                        "proportion",
                        f"longest-dim ratio to {other} is {measured:.2f}, spec {float(ratio):.2f}",
                        cname,
                    )
                )
        if comp.get("ground"):
            if abs(node["bbmin"][2] - root_pos[2]) > node_tol:
                viols.append(
                    _violation(
                        "relation",
                        f"ground contact: bbox min z {node['bbmin'][2]:.2f} vs root z {root_pos[2]:.2f}",
                        cname,
                    )
                )
        sym = str(comp.get("symmetry") or "").lower()
        if sym in {"x", "y", "z"}:
            ax = {"x": 0, "y": 1, "z": 2}[sym]
            off = _center(node)[ax] - root_pos[ax]
            if abs(off) > node_tol:
                viols.append(
                    _violation("relation", f"declared {sym}-symmetric but center is off by {off:.2f}", cname)
                )
        mirror = str(comp.get("mirror_of") or "")
        if mirror:
            onode = found.get(mirror.lower())
            if onode is not None:
                ax = {"x": 0, "y": 1, "z": 2}.get(str(comp.get("mirror_axis") or "x").lower(), 0)
                a = [_center(node)[i] - root_pos[i] for i in range(3)]
                b = [_center(onode)[i] - root_pos[i] for i in range(3)]
                mirror_tol = max(node_tol, component_tolerance(onode))
                bad = abs(a[ax] + b[ax]) > mirror_tol or any(
                    abs(a[i] - b[i]) > mirror_tol for i in range(3) if i != ax
                )
                if not bad:
                    da, db = _dims(node), _dims(onode)
                    bad = any(not _rel_ok(m, t, tol_pct) for m, t in zip(da, db))
                if bad:
                    viols.append(_violation("relation", f"not a mirror of {mirror} across {'xyz'[ax]}", cname))
        for other in comp.get("touches") or []:
            onode = found.get(str(other).lower())
            if onode is None:
                continue
            if not _boxes_touch(node, onode, pair_gap(node, onode)):
                viols.append(_violation("relation", f"declared touching {other} but bboxes are apart", cname))
        nested_in = str(comp.get("nested_in") or "")
        if nested_in:
            container = found.get(nested_in.lower())
            if container is not None:
                nesting_tol = pair_gap(node, container)
                contained = all(
                    node["bbmin"][axis] >= container["bbmin"][axis] - nesting_tol
                    and node["bbmax"][axis] <= container["bbmax"][axis] + nesting_tol
                    for axis in range(3)
                )
                meaningfully_smaller = any(
                    outer - inner > nesting_tol
                    for inner, outer in zip(_axis_dims(node), _axis_dims(container))
                )
                if contained and meaningfully_smaller:
                    valid_nested.add(cname.lower())
                else:
                    viols.append(
                        _violation(
                            "relation",
                            f"declared nested_in {nested_in}, but the live bbox is not a distinct contained mass",
                            cname,
                            code="nested-containment",
                            actual={"min": node["bbmin"], "max": node["bbmax"]},
                            target={"inside": nested_in, "min": container["bbmin"], "max": container["bbmax"]},
                        )
                    )

    geometry_found = {k: n for k, n in found.items() if any(
        kind_is_geometry(c) and str(c["name"]).lower() == k for c in comps
    )}
    geometry_items = sorted(geometry_found.items())
    component_by_name = {
        str(comp.get("name") or "").lower(): comp
        for comp in comps
        if isinstance(comp, dict) and comp.get("name")
    }
    for first_idx, (first_name, first_node) in enumerate(geometry_items):
        for second_name, second_node in geometry_items[first_idx + 1:]:
            first_spec = component_by_name[first_name]
            second_spec = component_by_name[second_name]
            overlap = _intersection_ratio(first_node, second_node)
            pair_tol = max(
                component_tolerance(first_node),
                component_tolerance(second_node),
            )
            same_center = all(
                abs(a - b) <= pair_tol
                for a, b in zip(_center(first_node), _center(second_node))
            )
            same_dims = all(
                _rel_ok(a, b, tol_pct)
                for a, b in zip(_dims(first_node), _dims(second_node))
            )
            if same_center and same_dims:
                viols.append(
                    _violation(
                        "overlap",
                        f"duplicates the bounds of {first_node['name']}; distinct components need distinct forms",
                        second_node["name"],
                        code="duplicate-bounds",
                    )
                )
            elif (
                first_name in valid_nested
                and str(first_spec.get("nested_in") or "").lower() == second_name
            ) or (
                second_name in valid_nested
                and str(second_spec.get("nested_in") or "").lower() == first_name
            ):
                continue
            elif overlap > 0.9:
                viols.append(
                    _violation(
                        "overlap",
                        f"overlaps {overlap:.0%} of the smaller bbox with {first_node['name']}; "
                        "separate the masses or declare the exact nested_in relationship",
                        second_node["name"],
                        code="overpenetration",
                        actual=round(overlap, 3),
                        target="<=0.9 or explicit nested_in",
                    )
                )
    if len(geometry_found) >= 2:
        for comp in comps:
            cname = str(comp["name"])
            node = geometry_found.get(cname.lower())
            if (
                node is None
                or comp.get("floating")
                or comp.get("touches")
                or cname.lower() in valid_nested
            ):
                continue
            if not any(
                _boxes_touch(node, other_node, pair_gap(node, other_node))
                for other_name, other_node in geometry_found.items()
                if other_name != cname.lower()
            ):
                viols.append(_violation("relation", "floating: touches no other component (set floating:true if intended)", cname))

    if idx == 0:
        for comp in comps:
            if not kind_is_geometry(comp):
                continue
            cname = str(comp["name"])
            node = found.get(cname.lower())
            if node is None:
                continue
            base_cls = str(node.get("baseclass") or node["class"]).lower()
            mod_enabled = list(node.get("modenabled", []))
            shaping_mods = [
                value
                for index, value in enumerate(node.get("modcls", []))
                if str(value).lower() not in NONSHAPING_MOD_CLASSES
                and (index >= len(mod_enabled) or bool(mod_enabled[index]))
            ]
            if base_cls not in PRIMITIVE_CLASSES or node.get("boolops") or shaping_mods:
                viols.append(
                    _violation(
                        "blockout",
                        "blockout must stay a raw primitive seed; add form work after this pass",
                        cname,
                        code="premature-form",
                        actual={"base": base_cls, "modifiers": shaping_mods, "booleans": node.get("boolops", [])},
                        target="raw primitive",
                    )
                )

    if idx >= 1:
        form_baseline = state.get("form_baseline") or {}
        for cname, node in geometry_found.items():
            node_dims = _dims(node)
            if min(node_dims) < 1e-4 * max(node_dims):
                viols.append(_violation("degenerate", "near-zero thickness (collapsed geometry?)", node["name"]))
            if any(abs(s - 1.0) > 0.001 for s in node["scale"]):
                viols.append(
                    _violation(
                        "degenerate",
                        f"baked node scale {[round(s, 3) for s in node['scale']]} — model at real size, reset xform",
                        node["name"],
                    )
                )
        for comp in comps:
            cname = str(comp["name"])
            node = found.get(cname.lower())
            if node is None or not kind_is_geometry(comp):
                continue
            how = _shaping_of(comp, node, form_baseline.get(cname.lower()))
            if cname in metrics["components"]:
                metrics["components"][cname]["shaped"] = how or "raw-primitive"
            if not how:
                viols.append(
                    _violation(
                        "shaping",
                        f"still a raw {node.get('baseclass') or node['class']} — cut or shape it (boolean_operation, "
                        "draw_spline profile + Extrude/Lathe/Sweep, edit_vertices conform, "
                        "deform modifiers), or declare primitive:true in the spec if this "
                        "part truly is a bare primitive",
                        cname,
                    )
                )

    if idx >= 2:
        assigned = {n["mat"].lower() for n in census["node_list"] if n["mat"]}
        for comp in comps:
            cname = str(comp["name"])
            node = found.get(cname.lower())
            ref = str(comp.get("material") or "")
            if node is None or not ref:
                continue
            if node["mat"].lower() != ref.lower():
                got = node["mat"] or "none"
                viols.append(_violation("material", f"has material '{got}', spec says '{ref}'", cname))
        for mat in ledger["materials"]:
            mname = str(mat.get("name") or "")
            if mname.lower() not in assigned:
                viols.append(_violation("material", f"spec material '{mname}' not assigned to any node under root"))
                continue
            declared = str(mat.get("class") or "")
            material_nodes = [
                node
                for node in census["node_list"]
                if node["mat"].lower() == mname.lower()
            ]
            wrong_classes = [
                {"node": node["name"], "class": node["matclass"]}
                for node in material_nodes
                if declared and _normalize_class(node["matclass"]) != _normalize_class(declared)
            ]
            if wrong_classes:
                viols.append(
                    _violation(
                        "material",
                        f"'{mname}' has assigned instances outside spec class '{declared}'",
                        code="material-class",
                        actual=wrong_classes,
                        target=declared,
                    )
                )
            for key, want in (mat.get("params") or {}).items():
                for material_node in material_nodes:
                    got = census["mparams"].get(
                        (material_node["name"].lower(), mname.lower(), str(key).lower())
                    )
                    if got is None or got in {"__MISSING__", "__ERR__"}:
                        viols.append(
                            _violation(
                                "material",
                                f"'{mname}.{key}' could not be read from assigned node {material_node['name']}",
                                material_node["name"],
                            )
                        )
                    elif not _compare_param(want, got):
                        viols.append(
                            _violation(
                                "material",
                                f"'{mname}.{key}' is {got}, spec {want!r}",
                                material_node["name"],
                            )
                        )

    allowed_detail_handles: set[str] = set()
    if idx >= 3:
        detail_metrics: dict[str, Any] = {}
        for det in ledger["details"]:
            if not isinstance(det, dict) or not det.get("id"):
                continue
            did = str(det["id"])
            on = str(det.get("on") or "").lower()
            via = str(det.get("via") or "").lower()
            node = found.get(on)
            matches: list[str] = []
            if node is not None and via in {"modifier", "editpoly", "boolean"}:
                mod_names = list(node.get("mods", []))
                mod_classes = list(node.get("modcls", []))
                mod_enabled = list(node.get("modenabled", []))
                mod_pairs = [
                    (
                        name,
                        mod_classes[index] if index < len(mod_classes) else "",
                        mod_enabled[index] if index < len(mod_enabled) else True,
                    )
                    for index, name in enumerate(mod_names)
                ]
                if via == "modifier":
                    matches = [name for name, _cls, enabled in mod_pairs if enabled and _anchor_matches(name, did)]
                elif via == "editpoly":
                    matches = [
                        name
                        for name, cls, enabled in mod_pairs
                        if enabled and _anchor_matches(name, did)
                        and "edit" in str(cls).lower() and "poly" in str(cls).lower()
                    ]
                else:
                    operand_matches = [
                        value
                        for value in node.get("boolops", [])
                        if _anchor_matches(value, did)
                    ]
                    matches = operand_matches or [
                        name
                        for name, cls, enabled in mod_pairs
                        if enabled and _anchor_matches(name, did)
                        and "boolean" in str(cls).lower()
                    ]
            elif node is not None and via in {"map", "projection"}:
                matches = [
                    item["name"]
                    for item in census["maps"].get(node["name"].lower(), [])
                    if _anchor_matches(item["name"], did)
                    and (via != "projection" or "camera" in item["class"].lower())
                ]
            elif via == "geometry":
                route_nodes = [
                    item
                    for item in census["node_list"]
                    if _anchor_matches(item["name"], did)
                    and not item.get("hidden")
                    and item.get("renderable", True)
                    and (
                        str(item["super"]).lower() == "geometryclass"
                        or (str(item["super"]).lower() == "shape" and item["tris"] > 0)
                    )
                    and _belongs_to_component(item, str(det.get("on") or ""), by_name)
                ]
                matches = [item["name"] for item in route_nodes]
                allowed_detail_handles.update(
                    str(item["handle"]) for item in route_nodes if item.get("handle")
                )
            elif via == "spline":
                route_nodes = [
                    item
                    for item in census["node_list"]
                    if str(item["super"]).lower() == "shape"
                    and _anchor_matches(item["name"], did)
                    and not item.get("hidden")
                    and _belongs_to_component(item, str(det.get("on") or ""), by_name)
                ]
                matches = [item["name"] for item in route_nodes]
                allowed_detail_handles.update(
                    str(item["handle"]) for item in route_nodes if item.get("handle")
                )

            expected_count = int(det.get("count") or 1)
            support_entries: list[dict[str, Any]] = []
            if via == "projection":
                support_entries = [
                    item
                    for item in census["node_list"]
                    if _anchor_matches(item["name"], did)
                    and "camera" in (
                        str(item.get("super") or "") + str(item.get("class") or "")
                    ).lower()
                    and _belongs_to_component(item, str(det.get("on") or ""), by_name)
                ]
            elif via == "boolean":
                support_entries = [
                    item
                    for item in census["node_list"]
                    if _anchor_matches(item["name"], did)
                    and str(item.get("super") or "").lower() == "geometryclass"
                    and _belongs_to_component(item, str(det.get("on") or ""), by_name)
                ]
            support_nodes = [item["name"] for item in support_entries]
            visible_support = [item["name"] for item in support_entries if not item.get("hidden")]
            if visible_support:
                viols.append(
                    _violation(
                        "detail",
                        f"{did}: support nodes must be hidden during proof capture: {visible_support[:8]}",
                        str(det.get("on") or ""),
                        code="visible-support",
                    )
                )
            if len(support_nodes) > expected_count:
                viols.append(
                    _violation(
                        "detail",
                        f"{did}: {len(support_nodes)} owned support nodes exceed exact count {expected_count}",
                        str(det.get("on") or ""),
                        code="detail-support-count",
                        actual=len(support_nodes),
                        target=expected_count,
                    )
                )
            else:
                allowed_detail_handles.update(
                    str(item["handle"]) for item in support_entries if item.get("handle")
                )
            detail_metrics[did] = {"via": via, "found": len(matches), "expected": expected_count}
            if len(matches) != expected_count:
                viols.append(
                    _violation(
                        "detail",
                        f"{did}: found {len(matches)}/{expected_count} {via} anchor(s) on {det.get('on')}",
                        str(det.get("on") or ""),
                        code=f"detail-{via}",
                        actual=len(matches),
                        target=expected_count,
                    )
                )
        metrics["details"] = detail_metrics

    total_tris = sum(n["tris"] for n in census["node_list"])
    metrics["total_tris"] = total_tris
    comp_names_lower = {str(c["name"]).lower() for c in comps}
    unspecced = [
        n["name"]
        for n in census["node_list"]
        if n["name"].lower() not in comp_names_lower
        and str(n.get("handle") or "") not in allowed_detail_handles
    ]
    litter: list[str] = []
    baseline_handles = ledger.get("baseline_root_handles")
    if isinstance(baseline_handles, list):
        base_set = {str(value) for value in baseline_handles}
        litter = [
            f"{s['name']} ({s['class']})"
            for s in census["scene_roots"]
            if str(s.get("handle") or "") not in base_set
        ]
    else:
        baseline_names = ledger.get("baseline_roots")
        if isinstance(baseline_names, list):  # v1 compatibility during forced migration
            base_names = {str(value).lower() for value in baseline_names}
            litter = [
                f"{item['name']} ({item['class']})"
                for item in census["scene_roots"]
                if item["name"].lower() not in base_names
            ]
    if idx >= 4:
        budget = int(ledger["budget"].get("tris") or 0)
        if budget and total_tris > budget:
            viols.append(_violation("budget", f"{total_tris} tris > budget {budget}"))
        floor_tris = int(ledger["budget"].get("min_tris") or 0)
        if floor_tris and total_tris < floor_tris:
            viols.append(
                _violation("budget", f"{total_tris} tris < min_tris {floor_tris} — underbuilt vs declared floor")
            )
        for name in unspecced:
            viols.append(_violation("hygiene", "node under root matches no component or detail id", name))
        off_layer = sorted({n["name"] for n in census["node_list"] if n["layer"] != "_builder"})
        if off_layer:
            viols.append(_violation("hygiene", f"nodes not on _builder layer: {off_layer[:8]}"))
        if census["root"].get("hidden"):
            viols.append(_violation("hygiene", "builder root is hidden"))
        if census["root"].get("layer") != "_builder":
            viols.append(_violation("hygiene", "builder root is not on the _builder layer"))
        for item in litter:
            viols.append(
                _violation("hygiene", f"session litter at scene root: {item} — parent under the root or delete")
            )
    else:
        if unspecced:
            warnings.append(f"unspecced nodes under root (hard-fail at finish): {unspecced[:8]}")
        if litter:
            warnings.append(f"new scene-root nodes since start (hard-fail at finish): {litter[:8]}")

    return viols, warnings, metrics


# ---------------------------------------------------------------------------
# Capture (deterministic gates first — this runs only after they pass)


def _capture_grid(root_name: str, views: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "max_width": 1600,
        "max_height": 0,
        "frame_root": root_name,
    }
    if views:
        payload["views"] = list(views)
    file_path = ""
    try:
        response = client.send_command(json.dumps(payload), cmd_type="native:capture_multi_view")
        data = json.loads(str(response.get("result", "") or "{}"))
        file_path = str(data.get("file") or "").replace("/", os.sep)
        if not file_path:
            return {"error": "multi-view capture returned no file"}
        actual_views = [str(value).lower() for value in (data.get("views") or [])]
        if actual_views != views:
            _discard_temp_capture(file_path)
            return {
                "error": f"multi-view capture returned views {actual_views}, expected {views}"
            }
        if data.get("framed_root") != root_name:
            _discard_temp_capture(file_path)
            return {"error": "multi-view capture did not confirm the builder root frame"}
        return {
            "type": "image_file",
            "file": file_path,
            "views": actual_views,
            "size_bytes": int(data.get("size_bytes") or 0),
        }
    except Exception as exc:  # capture failure must not void the gate result
        _discard_temp_capture(file_path)
        return {"error": f"capture failed: {exc}"}


def _select_review_views(pass_name: str, requested: StrList | None) -> list[str]:
    if not requested:
        return list(PASS_VIEWS[pass_name])
    normalized = [str(value).strip().lower() for value in requested]
    if len(normalized) != 4 or len(set(normalized)) != 4:
        raise ValueError("review capture requires exactly four distinct views")
    invalid = sorted(set(normalized) - ALLOWED_VIEWS)
    if invalid:
        raise ValueError(f"unknown review view(s): {invalid}")
    if "front" not in normalized or "top" not in normalized:
        raise ValueError("custom review views must include front and top")
    if not ({"left", "right"} & set(normalized)):
        raise ValueError("custom review views must include left or right")
    return normalized


def _load_session(name: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Read the ledger cheaply, then make exactly one scene census."""
    root = _root_name(name)
    ledger = _read_ledger(root)
    pass_name = str(ledger["state"].get("pass") or "spec")
    pass_index = PASSES.index(pass_name) if pass_name in PASSES else -1
    probe = _census(
        root,
        ledger["materials"] if pass_index >= PASSES.index("material") else [],
        str(ledger.get("units") or "cm"),
        include_material_params=pass_index >= PASSES.index("material"),
        include_maps=pass_index >= PASSES.index("detail"),
    )
    return root, ledger, probe


def _review_targets(ledger: dict[str, Any], pass_name: str) -> Any:
    if pass_name == "blockout":
        return ["silhouette", "proportions", "component placement"]
    if pass_name == "form":
        return [
            str(comp["name"])
            for comp in ledger["components"]
            if isinstance(comp, dict) and kind_is_geometry(comp) and not comp.get("primitive")
        ]
    if pass_name == "material":
        return [str(mat["name"]) for mat in ledger["materials"] if isinstance(mat, dict) and mat.get("name")]
    if pass_name == "detail":
        grouped: dict[str, list[str]] = {}
        for detail in ledger["details"]:
            if not isinstance(detail, dict) or not detail.get("id"):
                continue
            grouped.setdefault(str(detail.get("on") or "unassigned"), []).append(str(detail["id"]))
        return [{"component": owner, "details": ids} for owner, ids in grouped.items()]
    return ["overall silhouette", "all identity details", "material read", "scene hygiene"]


# ---------------------------------------------------------------------------
# Parked builder implementation. Keep these callables undecorated so importing
# this module cannot add their large schemas to any MCP tool registry.


def builder_session(
    action: Literal["start", "spec", "status", "abandon"],
    name: str,
    object_desc: str = "",
    reference: str = "",
    units: Literal["generic", "mm", "cm", "m", "in", "ft"] = "cm",
    complexity: Literal["simple", "moderate", "complex"] = "moderate",
    spec: DictValue | None = None,
    delete_nodes: bool = False,
    verbose: bool = False,
    include_history: bool = False,
    include_spec: bool = False,
) -> Any:
    """Start, patch, inspect, or abandon a Blueprint-Build-Prove session.

    The compact guide is resource://3dsmax-mcp/builder-guide. Spec updates can
    replace whole sections or upsert keyed items via spec.patch. Builder mode
    is external-MCP-only; standalone in-Max chat does not run Python gates.
    """
    action = action.strip().lower()
    if not _valid_item_name(name.strip(), limit=MAX_SESSION_NAME):
        return {
            "status": "error",
            "error": f"name must be 1-{MAX_SESSION_NAME} letters/digits/space/_/- characters",
        }
    root = _root_name(name)
    safe = safe_string(root)

    if action == "start":
        if len(object_desc) > 500:
            return {"status": "error", "error": "object_desc is capped at 500 characters"}
        if len(reference) > 2048:
            return {"status": "error", "error": "reference path is capped at 2048 characters"}
        script = f"""(
local lay = LayerManager.getLayerFromName "_builder"
if lay == undefined do lay = LayerManager.newLayerFromName "_builder"
local matches = for o in objects where (stricmp o.name "{safe}") == 0 collect o
if matches.count > 1 then "__ERROR__|Multiple nodes are named {safe}" else (
    local root = if matches.count == 1 then matches[1] else undefined
    local created = "resumed"
    if root != undefined and (getAppData root {BUILDER_APPDATA_ID}) == undefined then (
        "__ERROR__|A non-builder node is already named {safe}"
    ) else (
        if root == undefined do (
            root = Dummy name:"{safe}" pos:[0,0,0]
            created = "created"
        )
        lay.addNode root
        local rootHandles = ""
        for o in objects where o.parent == undefined and o != root do (
            local oh = try ((getHandleByAnim o) as string) catch ("")
            rootHandles += oh + "|"
        )
        local ad = getAppData root {BUILDER_APPDATA_ID}
        if ad == undefined do ad = ""
        created + "\\n" + rootHandles + "\\n" + ad
    )
)
)"""
        raw = str(client.send_command(script).get("result", ""))
        if raw.startswith("__ERROR__|"):
            return {"status": "error", "error": raw.split("|", 1)[1]}
        created, _, rest = raw.partition("\n")
        roots_line, _, existing = rest.partition("\n")
        baseline_handles = sorted({value for value in roots_line.split("|") if value.strip()})
        ledger = _parse_ledger(existing)
        if ledger is not None:
            current = ledger["state"]["pass"]
            if current == "spec":
                next_action = {"do": "author-spec", "tool": "builder_session", "action": "spec"}
            elif current == "complete":
                next_action = {"do": "present-final", "review": ledger["state"].get("final_review", {})}
            else:
                next_action = {"do": "work-current-pass", "pass": current, "then": "builder_gate.check"}
            result = {
                "root": root,
                "resumed": True,
                "state": _public_state(ledger, include_history=include_history),
                "next": next_action,
            }
            if include_spec:
                result["spec"] = _public_spec(ledger)
            return result
        if existing.strip():
            return {
                "status": "error",
                "error": f"{root} contains an invalid or newer builder ledger; refusing to overwrite it",
            }
        ledger = {
            "kind": "builder",
            "v": LEDGER_VERSION,
            "object": object_desc,
            "reference": reference,
            "units": units,
            "complexity": complexity.strip().lower() or "moderate",
            "tolerance_pct": DEFAULT_TOLERANCE_PCT,
            "baseline_root_handles": baseline_handles,
            "components": [],
            "materials": [],
            "details": [],
            "budget": {},
            "review": {"threshold": DEFAULT_VISUAL_THRESHOLD},
            "assumptions": [],
            "state": _empty_state(),
        }
        _history_add(ledger, {"event": "start", "created": created})
        _write_ledger(root, ledger)
        return {
            "root": root,
            "resumed": False,
            "state": _public_state(ledger),
            "next": {
                "do": "author-spatial-blueprint",
                "tool": "builder_session",
                "action": "spec",
                "guide": "resource://3dsmax-mcp/builder-guide",
            },
        }

    if action == "spec":
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except (ValueError, TypeError):
                return {"valid": False, "violations": [_violation("spec", "spec is not valid JSON")]}
        if not isinstance(spec, dict):
            return {"valid": False, "violations": [_violation("spec", "spec must be a dict")]}
        try:
            spec_size = len(
                json.dumps(
                    spec,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            )
        except (TypeError, ValueError):
            return {
                "valid": False,
                "violations": [_violation("spec", "spec contains a non-JSON value")],
            }
        if spec_size > MAX_SPEC_BYTES:
            return {
                "valid": False,
                "violations": [_violation("spec", "spec update exceeds the 100 KB safety cap")],
            }
        ledger = _read_ledger(root)
        state = ledger["state"]
        if state["pass"] != "spec" and not state.get("spec_unlocked"):
            return {
                "status": "error",
                "error": "spec is locked; first record verdict=refine-spec with the current check_id or review_id",
            }
        candidate, patch_violations, patch_stats = _apply_spec_update(ledger, spec)
        candidate_size = len(
            json.dumps(_public_spec(candidate), separators=(",", ":"), ensure_ascii=False)
        )
        size_violations = (
            [_violation("spec", "resulting blueprint exceeds the 100 KB safety cap")]
            if candidate_size > MAX_SPEC_BYTES else []
        )
        violations = patch_violations + size_violations + _validate_spec(candidate)
        if violations:
            return {
                "valid": False,
                "violations": violations,
                "next": {"do": "patch-spec", "sections": sorted({item["gate"] for item in violations})},
            }

        old = ledger
        ledger = candidate
        state = ledger["state"]
        changed_sections = {
            key
            for key in (
                "components", "materials", "details", "budget", "reference", "complexity",
                "tolerance_pct", "object", "units", "review", "assumptions",
            )
            if old.get(key) != ledger.get(key)
        }
        reopen = None
        if changed_sections & {"components", "reference", "complexity", "tolerance_pct", "units"}:
            reopen = "blockout"
        elif "materials" in changed_sections:
            reopen = "material"
        elif "details" in changed_sections:
            reopen = "detail"
        elif "budget" in changed_sections:
            reopen = "finish"

        if state["pass"] == "spec":
            state["pass"] = PASSES[0]
        elif reopen and state["pass"] in PASSES and PASSES.index(reopen) < PASSES.index(state["pass"]):
            state["pass"] = reopen
        state["completed"] = False
        state["blocked"] = False
        state["spec_unlocked"] = False
        state["spec_revision"] = int(state.get("spec_revision") or 0) + 1
        state["last_check"] = {}
        state["failure"] = {}
        state["migration_from"] = 0
        state["resume_token"] = ""
        state["final_review"] = {}
        if state["pass"] == "blockout":
            state["form_baseline"] = {}
        _history_add(
            ledger,
            {
                "event": "spec",
                "revision": state["spec_revision"],
                "changed": sorted(changed_sections),
                "components": len(ledger["components"]),
                "details": len(ledger["details"]),
            },
        )
        _write_ledger(root, ledger)
        return {
            "valid": True,
            "pass": state["pass"],
            "spec_revision": state["spec_revision"],
            "components": len(ledger["components"]),
            "details": len(ledger["details"]),
            "patched": {key: value for key, value in patch_stats.items() if value},
            "next": {"do": "build", "pass": state["pass"], "then": "builder_gate.check"},
        }

    if action == "status":
        ledger = _read_ledger(root)
        result: dict[str, Any] = {
            "root": root,
            "object": ledger.get("object"),
            "state": _public_state(ledger, include_history=include_history),
            "spec_summary": {
                "components": len(ledger["components"]),
                "materials": len(ledger["materials"]),
                "details": len(ledger["details"]),
                "budget": ledger["budget"],
            },
        }
        if verbose:
            census = _census(root, ledger["materials"], str(ledger.get("units") or "cm"))
            result["nodes_under_root"] = len(census["node_list"])
            result["spec_summary"]["component_names"] = [
                str(comp.get("name")) for comp in ledger["components"] if isinstance(comp, dict)
            ]
            result["spec_summary"]["detail_ids"] = [
                str(detail.get("id")) for detail in ledger["details"] if isinstance(detail, dict)
            ]
        if include_spec:
            result["spec"] = _public_spec(ledger)
        current = ledger["state"]["pass"]
        if current == "spec":
            result["next"] = {"do": "author-spec", "tool": "builder_session", "action": "spec"}
        elif current == "complete":
            result["next"] = {"do": "present-final"}
        else:
            result["next"] = {"do": "work-current-pass", "pass": current, "then": "builder_gate.check"}
        return result

    if action == "abandon":
        try:
            abandon_ledger = _read_ledger(root)
        except RuntimeError as exc:
            return {"status": "error", "error": str(exc)}
        capture_files = {
            str((abandon_ledger["state"].get("last_check") or {}).get("capture_file") or ""),
            str((abandon_ledger["state"].get("final_review") or {}).get("file") or ""),
        }
        if delete_nodes:
            script = f"""(
local matches = for o in objects where (stricmp o.name "{safe}") == 0 collect o
if matches.count == 0 then "__ERROR__|Root not found: {safe}" else if matches.count > 1 then (
    "__ERROR__|Multiple nodes are named {safe}"
) else (
    local root = matches[1]
    local doomed = #()
    local queue = #()
    for child in root.children do append queue child
    local qi = 1
    while qi <= queue.count do (
        local node = queue[qi]
        qi += 1
        for child in node.children do append queue child
        append doomed node
    )
    for index = doomed.count to 1 by -1 do (
        if isValidNode doomed[index] do try (delete doomed[index]) catch ()
    )
    if isValidNode root do try (delete root) catch ()
    local survivors = for node in doomed where isValidNode node collect node
    if isValidNode root or survivors.count > 0 then (
        "__ERROR__|Could not delete builder root or " + (survivors.count as string) + " descendant(s)"
    ) else "OK"
)
)"""
        else:
            script = f"""(
local matches = for o in objects where (stricmp o.name "{safe}") == 0 collect o
if matches.count == 0 then "__ERROR__|Root not found: {safe}" else if matches.count > 1 then (
    "__ERROR__|Multiple nodes are named {safe}"
) else (
    local root = matches[1]
    try (deleteAppData root {BUILDER_APPDATA_ID}) catch ()
    local archived = uniqueName ("ABANDONED_" + root.name + "_")
    root.name = archived
    "OK|" + archived
)
)"""
        raw = str(client.send_command(script).get("result", ""))
        if raw.startswith("__ERROR__|"):
            return {"status": "error", "error": raw.split("|", 1)[1]}
        if delete_nodes and raw.strip() != "OK":
            return {"status": "error", "error": f"abandon was not acknowledged: {raw!r}"}
        if not delete_nodes and not raw.startswith("OK|"):
            return {"status": "error", "error": f"abandon was not acknowledged: {raw!r}"}
        result = {"root": root, "abandoned": True, "nodes_deleted": bool(delete_nodes)}
        if not delete_nodes:
            result["archived_root"] = raw.partition("|")[2]
        for capture_file in capture_files:
            _discard_temp_capture(capture_file)
        return result

    return {"status": "error", "error": f"unknown action: {action}"}


def builder_gate(
    action: Literal["check", "record"],
    name: str,
    verdict: Literal["", "continue", "refine-spec", "refine-scene", "request-input"] = "",
    evidence: str = "",
    check_id: str = "",
    review_id: str = "",
    visual_score: float | None = None,
    reviewed: StrList | None = None,
    mismatches: StrList | None = None,
    changes: StrList | None = None,
    views: StrList | None = None,
    capture: bool = True,
    report: Literal["compact", "full"] = "compact",
    resume: bool = False,
    resume_token: str = "",
) -> Any:
    """Check hard gates, capture once, then record a snapshot-bound verdict.

    A clean check returns review_id. Any scene/spec/capture change makes that ID
    stale. Continue also requires visual_score, concise evidence, and all detail
    IDs in reviewed during the detail pass. Use report=full only for debugging.
    """
    action = action.strip().lower()
    verdict = verdict.strip().lower()
    if action not in {"check", "record"}:
        return {"status": "error", "error": "action must be check or record"}
    if not _valid_item_name(name.strip(), limit=MAX_SESSION_NAME):
        return {
            "status": "error",
            "error": f"name must be 1-{MAX_SESSION_NAME} letters/digits/space/_/- characters",
        }
    if len(resume_token) > 128:
        return {"status": "error", "error": "resume_token is invalid"}
    if action == "record":
        if verdict not in VERDICTS:
            return {"status": "error", "error": f"verdict must be one of {sorted(VERDICTS)}"}
        if len(evidence.strip()) < MIN_EVIDENCE_CHARS:
            return {"status": "error", "error": "evidence must briefly state what the capture showed"}
        if len(evidence) > 1200:
            return {"status": "error", "error": "evidence is capped at 1200 characters; keep it decision-focused"}
        if changes and (len(changes) > 24 or any(len(str(item)) > 240 for item in changes)):
            return {"status": "error", "error": "changes must be <=24 concise entries (240 chars each)"}
        if verdict in {"continue", "refine-scene"} and not review_id.strip():
            return {"status": "error", "error": "review_id from the latest clean captured check is required"}
        if verdict == "refine-spec" and not (check_id.strip() or review_id.strip()):
            return {"status": "error", "error": "refine-spec requires check_id (or review_id) from the latest check"}
        if visual_score is not None:
            try:
                numeric_score = float(visual_score)
            except (TypeError, ValueError):
                numeric_score = math.nan
            if not math.isfinite(numeric_score) or numeric_score < 0 or numeric_score > 1:
                return {"status": "error", "error": "visual_score must be finite and between 0 and 1"}
        if verdict == "continue" and visual_score is None:
            return {"status": "error", "error": "continue requires visual_score from the captured review"}
        if verdict == "continue" and mismatches:
            return {"status": "error", "error": "continue cannot carry mismatches; use refine-scene or refine-spec"}

    root, ledger, census = _load_session(name)
    state = ledger["state"]
    pass_name = state["pass"]

    if pass_name == "spec":
        return {
            "status": "error",
            "error": "no spec yet — author one with builder_session action=spec before gating",
        }
    if pass_name == "complete":
        return {"status": "error", "error": "session is complete; start a new one or abandon"}

    if action == "check":
        try:
            selected_views = _select_review_views(pass_name, views)
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
        if resume:
            if not state.get("blocked"):
                return {"status": "error", "error": "resume is valid only for a blocked session"}
            if not state.get("resume_token") or resume_token.strip() != state.get("resume_token"):
                return {
                    "status": "error",
                    "error": "resume_token from the recorded request-input verdict is required",
                }
            state["blocked"] = False
            state["failure"] = {}
            state["resume_token"] = ""
        elif state.get("blocked"):
            return {
                "status": "error",
                "error": "session is blocked for user input; record request-input and resume with its token",
            }
        viols, warnings, metrics = _evaluate(ledger, census)
        clean = not viols
        state["check_seq"] = int(state.get("check_seq") or 0) + 1
        scene_hash = _scene_fingerprint(ledger, census)
        total_attempts = int(state["attempts"].get(pass_name, 0))
        streak = 0
        defect_id = ""
        defect_severity = 0.0
        if not clean:
            total_attempts += 1
            state["attempts"][pass_name] = total_attempts
            defect_id = _failure_signature(viols)
            defect_severity = _failure_severity(viols)
            previous = state.get("failure") or {}
            same_pass = previous.get("pass") == pass_name
            id_trail = list(previous.get("id_trail") or [])[-5:] if same_pass else []
            severity_trail = (
                list(previous.get("severity_trail") or [])[-len(id_trail):]
                if id_trail else []
            )
            prior_index = max(
                (index for index, value in enumerate(id_trail) if value == defect_id),
                default=-1,
            )
            prior_severity = (
                float(severity_trail[prior_index])
                if prior_index >= 0 and prior_index < len(severity_trail)
                else math.inf
            )
            made_progress = prior_index >= 0 and defect_severity < prior_severity * 0.98
            cycle_span = len(id_trail) - prior_index + 1 if prior_index >= 0 else 0
            streak = (
                max(int(previous.get("streak") or 0) + 1, cycle_span)
                if prior_index >= 0 and not made_progress
                else 1
            )
            trail = list(previous.get("trail") or [])[-3:] if prior_index >= 0 else []
            trail.append(defect_severity)
            id_trail.append(defect_id)
            severity_trail.append(defect_severity)
            state["failure"] = {
                "pass": pass_name,
                "id": defect_id,
                "severity": defect_severity,
                "streak": streak,
                "trail": trail,
                "id_trail": id_trail[-6:],
                "severity_trail": severity_trail[-6:],
            }
        else:
            state["failure"] = {}

        capture_result: dict[str, Any] | None = None
        capture_hash = ""
        if clean and capture:
            capture_result = _capture_grid(root, selected_views)
            if capture_result.get("file"):
                capture_hash = _png_digest(str(capture_result["file"]))
                if not capture_hash:
                    _discard_temp_capture(str(capture_result["file"]))
                    capture_result = {"error": "multi-view capture did not produce a valid PNG"}
        review_ready = bool(clean and capture_result and capture_result.get("file") and capture_hash)
        current_check_id = (
            f"c{int(state.get('spec_revision') or 0)}-{state['check_seq']}-{scene_hash[:8]}"
        )
        current_review_id = (
            f"r{int(state.get('spec_revision') or 0)}-{state['check_seq']}-{scene_hash[:8]}"
            if review_ready
            else ""
        )
        previous_capture = str((state.get("last_check") or {}).get("capture_file") or "")
        state["last_check"] = {
            "pass": pass_name,
            "clean": clean,
            "scene": scene_hash,
            "spec_revision": int(state.get("spec_revision") or 0),
            "check_id": current_check_id,
            "review_id": current_review_id,
            "review_ready": review_ready,
            "capture_file": str((capture_result or {}).get("file") or ""),
            "capture_hash": capture_hash,
            "views": selected_views if review_ready else [],
            "t": int(time.time()),
        }
        if not clean and streak >= MAX_ATTEMPTS:
            state["blocked"] = True
        _write_ledger(root, ledger)
        _discard_temp_capture(previous_capture, keep=str((capture_result or {}).get("file") or ""))
        visible_violations = viols if report == "full" else viols[:20]
        result: dict[str, Any] = {
            "pass": pass_name,
            "clean": clean,
            "check_id": current_check_id,
            "violations": visible_violations,
            "metrics": _compact_metrics(metrics, viols, report),
            "attempts": {"total": total_attempts, "same_defect": streak},
            "next": _next_action(pass_name, viols),
        }
        if len(visible_violations) < len(viols):
            result["violation_summary"] = {
                "total": len(viols),
                "shown": len(visible_violations),
                "by_gate": {
                    gate: sum(item.get("gate") == gate for item in viols)
                    for gate in sorted({str(item.get("gate") or "") for item in viols})
                },
                "hint": "use report=full only if the first fixes do not expose enough context",
            }
        if defect_id:
            result["defect_id"] = defect_id
            result["defect_severity"] = defect_severity
        if warnings:
            result["warnings"] = warnings
        if clean:
            result["review_ready"] = review_ready
            result["review_targets"] = _review_targets(ledger, pass_name)
        if review_ready:
            result["review_id"] = current_review_id
            result["capture"] = capture_result
            reference = str(ledger.get("reference") or "")
            if reference:
                result["reference"] = reference
            result["threshold"] = _review_threshold(ledger)
        elif clean:
            result["next"] = {"do": "check-with-capture", "capture": True}
            result["capture_error"] = (capture_result or {}).get("error", "capture disabled")
        elif streak >= MAX_ATTEMPTS:
            result["next"] = {
                "do": "record",
                "verdict": "request-input",
                "reason": f"same defect set repeated {streak} times",
            }
        return result

    if action == "record":
        if state.get("blocked") and verdict != "request-input":
            return {"status": "error", "error": "three identical failures require verdict=request-input"}
        last = state.get("last_check") or {}
        using_hard_check = verdict == "refine-spec" and not review_id.strip()
        if using_hard_check:
            if last.get("check_id") != check_id.strip():
                return {"status": "error", "error": "check_id is stale; run check again"}
            if last.get("pass") != pass_name or last.get("clean") is not False:
                return {
                    "status": "error",
                    "error": "check_id can unlock the spec only from a dirty hard-gate result; "
                    "a clean result requires its captured review_id",
                }
            if int(last.get("spec_revision") or -1) != int(state.get("spec_revision") or 0):
                return {"status": "error", "error": "spec changed after check; run check again"}
            if _scene_fingerprint(ledger, census) != last.get("scene"):
                return {"status": "error", "error": "scene changed after check; run check again"}
        elif verdict != "request-input":
            if (
                last.get("pass") != pass_name
                or not last.get("clean")
                or not last.get("review_ready")
                or last.get("review_id") != review_id.strip()
            ):
                return {"status": "error", "error": "review_id is stale or no clean captured check exists"}
            if int(last.get("spec_revision") or -1) != int(state.get("spec_revision") or 0):
                return {"status": "error", "error": "spec changed after capture; run check again"}
            if _scene_fingerprint(ledger, census) != last.get("scene"):
                return {"status": "error", "error": "scene changed after capture; run check again"}
            capture_file = str(last.get("capture_file") or "")
            if _png_digest(capture_file) != last.get("capture_hash"):
                return {"status": "error", "error": "capture file changed or disappeared; run check again"}
            verification = _capture_grid(root, list(last.get("views") or []))
            verification_file = str(verification.get("file") or "")
            verification_hash = _png_digest(verification_file)
            _discard_temp_capture(verification_file, keep=capture_file)
            if not verification_hash:
                return {
                    "status": "error",
                    "error": f"freshness recapture failed: {verification.get('error', 'invalid PNG')}",
                }
            if verification_hash != last.get("capture_hash"):
                return {
                    "status": "error",
                    "error": "builder appearance changed after review; run check and review the new capture",
                }

        entry: dict[str, Any] = {
            "event": "verdict",
            "pass": pass_name,
            "verdict": verdict,
            "evidence": evidence.strip(),
        }
        if review_id:
            entry["review_id"] = review_id.strip()
        if check_id:
            entry["check_id"] = check_id.strip()
        if visual_score is not None:
            entry["visual_score"] = round(float(visual_score), 3)
        if reviewed:
            entry["reviewed"] = list(reviewed)
        if mismatches:
            entry["mismatches"] = list(mismatches)
        if changes:
            entry["changes"] = list(changes)

        if verdict == "continue":
            threshold = _review_threshold(ledger)
            if float(visual_score) < threshold:
                return {
                    "status": "error",
                    "error": f"visual_score {float(visual_score):.2f} is below threshold {threshold:.2f}; refine",
                }
            hedges = _evidence_hedges(evidence)
            if hedges:
                return {
                    "status": "error",
                    "error": f"evidence hedges ({', '.join(hedges)}) — those are refine words: "
                    "fix the work or record refine-scene",
                }
            if pass_name == "detail":
                ids = [str(d["id"]) for d in ledger["details"] if isinstance(d, dict) and d.get("id")]
                reviewed_set = {str(item).lower() for item in (reviewed or [])}
                missing = [item for item in ids if item.lower() not in reviewed_set]
                extra = sorted(reviewed_set - {item.lower() for item in ids})
                unnamed = [item for item in ids if not _whole_token(evidence, item)]
                if missing or extra or unnamed:
                    return {
                        "status": "error",
                        "error": "detail review must be exact and observed",
                        "details": {
                            "missing_reviewed": missing,
                            "unknown_reviewed": extra,
                            "missing_in_evidence": unnamed,
                        },
                    }
            viols, _, _ = _evaluate(ledger, census)
            if viols:
                return {
                    "status": "error",
                    "error": f"{len(viols)} violation(s) outstanding — pass not done",
                    "details": {"violations": viols},
                }
            if pass_name == "blockout":
                baselines: dict[str, Any] = {}
                for comp in ledger["components"]:
                    if not isinstance(comp, dict) or not comp.get("name") or not kind_is_geometry(comp):
                        continue
                    matches = census["nodes_by_name"].get(str(comp["name"]).lower(), [])
                    if len(matches) == 1:
                        baselines[str(comp["name"]).lower()] = _form_signature(matches[0])
                state["form_baseline"] = baselines
            idx = PASSES.index(pass_name)
            state["pass"] = "complete" if idx == len(PASSES) - 1 else PASSES[idx + 1]
            state["completed"] = state["pass"] == "complete"
            if state["completed"]:
                state["final_review"] = {
                    "review_id": review_id.strip(),
                    "file": str(last.get("capture_file") or ""),
                    "hash": str(last.get("capture_hash") or ""),
                    "views": list(last.get("views") or []),
                    "visual_score": round(float(visual_score), 3),
                }
            state["blocked"] = False
            state["spec_unlocked"] = False
            state["resume_token"] = ""
            state["last_check"] = {}
            state["failure"] = {}
        elif verdict == "refine-spec":
            state["spec_unlocked"] = True
            state["blocked"] = False
            state["resume_token"] = ""
            state["last_check"] = {}
            state["failure"] = {}
        elif verdict == "refine-scene":
            state["blocked"] = False
            state["resume_token"] = ""
            state["last_check"] = {}
            state["failure"] = {}
        elif verdict == "request-input":
            state["blocked"] = True
            state["resume_token"] = "q" + _stable_hash(
                {
                    "root": root,
                    "revision": state.get("spec_revision"),
                    "check": state.get("check_seq"),
                    "evidence": evidence.strip(),
                    "t": time.time_ns(),
                }
            )[:15]
            state["last_check"] = {}

        _history_add(ledger, entry)
        _write_ledger(root, ledger)
        if not state["completed"]:
            _discard_temp_capture(str(last.get("capture_file") or ""))
        result: dict[str, Any] = {
            "pass": state["pass"],
            "recorded": verdict,
            "completed": state["completed"],
        }
        if state["completed"]:
            result["next"] = {"do": "present-final", "capture": last.get("capture_file")}
        elif verdict == "continue":
            result["next"] = {"do": "build", "pass": state["pass"], "then": "builder_gate.check"}
        elif verdict == "refine-spec":
            result["next"] = {"do": "patch-spec", "tool": "builder_session", "action": "spec"}
        elif verdict == "refine-scene":
            result["next"] = {"do": "fix-scene", "pass": state["pass"], "then": "builder_gate.check"}
        else:
            result["resume_token"] = state["resume_token"]
            result["next"] = {
                "do": "ask-user",
                "resume_with": "builder_gate.check(resume=true, resume_token=<returned token>)",
            }
        return result

    return {"status": "error", "error": f"unknown action: {action}"}
