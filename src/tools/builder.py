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

import json
import os
import re
import time
from typing import Any

from ..coerce import StrList
from ..helpers.maxscript import safe_string
from ..server import client, mcp

BUILDER_APPDATA_ID = 1112294482  # "BLDR"
LEDGER_VERSION = 1
PASSES = ["blockout", "form", "material", "detail", "finish"]
VERDICTS = {"continue", "refine-spec", "refine-scene", "request-input"}
VIA = {"modifier", "editpoly", "map", "geometry", "projection", "boolean", "spline"}
COMPLEXITY_FLOORS = {"simple": (3, 0), "moderate": (6, 6), "complex": (10, 12)}
DEFAULT_TOLERANCE_PCT = 8.0
MAX_ATTEMPTS = 3
HISTORY_CAP = 40
MIN_EVIDENCE_CHARS = 10
_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-]+$")

# Self-absolving vocabulary: evidence that needs these words describes work that
# is not done. `record continue` refuses them — they are refine words.
HEDGE_WORDS = (
    "stylized", "stylization", "proxy", "proxies", "placeholder",
    "chunky", "good enough", "close enough", "for now", "acceptable for",
)


# ---------------------------------------------------------------------------
# Ledger


def _empty_state() -> dict[str, Any]:
    return {
        "pass": "spec",
        "completed": False,
        "blocked": False,
        "attempts": {},
        "last_check": {},
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
    state = data.get("state")
    if not isinstance(state, dict):
        data["state"] = _empty_state()
    else:
        base = _empty_state()
        base.update({k: state[k] for k in base if k in state})
        data["state"] = base
    for key in ("components", "materials", "details"):
        if not isinstance(data.get(key), list):
            data[key] = []
    if not isinstance(data.get("budget"), dict):
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
local root = getNodeByName "{safe}"
if root == undefined then (
    "__ERROR__|Root not found: {safe}"
) else (
    try (deleteAppData root {BUILDER_APPDATA_ID}) catch ()
    setAppData root {BUILDER_APPDATA_ID} {_ledger_literal(ledger)}
    "OK"
)
)"""
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])


def _history_add(ledger: dict[str, Any], entry: dict[str, Any]) -> None:
    entry["t"] = int(time.time())
    history = ledger["state"]["history"]
    history.append(entry)
    del history[:-HISTORY_CAP]


def _root_name(name: str) -> str:
    name = name.strip()
    return name if name.upper().startswith("BLD_") else f"BLD_{name}"


# ---------------------------------------------------------------------------
# Spec validation


def _violation(gate: str, message: str, component: str = "") -> dict[str, str]:
    out = {"gate": gate, "message": message}
    if component:
        out["component"] = component
    return out


def _as_float3(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def _validate_spec(ledger: dict[str, Any]) -> list[dict[str, str]]:
    """Deterministic pre-code gate: reject shallow specs before any geometry."""
    v: list[dict[str, str]] = []
    complexity = str(ledger.get("complexity") or "moderate").lower()
    if complexity not in COMPLEXITY_FLOORS:
        v.append(_violation("spec", f"complexity must be one of {sorted(COMPLEXITY_FLOORS)}"))
        complexity = "moderate"
    min_comps, min_details = COMPLEXITY_FLOORS[complexity]

    comps = ledger["components"]
    mats = ledger["materials"]
    details = ledger["details"]
    mat_names = set()
    comp_names = set()

    for mat in mats:
        if not isinstance(mat, dict) or not mat.get("name") or not mat.get("class"):
            v.append(_violation("spec", f"material needs name + class: {mat!r}"))
            continue
        mname = str(mat["name"])
        if not _NAME_RE.match(mname):
            v.append(_violation("spec", f"material name has illegal chars: {mname}"))
        if mname.lower() in mat_names:
            v.append(_violation("spec", f"duplicate material name: {mname}"))
        mat_names.add(mname.lower())
        params = mat.get("params")
        if params is not None and not isinstance(params, dict):
            v.append(_violation("spec", f"material {mname}: params must be a dict"))

    for comp in comps:
        if not isinstance(comp, dict) or not comp.get("name"):
            v.append(_violation("spec", f"component needs a name: {comp!r}"))
            continue
        cname = str(comp["name"])
        if not _NAME_RE.match(cname):
            v.append(_violation("spec", "name has illegal chars (letters/digits/space/_/- only)", cname))
        if cname.lower() in comp_names:
            v.append(_violation("spec", "duplicate component name", cname))
        comp_names.add(cname.lower())
        kind = str(comp.get("kind") or "geometry").lower()
        if kind not in {"geometry", "helper", "shape"}:
            v.append(_violation("spec", f"kind must be geometry|helper|shape, got {kind}", cname))
        if _as_float3(comp.get("dims")) is None or min(_as_float3(comp.get("dims")) or [0]) <= 0:
            v.append(_violation("spec", "dims must be 3 positive numbers (spec units)", cname))
        if kind == "geometry" and not comp.get("material"):
            v.append(_violation("spec", "geometry component needs a material ref", cname))

    for comp in comps:
        if not isinstance(comp, dict) or not comp.get("name"):
            continue
        cname = str(comp["name"])
        mat_ref = str(comp.get("material") or "")
        if mat_ref and mat_ref.lower() not in mat_names:
            v.append(_violation("spec", f"material ref not in materials: {mat_ref}", cname))
        ratios = comp.get("ratios")
        if ratios is not None:
            if not isinstance(ratios, dict):
                v.append(_violation("spec", "ratios must be a dict of component->number", cname))
            else:
                for other, ratio in ratios.items():
                    if str(other).lower() not in comp_names:
                        v.append(_violation("spec", f"ratio references unknown component: {other}", cname))
                    try:
                        if float(ratio) <= 0:
                            raise ValueError
                    except (TypeError, ValueError):
                        v.append(_violation("spec", f"ratio to {other} must be a positive number", cname))
        sym = comp.get("symmetry")
        if sym is not None and str(sym).lower() not in {"x", "y"}:
            v.append(_violation("spec", "symmetry must be 'x' or 'y'", cname))
        mirror = comp.get("mirror_of")
        if mirror is not None and str(mirror).lower() not in comp_names:
            v.append(_violation("spec", f"mirror_of references unknown component: {mirror}", cname))
        touches = comp.get("touches")
        if touches is not None:
            if not isinstance(touches, list):
                v.append(_violation("spec", "touches must be a list of component names", cname))
            else:
                for other in touches:
                    if str(other).lower() not in comp_names:
                        v.append(_violation("spec", f"touches references unknown component: {other}", cname))
        if complexity != "simple" and kind_is_geometry(comp):
            relational = any(
                comp.get(k) for k in ("ratios", "symmetry", "mirror_of", "ground", "touches")
            )
            if not relational:
                v.append(
                    _violation(
                        "spec",
                        "needs at least one relational constraint "
                        "(ratios/symmetry/mirror_of/ground/touches) at this complexity",
                        cname,
                    )
                )

    detail_ids = set()
    for det in details:
        if not isinstance(det, dict) or not det.get("id") or not det.get("on"):
            v.append(_violation("spec", f"detail needs id + on: {det!r}"))
            continue
        did = str(det["id"])
        if not _NAME_RE.match(did):
            v.append(_violation("spec", f"detail id has illegal chars: {did}"))
        if did.lower() in detail_ids:
            v.append(_violation("spec", f"duplicate detail id: {did}"))
        detail_ids.add(did.lower())
        if str(det["on"]).lower() not in comp_names:
            v.append(_violation("spec", f"detail {did}: 'on' references unknown component: {det['on']}"))
        via = str(det.get("via") or "").lower()
        if via not in VIA:
            v.append(_violation("spec", f"detail {did}: via must be one of {sorted(VIA)}"))

    geometry_comps = sum(1 for c in comps if isinstance(c, dict) and kind_is_geometry(c))
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

    budget = ledger["budget"]
    try:
        if int(budget.get("tris", 0)) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        v.append(_violation("spec", "budget.tris must be a positive integer"))
    if budget.get("min_tris") is not None:
        try:
            floor_tris = int(budget["min_tris"])
            if floor_tris <= 0 or floor_tris >= int(budget.get("tris") or 0):
                raise ValueError
        except (TypeError, ValueError):
            v.append(_violation("spec", "budget.min_tris must be a positive integer below budget.tris"))

    reference = str(ledger.get("reference") or "")
    if reference and not os.path.isfile(reference):
        v.append(_violation("spec", f"reference image not found on disk: {reference}"))

    try:
        if float(ledger.get("tolerance_pct") or DEFAULT_TOLERANCE_PCT) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        v.append(_violation("spec", "tolerance_pct must be a positive number"))
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


def _census(root_name: str, spec_materials: list[dict[str, Any]]) -> dict[str, Any]:
    safe = safe_string(root_name)
    probes = []
    for mat in spec_materials:
        mname = safe_string(str(mat.get("name") or ""))
        for key in (mat.get("params") or {}):
            k = safe_string(str(key))
            probes.append(
                f'try (local mm = undefined; for sm in sceneMaterials do (if mm == undefined and sm.name == "{mname}" do mm = sm); '
                f'if mm != undefined then (format "MPARAM|{mname}|{k}|%\\n" ((getProperty mm "{k}") as string) to:out) '
                f'else (format "MPARAM|{mname}|{k}|__MISSING__\\n" to:out)) '
                f'catch (format "MPARAM|{mname}|{k}|__ERR__\\n" to:out)'
            )
    probe_block = "\n".join(f"    {line}" for line in probes)
    script = f"""(
fn bldClean s = (
    local t = s as string
    t = substituteString t "|" "<pipe>"
    t = substituteString t "\\n" " "
    substituteString t "\\r" ""
)
local root = getNodeByName "{safe}"
if root == undefined then (
    "__ERROR__|Root not found: {safe}"
) else (
    local out = stringstream ""
    local ledgerRaw = ""
    try (local ad = getAppData root {BUILDER_APPDATA_ID}; if ad != undefined do ledgerRaw = ad) catch ()
    format "ROOT|%|%,%,%\\n" (bldClean root.name) root.pos.x root.pos.y root.pos.z to:out
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
        try (tris = (GetTriMeshFaceCount n)[1]) catch ()
        local mname = ""
        local mclass = ""
        if n.material != undefined do (
            mname = bldClean n.material.name
            mclass = (classof n.material) as string
        )
        local mods = ""
        local bops = ""
        for m in n.modifiers do (
            mods += (bldClean m.name) + ","
            if (classof m) == BooleanMod do (
                try (
                    local bcnt = m.GetNumOperands()
                    for oi = 2 to bcnt do (
                        local onm = ""
                        m.GetFlatOperandName oi &onm
                        bops += (bldClean onm) + ","
                    )
                ) catch ()
            )
        )
        local lname = ""
        try (lname = bldClean n.layer.name) catch ()
        format "NODE|%|%|%|%|%|%,%,%|%,%,%|%,%,%|%|%|%|%|%,%,%|%\\n" (bldClean n.name) ((classof n) as string) ((superclassof n) as string) (bldClean (if n.parent != undefined then n.parent.name else "")) lname n.pos.x n.pos.y n.pos.z bbmin.x bbmin.y bbmin.z bbmax.x bbmax.y bbmax.z (tris as string) mname mclass mods n.scale.x n.scale.y n.scale.z bops to:out
        if n.material != undefined do (
            try (
                for i = 1 to (getNumSubTexmaps n.material) do (
                    local t = getSubTexmap n.material i
                    if t != undefined do format "MAP|%|%|%\\n" (bldClean n.name) (bldClean t.name) ((classof t) as string) to:out
                )
            ) catch ()
        )
    )
{probe_block}
    for o in objects where o.parent == undefined and o != root do (
        format "SROOT|%|%\\n" (bldClean o.name) ((classof o) as string) to:out
    )
    format "LEDGER|%\\n" ledgerRaw to:out
    out as string
)
)"""
    raw = str(client.send_command(script).get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])

    census: dict[str, Any] = {
        "root": {"name": root_name, "pos": [0.0, 0.0, 0.0]},
        "node_list": [],
        "nodes_by_name": {},
        "maps": {},
        "mparams": {},
        "scene_roots": [],
        "ledger_raw": "",
    }
    for line in raw.splitlines():
        if not line.strip():
            continue
        kind, _, rest = line.partition("|")
        if kind == "ROOT":
            name, _, pos = rest.partition("|")
            census["root"] = {"name": name.replace("<pipe>", "|"), "pos": _parse_triple(pos)}
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
                "mods": [m for m in f[11].split(",") if m],
                "scale": _parse_triple(f[12]),
                "boolops": [b.replace("<pipe>", "|") for b in f[13].split(",") if b] if len(f) > 13 else [],
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
            f = rest.split("|", 2)
            if len(f) >= 3:
                census["mparams"][(f[0].lower(), f[1].lower())] = f[2]
        elif kind == "SROOT":
            f = rest.split("|")
            if len(f) >= 2:
                census["scene_roots"].append(
                    {"name": f[0].replace("<pipe>", "|"), "class": f[1]}
                )
        elif kind == "LEDGER":
            census["ledger_raw"] = rest
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


def _compare_param(spec_value: Any, measured: str) -> bool:
    measured = measured.strip()
    if isinstance(spec_value, (list, tuple)):
        nums = re.findall(r"-?\d+\.?\d*", measured)
        if len(nums) < len(spec_value):
            return False
        return all(abs(float(n) - float(s)) <= 5.0 for n, s in zip(nums, spec_value))
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
    tol_pct = float(ledger.get("tolerance_pct") or DEFAULT_TOLERANCE_PCT)
    comps = [c for c in ledger["components"] if isinstance(c, dict) and c.get("name")]
    root_pos = census["root"]["pos"]
    by_name = census["nodes_by_name"]

    viols: list[dict] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {"components": {}, "pass": pass_name}

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
            if kind_is_geometry(comp) and node["super"] != "GeometryClass" and not (
                node["super"] == "Shape" and node["tris"] > 0
            ):
                viols.append(
                    _violation(
                        "coverage",
                        f"node is {node['class']}, not geometry"
                        + (
                            " (spline with no mesh output — add Extrude/Lathe/Sweep or declare kind:shape)"
                            if node["super"] == "Shape"
                            else ""
                        ),
                        cname,
                    )
                )
            else:
                found[cname.lower()] = node

    assembly_dim = 1.0
    if found:
        lo = [min(n["bbmin"][i] for n in found.values()) for i in range(3)]
        hi = [max(n["bbmax"][i] for n in found.values()) for i in range(3)]
        assembly_dim = max(max(hi[i] - lo[i] for i in range(3)), 1e-6)
    tol_abs = max(0.25, 0.02 * assembly_dim)
    gap = max(tol_abs, 0.01 * assembly_dim)

    for comp in comps:
        cname = str(comp["name"])
        node = found.get(cname.lower())
        if node is None:
            continue
        dims = _dims(node)
        metrics["components"][cname] = {
            "dims": [round(d, 3) for d in dims],
            "tris": node["tris"],
            "center_off_root": [round(_center(node)[i] - root_pos[i], 3) for i in range(3)],
        }
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
                        )
                    )
                    break
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
            if abs(node["bbmin"][2] - root_pos[2]) > tol_abs:
                viols.append(
                    _violation(
                        "relation",
                        f"ground contact: bbox min z {node['bbmin'][2]:.2f} vs root z {root_pos[2]:.2f}",
                        cname,
                    )
                )
        sym = str(comp.get("symmetry") or "").lower()
        if sym in {"x", "y"}:
            ax = 0 if sym == "x" else 1
            off = _center(node)[ax] - root_pos[ax]
            if abs(off) > tol_abs:
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
                bad = abs(a[ax] + b[ax]) > tol_abs or any(
                    abs(a[i] - b[i]) > tol_abs for i in range(3) if i != ax
                )
                if not bad:
                    da, db = _dims(node), _dims(onode)
                    bad = any(not _rel_ok(m, t, tol_pct) for m, t in zip(da, db))
                if bad:
                    viols.append(_violation("relation", f"not a mirror of {mirror} across {'xyz'[ax]}", cname))
        for other in comp.get("touches") or []:
            onode = found.get(str(other).lower())
            if onode is not None and not _boxes_touch(node, onode, gap):
                viols.append(_violation("relation", f"declared touching {other} but bboxes are apart", cname))

    geometry_found = {k: n for k, n in found.items() if any(
        kind_is_geometry(c) and str(c["name"]).lower() == k for c in comps
    )}
    if len(geometry_found) >= 2:
        for comp in comps:
            cname = str(comp["name"])
            node = geometry_found.get(cname.lower())
            if node is None or comp.get("floating") or comp.get("touches"):
                continue
            if not any(
                _boxes_touch(node, o, gap) for k, o in geometry_found.items() if k != cname.lower()
            ):
                viols.append(_violation("relation", "floating: touches no other component (set floating:true if intended)", cname))

    if idx >= 1:
        for cname, node in found.items():
            if min(_dims(node)) < 1e-4 * assembly_dim:
                viols.append(_violation("degenerate", "near-zero thickness (collapsed geometry?)", node["name"]))
            if any(abs(s - 1.0) > 0.001 for s in node["scale"]):
                viols.append(
                    _violation(
                        "degenerate",
                        f"baked node scale {[round(s, 3) for s in node['scale']]} — model at real size, reset xform",
                        node["name"],
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
            classes = {
                n["matclass"].lower() for n in census["node_list"] if n["mat"].lower() == mname.lower()
            }
            if declared and not any(declared.lower().replace(" ", "_") in c or declared.lower() in c for c in classes):
                viols.append(
                    _violation("material", f"'{mname}' is {sorted(classes)}, spec class '{declared}'")
                )
            for key, want in (mat.get("params") or {}).items():
                got = census["mparams"].get((mname.lower(), str(key).lower()))
                if got is None or got in {"__MISSING__", "__ERR__"}:
                    viols.append(_violation("material", f"'{mname}': could not read param '{key}'"))
                elif not _compare_param(want, got):
                    viols.append(_violation("material", f"'{mname}.{key}' is {got}, spec {want!r}"))

    detail_ids = [str(d["id"]) for d in ledger["details"] if isinstance(d, dict) and d.get("id")]
    if idx >= 3:
        for det in ledger["details"]:
            if not isinstance(det, dict) or not det.get("id"):
                continue
            did = str(det["id"]).lower()
            on = str(det.get("on") or "").lower()
            via = str(det.get("via") or "").lower()
            node = found.get(on)
            anchors: list[str] = []
            if node is not None:
                anchors += node["mods"]
                anchors += node.get("boolops", [])
                anchors += [m["name"] for m in census["maps"].get(node["name"].lower(), [])]
            anchors += [n["name"] for n in census["node_list"]]
            if not any(did in a.lower() for a in anchors):
                viols.append(
                    _violation(
                        "detail",
                        f"no anchor named *{det['id']}* (node, modifier or Boolean operand "
                        f"on {det.get('on')}, or map)",
                    )
                )
            elif via == "projection" and node is not None:
                maps = census["maps"].get(node["name"].lower(), [])
                if not any("camera" in m["class"].lower() for m in maps):
                    viols.append(
                        _violation("detail", f"{det['id']}: via=projection but no Camera Map on {det.get('on')}")
                    )
            elif via == "boolean" and node is not None:
                own = node["mods"] + node.get("boolops", [])
                if not any(did in a.lower() for a in own):
                    viols.append(
                        _violation(
                            "detail",
                            f"{det['id']}: via=boolean but no Boolean operand or modifier "
                            f"named *{det['id']}* on {det.get('on')}",
                        )
                    )
            elif via == "spline":
                if not any(
                    did in n["name"].lower() and n["super"] == "Shape" for n in census["node_list"]
                ):
                    viols.append(
                        _violation(
                            "detail",
                            f"{det['id']}: via=spline but no spline shape named *{det['id']}* under root",
                        )
                    )

    total_tris = sum(n["tris"] for n in census["node_list"])
    metrics["total_tris"] = total_tris
    comp_names_lower = {str(c["name"]).lower() for c in comps}
    unspecced = [
        n["name"]
        for n in census["node_list"]
        if (n["super"] == "GeometryClass" or (n["super"] == "Shape" and n["tris"] > 0))
        and n["name"].lower() not in comp_names_lower
        and not any(did.lower() in n["name"].lower() for did in detail_ids)
    ]
    litter: list[str] = []
    baseline = ledger.get("baseline_roots")
    if isinstance(baseline, list):  # pre-baseline ledgers skip the litter gate
        base_set = {str(x).lower() for x in baseline}
        litter = [
            f"{s['name']} ({s['class']})"
            for s in census["scene_roots"]
            if s["name"].lower() not in base_set
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
            viols.append(_violation("hygiene", "geometry under root matches no component or detail id", name))
        off_layer = sorted({n["name"] for n in census["node_list"] if n["layer"] != "_builder"})
        if off_layer:
            viols.append(_violation("hygiene", f"nodes not on _builder layer: {off_layer[:8]}"))
        for item in litter:
            viols.append(
                _violation("hygiene", f"session litter at scene root: {item} — parent under the root or delete")
            )
    else:
        if unspecced:
            warnings.append(f"unspecced geometry under root (hard-fails at finish): {unspecced[:8]}")
        if litter:
            warnings.append(f"new scene-root nodes since start (hard-fail at finish): {litter[:8]}")

    return viols, warnings, metrics


# ---------------------------------------------------------------------------
# Capture (deterministic gates first — this runs only after they pass)


def _capture_grid(views: list[str] | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"max_width": 1600, "max_height": 0}
    if views:
        payload["views"] = list(views)
    try:
        response = client.send_command(json.dumps(payload), cmd_type="native:capture_multi_view")
        data = json.loads(str(response.get("result", "") or "{}"))
        file_path = data.get("file", "")
        if not file_path:
            return {"error": "multi-view capture returned no file"}
        return {"type": "image_file", "file": file_path.replace("/", os.sep), "views": data.get("views")}
    except Exception as exc:  # capture failure must not void the gate result
        return {"error": f"capture failed: {exc}"}


def _load_session(name: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Census + parsed ledger for a session root; raises if uninitialized."""
    root = _root_name(name)
    probe = _census(root, [])
    ledger = _parse_ledger(probe["ledger_raw"])
    if ledger is None:
        raise RuntimeError(f"{root} has no builder ledger — run builder_session action=start first")
    if ledger["materials"] and any(m.get("params") for m in ledger["materials"]):
        probe = _census(root, ledger["materials"])  # re-read with param probes
    return root, ledger, probe


# ---------------------------------------------------------------------------
# Tools


@mcp.tool()
def builder_session(
    action: str,
    name: str,
    object_desc: str = "",
    reference: str = "",
    units: str = "cm",
    complexity: str = "moderate",
    spec: Any = None,
    delete_nodes: bool = False,
) -> Any:
    """Manage a builder-mode session: spec-gated staged asset construction.

    Actions: start (create root assembly + ledger), spec (author/patch the
    sculpt spec — validated, shallow specs rejected), status, abandon.
    Read skills/3dsmax-mcp-dev/builder.md before using builder mode.

    Use when: constructing an asset from a reference image or description with
    pass gating and deterministic checks.
    Not when: one-off object creation (create_object) or scene edits.
    """
    action = action.strip().lower()
    root = _root_name(name)
    safe = safe_string(root)

    if action == "start":
        script = f"""(
local lay = LayerManager.getLayerFromName "_builder"
if lay == undefined do lay = LayerManager.newLayerFromName "_builder"
local root = getNodeByName "{safe}"
local created = "resumed"
if root == undefined do (
    root = Dummy name:"{safe}" pos:[0,0,0]
    created = "created"
)
lay.addNode root
local rootNames = ""
for o in objects where o.parent == undefined and o != root do (
    local cleanName = substituteString (substituteString (o.name as string) "|" "<pipe>") "\\n" " "
    rootNames += cleanName + "|"
)
local ad = getAppData root {BUILDER_APPDATA_ID}
if ad == undefined do ad = ""
created + "\\n" + rootNames + "\\n" + ad
)"""
        raw = str(client.send_command(script).get("result", ""))
        created, _, rest = raw.partition("\n")
        roots_line, _, existing = rest.partition("\n")
        baseline = sorted({r.lower() for r in roots_line.split("|") if r.strip()})
        ledger = _parse_ledger(existing)
        if ledger is not None:
            return {"root": root, "resumed": True, "state": ledger["state"]}
        ledger = {
            "kind": "builder",
            "v": LEDGER_VERSION,
            "object": object_desc,
            "reference": reference,
            "units": units,
            "complexity": complexity.strip().lower() or "moderate",
            "tolerance_pct": DEFAULT_TOLERANCE_PCT,
            "baseline_roots": baseline,
            "components": [],
            "materials": [],
            "details": [],
            "budget": {},
            "state": _empty_state(),
        }
        _history_add(ledger, {"event": "start", "created": created})
        _write_ledger(root, ledger)
        return {
            "root": root,
            "resumed": False,
            "state": ledger["state"],
            "hint": {
                "message": "Session at pass 'spec'. Study the reference detail-first "
                "(see builder.md), then author the spec via action=spec."
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
        _, ledger, _ = _load_session(name)
        for key in ("components", "materials", "details", "budget", "reference", "complexity", "tolerance_pct", "object"):
            if key in spec:
                ledger[key] = spec[key]
        violations = _validate_spec(ledger)
        if violations:
            return {"valid": False, "violations": violations}
        if ledger["state"]["pass"] == "spec":
            ledger["state"]["pass"] = PASSES[0]
        _history_add(ledger, {"event": "spec", "components": len(ledger["components"]), "details": len(ledger["details"])})
        _write_ledger(root, ledger)
        return {
            "valid": True,
            "pass": ledger["state"]["pass"],
            "components": len(ledger["components"]),
            "details": len(ledger["details"]),
        }

    if action == "status":
        _, ledger, census = _load_session(name)
        return {
            "root": root,
            "object": ledger.get("object"),
            "state": ledger["state"],
            "nodes_under_root": len(census["node_list"]),
            "spec_summary": {
                "components": [str(c.get("name")) for c in ledger["components"]],
                "details": [str(d.get("id")) for d in ledger["details"]],
                "budget": ledger["budget"],
            },
        }

    if action == "abandon":
        nodes_clause = ""
        if delete_nodes:
            nodes_clause = (
                "local doomed = #()\n"
                "    local queue = #()\n"
                "    for c in root.children do append queue c\n"
                "    local qi = 1\n"
                "    while qi <= queue.count do (\n"
                "        local n = queue[qi]\n"
                "        qi += 1\n"
                "        for c in n.children do append queue c\n"
                "        append doomed n\n"
                "    )\n"
                "    try (delete doomed) catch ()\n"
                "    try (delete root) catch ()\n    "
            )
        script = f"""(
local root = getNodeByName "{safe}"
if root == undefined then "OK" else (
    try (deleteAppData root {BUILDER_APPDATA_ID}) catch ()
    {nodes_clause}"OK"
)
)"""
        client.send_command(script)
        return {"root": root, "abandoned": True, "nodes_deleted": bool(delete_nodes)}

    return {"status": "error", "error": f"unknown action: {action} (start|spec|status|abandon)"}


@mcp.tool()
def builder_gate(
    action: str,
    name: str,
    verdict: str = "",
    evidence: str = "",
    changes: StrList | None = None,
    views: StrList | None = None,
    capture: bool = True,
) -> Any:
    """The only door between builder passes: deterministic census gates, then
    a multi-view capture for agent-vision review, then a single recorded verdict.

    Actions: check (measure scene vs spec for the current pass; capture fires
    only when the hard gates pass) and record (verdict: continue | refine-spec
    | refine-scene | request-input; 'continue' re-checks and refuses while
    violations remain).

    Use when: after building/refining each pass in a builder session.
    Not when: no builder session exists (builder_session action=start).
    """
    action = action.strip().lower()
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
        viols, warnings, metrics = _evaluate(ledger, census)
        clean = not viols
        attempts = state["attempts"].get(pass_name, 0)
        if not clean:
            attempts += 1
            state["attempts"][pass_name] = attempts
        state["last_check"] = {"pass": pass_name, "clean": clean, "t": int(time.time())}
        _write_ledger(root, ledger)
        result: dict[str, Any] = {
            "pass": pass_name,
            "clean": clean,
            "violations": viols,
            "metrics": metrics,
            "attempts": attempts,
        }
        if pass_name == "detail":
            result["details_to_review"] = [
                str(d["id"]) for d in ledger["details"] if isinstance(d, dict) and d.get("id")
            ]
        if warnings:
            result["warnings"] = warnings
        if clean and capture:
            result["capture"] = _capture_grid(list(views) if views else None)
            reference = str(ledger.get("reference") or "")
            if reference:
                result["reference"] = reference
            result["hint"] = {
                "message": "Hard gates pass. Read the capture and the reference, judge per the "
                "builder.md rubric, then builder_gate action=record with your verdict and evidence."
            }
        elif not clean and attempts >= MAX_ATTEMPTS:
            result["hint"] = {
                "message": f"{attempts} failed checks on '{pass_name}' — stop burning attempts; "
                "record verdict=request-input and ask the user."
            }
        return result

    if action == "record":
        verdict = verdict.strip().lower()
        if verdict not in VERDICTS:
            return {"status": "error", "error": f"verdict must be one of {sorted(VERDICTS)}"}
        if len(evidence.strip()) < MIN_EVIDENCE_CHARS:
            return {
                "status": "error",
                "error": "evidence required: what you compared and what you saw (or changed)",
            }
        entry: dict[str, Any] = {"event": "verdict", "pass": pass_name, "verdict": verdict, "evidence": evidence.strip()}
        if changes:
            entry["changes"] = list(changes)

        if verdict == "continue":
            lowered = evidence.lower()
            hedges = sorted({w for w in HEDGE_WORDS if w in lowered})
            if hedges:
                return {
                    "status": "error",
                    "error": f"evidence hedges ({', '.join(hedges)}) — those are refine words: "
                    "fix the work or record refine-scene",
                }
            if pass_name == "detail":
                ids = [str(d["id"]) for d in ledger["details"] if isinstance(d, dict) and d.get("id")]
                missing = [i for i in ids if i.lower() not in lowered]
                if missing:
                    return {
                        "status": "error",
                        "error": "detail-pass evidence must verdict every detail id "
                        f"(isolate-capture each component and judge its details); missing: {missing}",
                    }
            last = state.get("last_check") or {}
            if last.get("pass") != pass_name or not last.get("clean"):
                return {
                    "status": "error",
                    "error": "no clean check on this pass — run builder_gate action=check first",
                }
            viols, _, _ = _evaluate(ledger, census)
            if viols:
                return {
                    "status": "error",
                    "error": f"{len(viols)} violation(s) outstanding — pass not done",
                    "details": {"violations": viols},
                }
            idx = PASSES.index(pass_name)
            state["pass"] = "complete" if idx == len(PASSES) - 1 else PASSES[idx + 1]
            state["completed"] = state["pass"] == "complete"
            state["blocked"] = False
            state["last_check"] = {}
        elif verdict == "request-input":
            state["blocked"] = True

        _history_add(ledger, entry)
        _write_ledger(root, ledger)
        result = {"pass": state["pass"], "recorded": verdict, "completed": state["completed"]}
        if state["completed"]:
            result["hint"] = {"message": "Build complete. Present the final capture and the pass history to the user."}
        elif verdict == "continue":
            result["hint"] = {"message": f"Pass '{state['pass']}' unlocked — see builder.md for its work and gates."}
        return result

    return {"status": "error", "error": f"unknown action: {action} (check|record)"}
