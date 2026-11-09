"""Read every texture source per material connection; filename hints are advisory."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...core.max_client import MaxClient
from ._material_detection import _DEFAULT_CHANNEL_PATTERNS, _detect_texture_channel

# Native slot roles -> the short vocabulary used across this project.
SLOT_ROLE_TO_VOCAB: dict[str, str] = {
    "base_color": "dif",
    "basecolor": "dif",
    "diffuse": "dif",
    "color": "dif",
    "roughness": "rough",
    "glossiness": "gloss",
    "gloss": "gloss",
    "metalness": "met",
    "metallic": "met",
    "normal": "nrm",
    "bump": "bump",
    "displacement": "disp",
    "height": "disp",
    "ao": "ao",
    "occlusion": "ao",
    "opacity": "opac",
    "alpha": "opac",
    "ior": "ior",
    "refraction": "trans",
    "translucency": "trans",
    "transmission": "trans",
    "emission": "emis",
    "emissive": "emis",
    "self_illumination": "emis",
    "sss": "sss",
    "scattering": "sss",
    "reflection": "refl",
    "specular": "refl",
}

# Detected file-name channels -> the same vocabulary.
FILE_CHANNEL_TO_VOCAB: dict[str, str] = {
    "diffuse": "dif",
    "ao": "ao",
    "orm": "orm",
    "roughness": "rough",
    "glossiness": "gloss",
    "metallic": "met",
    "normal": "nrm",
    "bump": "bump",
    "displacement": "disp",
    "opacity": "opac",
    "emission": "emis",
    "translucency": "trans",
    "ior": "ior",
    "specular": "refl",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")

# Map classes (lower-case, alphanumerics only) whose output is an image file.
_FILE_MAP_CLASSES = frozenset({
    "bitmap", "bitmaptexture", "bitmaptex", "autodeskbitmap", "vraybitmap", "vrayhdri",
    "coronabitmap", "aiimage",
})
# Shipped OSL shaders that load an image from their own filename input.
_OSL_IMAGE_SHADERS = frozenset({
    "hdrienviron", "hdrilights", "cameraprojector", "sphericalprojector", "objectprojector",
})
_SHADER_EXTS = (".osl", ".oso")


def _clean(value: Any) -> Any:
    return _CONTROL_CHARS.sub("", value) if isinstance(value, str) else value


def _map_class(node: dict) -> str:
    # classInternal is the non-localized class name; "class" is the UI label.
    return re.sub(r"[^a-z0-9]", "", str(node.get("classInternal") or node.get("class") or "").lower())


def _paths(node: dict) -> list[str]:
    return [str(f["path"]) for f in node.get("files") or [] if isinstance(f, dict) and f.get("path")]


def _file_path_unreadable(node: dict) -> bool:
    """A file-bearing map from which no image path was read (procedural maps never match)."""
    cls, paths = _map_class(node), _paths(node)
    if cls in _FILE_MAP_CLASSES:
        return not paths
    if cls == "oslmap":  # An OSL image loader reports its shader; the image must be there too.
        shaders = [p for p in paths if p.lower().endswith(_SHADER_EXTS)]
        stems = {re.split(r"[\\/]", p)[-1].lower().rsplit(".", 1)[0] for p in shaders}
        return (bool(shaders) and len(shaders) == len(paths)
                and any("bitmap" in stem or stem in _OSL_IMAGE_SHADERS for stem in stems))
    return False


def fetch_graphs(client: MaxClient, **arguments: Any) -> dict[str, Any]:
    response = client.send_command(
        json.dumps({"action": "roles", **arguments}, separators=(",", ":")),
        cmd_type="native:inspect_material_network",
    )
    payload = json.loads(response.get("result", "{}"))
    if not isinstance(payload, dict) or payload.get("graphVersion") != 2 or "graphs" not in payload:
        raise RuntimeError("material_roles requires a native bridge with material graph connections (graphVersion 2).")
    return payload


def _file_hint(path: str) -> tuple[str | None, str | None]:
    detected = _detect_texture_channel(Path(path), _DEFAULT_CHANNEL_PATTERNS)
    if detected is None:
        return None, None
    channel, _, alias = detected
    if len(alias.strip("_")) < 3:
        return None, channel
    return FILE_CHANNEL_TO_VOCAB.get(channel), channel


def _slot_role(edge: dict) -> str:
    # Parameter names and UI aliases are more precise than the native coarse role.
    labels = [edge.get("slot", ""), *edge.get("aliases", [])]
    patterns = (
        ("rough", "rough"), ("gloss", "gloss"), ("metal", "met"),
        ("normal", "nrm"), ("bump", "bump"), ("displac", "disp"),
        ("cutout", "opac"), ("opaci", "opac"), ("alpha", "opac"),
        ("ior", "ior"), ("emission", "emis"), ("emiss", "emis"),
        ("selfillum", "emis"), ("transluc", "trans"), ("transmis", "trans"),
        ("refract", "trans"), ("occlusion", "ao"), ("scatter", "sss"),
        ("basecolor", "dif"), ("diffuse", "dif"),
        ("reflect", "refl"), ("specular", "refl"),
    )
    for label in labels:
        key = re.sub(r"[^a-z0-9]", "", str(label).lower())
        for token, role in patterns:
            if token in key:
                return role
    raw = str(edge.get("role") or "map").lower()
    return SLOT_ROLE_TO_VOCAB.get(raw, "unknown" if raw == "map" else raw)


def roles_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("graphVersion") != 2:
        raise ValueError("Material graph lacks explicit connections; refusing an incomplete tree-based audit.")
    by_id = {n["id"]: n for n in payload.get("nodes", [])}
    children: dict[str, list[dict]] = {}
    for edge in payload.get("edges", []):
        children.setdefault(edge["parentId"], []).append(edge)
    entries: list[dict[str, Any]] = []
    issues = list(payload.get("issues") or [])
    warnings = list(payload.get("warnings") or [])
    truncated = dict(payload.get("truncated") or {})
    complete = payload.get("complete") is True and not any(truncated.values())
    visits = 0
    max_visits, max_entries = 10000, 2000

    def incomplete(code: str, node_id: str, message: str) -> None:
        nonlocal complete
        complete = False
        issue = {"code": code, "nodeId": node_id, "message": message}
        if issue not in issues:
            issues.append(issue)

    def enter(node_id: str, seen: frozenset[str]) -> dict | None:
        nonlocal visits
        visits += 1
        if visits > max_visits or len(entries) >= max_entries:
            incomplete("PATH_LIMIT", "", "Source traversal limit reached; narrow the material query.")
            return None
        if node_id in seen:
            incomplete("CIRCULAR_REF", node_id, "Circular source connection.")
            return None
        if node_id not in by_id:
            incomplete("MISSING_NODE", node_id, "A connection target was omitted from the graph.")
            return None
        return by_id[node_id]

    def sources(node_id: str, seen: frozenset[str], chain: list[dict], path: list[str]):
        node = enter(node_id, seen)
        if node is None:
            return
        chain = [*chain, node]
        files = [f for f in node.get("files", []) if f.get("path")]
        for file in files:
            yield file, chain, path
        outgoing = children.get(node_id, [])
        if not files and not outgoing:
            yield None, chain, path  # A procedural map can legitimately have no file.
        for edge in outgoing:
            if visits > max_visits or len(entries) >= max_entries:
                incomplete("PATH_LIMIT", "", "Source traversal limit reached; narrow the material query.")
                break
            yield from sources(edge["nodeId"], seen | {node_id}, chain, [*path, edge.get("slot", "")])

    def material(node_id: str, seen: frozenset[str], material_path: list[str]) -> None:
        node = enter(node_id, seen)
        if node is None:
            return
        for edge in children.get(node_id, []):
            if visits > max_visits or len(entries) >= max_entries:
                incomplete("PATH_LIMIT", "", "Source traversal limit reached; narrow the material query.")
                break
            target_id = edge["nodeId"]
            if edge.get("kind") == "material":
                material(target_id, seen | {node_id}, [*material_path, edge.get("slot", "")])
                continue
            role = _slot_role(edge)
            for file, chain, source_path in sources(target_id, seen | {node_id}, [], []):
                if len(entries) >= max_entries:
                    incomplete("PATH_LIMIT", "", "Source traversal limit reached; narrow the material query.")
                    break
                path = file.get("path") if file else None
                hint, channel = _file_hint(path) if path else (None, None)
                classes = [_clean(n.get("class", "?")) for n in chain]
                # A mask is a control input, not the color/roughness value itself.
                control = any("mask" in label.lower() or "mixamount" in label.lower().replace("_", "")
                              for label in source_path)
                expected = role
                normal_adapter = any(c.lower().replace(" ", "") in {"normalbump", "coronanormal", "vraynormalmap"}
                                     for c in classes[:-1])
                normal_input = any("normal" in label.lower() for label in source_path)
                if role == "bump" and normal_adapter and normal_input:
                    expected = "nrm"
                mismatch = bool(hint and expected != "unknown" and hint != expected and not control)
                entries.append({
                    "material": _clean(node.get("name")), "material_handle": node.get("handle"),
                    "material_path": material_path, "role": role,
                    "slot": _clean(edge.get("slot")), "slot_key": edge.get("slotKey"),
                    "slot_role_raw": edge.get("role"), "node_chain": classes,
                    "source_path": source_path, "source_usage": "control" if control else "value",
                    "file": path, "file_parameter": file.get("param") if file else None,
                    "exists": file.get("exists") if file else None,
                    "node_id": chain[-1]["id"], "node_name": _clean(chain[-1].get("name")),
                    "file_hint": hint or channel, "mismatch": mismatch,
                    "mismatch_reason": (
                        f"Filename suggests {hint}; connected to {role}. Check channel selection, map transforms and renderer modes."
                        if mismatch else None),
                })

    def image_below(node_id: str) -> bool:
        """A connected input (e.g. SetFile feeding an OSL loader) supplies an image path."""
        stack, seen = [e["nodeId"] for e in children.get(node_id, [])], {node_id}
        while stack and len(seen) < 500:
            child_id = stack.pop()
            if child_id in seen or child_id not in by_id:
                continue
            seen.add(child_id)
            if any(not p.lower().endswith(_SHADER_EXTS) for p in _paths(by_id[child_id])):
                return True
            stack.extend(e["nodeId"] for e in children.get(child_id, []))
        return False

    for node in payload.get("nodes", []):
        if node.get("kind") == "material" or not _file_path_unreadable(node):
            continue
        node_id, label = node.get("id", ""), f"{_clean(node.get('class'))} '{_clean(node.get('name'))}'"
        # Every file parameter is empty: nothing to audit. Autodesk Bitmap keeps its
        # source outside plain parameters, so empty parameters are no proof there.
        if node.get("fileUnset") is True and _map_class(node) != "autodeskbitmap":
            warning = {"code": "FILE_NOT_ASSIGNED", "nodeId": node_id, "message": f"{label} has no file assigned."}
        elif image_below(node_id):
            continue
        else:
            complete = False
            warning = {"code": "FILE_PATH_UNREADABLE", "nodeId": node_id,
                       "message": f"No image path could be read from {label} (unset or unreadable "
                                  "source); its texture is not audited."}
        if warning not in warnings:
            warnings.append(warning)

    root = payload.get("root") or {}
    material(root.get("id", "n0"), frozenset(), [])
    return {
        "material": _clean(payload.get("query")), "material_handle": root.get("handle"),
        "class": _clean(root.get("class")), "renderer": root.get("rendererProfile"),
        "owner": _clean(payload.get("owner")),
        "texture_folder": (payload.get("hints") or {}).get("textureFolderGuess"),
        "roles": entries, "complete": complete, "truncated": truncated,
        "missing_files": sorted({f["path"] for f in payload.get("fileManifest", [])
                                 if f.get("path") and f.get("exists") is False}),
        "mismatches": [e for e in entries if e["mismatch"]],
        "issues": issues, "warnings": warnings,
    }
