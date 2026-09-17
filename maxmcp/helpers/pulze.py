"""Pulze Scene Manager reads and guarded writes.

Scene Manager keeps its setups in a scene custom attribute on the root node
(``SceneManager_Attributes``), not in its UI: ``SceneManager_Dat`` is a
base64-encoded JSON array, one object per setup. Reading it needs no Pulze
function call and no window, so setup inspection works headless and stays
deterministic.

Setup keys (schema version 7): version, id, color, render, scenename, camera,
resolution, sun, dome, environment, background, frames, elements, layers,
objects, variants, lights, output_Common, output_Channels, output_Raw, todo,
notes, xrefScene, scripts, snippet, renderPreset, renderValue, renderCustom,
renderManager, atmosphere, post, thumbnail, status.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import posixpath
from typing import Any

from ..max_client import MaxClient
from .maxscript import safe_string

CA_NAME = "SceneManager_Attributes"
DATA_PROP = "SceneManager_Dat"
_CHUNK = 3500


class PulzeUnavailable(RuntimeError):
    """Scene Manager data is not present in the open scene."""


def _find_ca_maxscript(body: str) -> str:
    """Wrap a body that can use `ca` — the Scene Manager custom attribute."""
    return f"""(
    local ca = undefined
    local total = custAttributes.count rootNode
    for i = 1 to total while ca == undefined do (
        local candidate = custAttributes.get rootNode i
        if (matchPattern (candidate as string) pattern:"*{CA_NAME}*") do ca = candidate
    )
    if ca == undefined then "__PULZE_ABSENT__" else ({body})
)"""


def _send(client: MaxClient, maxscript: str, timeout: float = 30.0) -> str:
    response = client.send_command(maxscript, timeout=timeout)
    return str(response.get("result", ""))


def read_blob(client: MaxClient) -> str:
    """Return the raw base64 setup blob, or raise if Scene Manager is absent."""
    blob = _send(client, _find_ca_maxscript(f"getProperty ca #{DATA_PROP}"))
    if blob == "__PULZE_ABSENT__":
        raise PulzeUnavailable(
            "No Scene Manager data in this scene. Open a scene saved with Pulze "
            "Scene Manager, or add at least one setup first."
        )
    return blob.strip()


def decode_setups(blob: str) -> list[dict[str, Any]]:
    if not blob:
        return []
    try:
        raw = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PulzeUnavailable(f"Scene Manager data is not valid base64: {exc}") from exc
    try:
        setups = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PulzeUnavailable(f"Scene Manager data is not valid JSON: {exc}") from exc
    if not isinstance(setups, list):
        raise PulzeUnavailable("Scene Manager data is not a setup list.")
    return setups


def read_setups(client: MaxClient) -> tuple[list[dict[str, Any]], str]:
    """Return (setups, token). The token guards writes against concurrent edits."""
    blob = read_blob(client)
    return decode_setups(blob), blob_token(blob)


def blob_token(blob: str) -> str:
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _output_entry(setup: dict[str, Any], key: str) -> dict[str, Any]:
    value = setup.get(key) or {}
    return value if isinstance(value, dict) else {}


def setup_name(setup: dict[str, Any], index: int) -> str:
    scenename = setup.get("scenename") or {}
    text = scenename.get("text") if isinstance(scenename, dict) else None
    if text:
        return str(text)
    camera = setup.get("camera") or {}
    if isinstance(camera, dict) and camera.get("name"):
        return str(camera["name"])
    return f"Setup {index + 1}"


def summarize(setup: dict[str, Any], index: int) -> dict[str, Any]:
    camera = setup.get("camera") or {}
    resolution = setup.get("resolution") or {}
    frames = setup.get("frames") or {}
    common = _output_entry(setup, "output_Common")
    layers = setup.get("layers") or {}
    elements = setup.get("elements") or {}
    objects = setup.get("objects") or {}
    return {
        "index": index,
        "id": setup.get("id"),
        "name": setup_name(setup, index),
        "render_enabled": bool((setup.get("render") or {}).get("enabled", False)),
        "camera": {
            "name": camera.get("name"),
            "handle": camera.get("handle"),
            "enabled": bool(camera.get("enabled", False)),
            "locked": bool(camera.get("locked", False)),
        },
        "resolution": {
            "width": resolution.get("width"),
            "height": resolution.get("height"),
            "enabled": bool(resolution.get("enabled", False)),
        },
        "frames": {
            "enabled": bool(frames.get("enabled", False)),
            "type": frames.get("type"),
            "single": frames.get("single"),
            "start": frames.get("start"),
            "end": frames.get("end"),
            "nth": frames.get("nth"),
            "list": frames.get("list"),
        },
        "output": {
            "enabled": bool(common.get("enabled", False)),
            "path": common.get("path"),
            "channels_enabled": bool(_output_entry(setup, "output_Channels").get("enabled", False)),
            "raw_enabled": bool(_output_entry(setup, "output_Raw").get("enabled", False)),
        },
        "overrides": {
            "layers": len((layers.get("set") or [])) if layers.get("enabled") else 0,
            "elements": len((elements.get("set") or [])) if elements.get("enabled") else 0,
            "objects": len((objects.get("set") or [])) if objects.get("enabled") else 0,
        },
        "notes": setup.get("notes") or None,
        "status": setup.get("status"),
    }


def probe_scene(client: MaxClient, paths: list[str]) -> dict[str, Any]:
    """One round trip: renderer, camera names, and whether each output folder exists."""
    encoded = ",".join(f'"{safe_string(p)}"' for p in paths)
    maxscript = f"""(
    local cams = ""
    for c in cameras do cams += c.name + "\\n"
    local dirs = ""
    local probe = #({encoded})
    for p in probe do (
        local folder = try (getFilenamePath p) catch ""
        dirs += (if folder != "" and (doesDirectoryExist folder) then "1" else "0")
    )
    local rend = try ((classOf renderers.current) as string) catch "unknown"
    local scene = if maxFileName == "" then "(unsaved)" else maxFileName
    "RENDERER:" + rend + "\\nSCENE:" + scene + "\\nOBJECTS:" + (objects.count as string) + \
        "\\nDIRS:" + dirs + "\\nCAMS:\\n" + cams
)"""
    payload = _send(client, maxscript)
    renderer, scene_file, dirs, cameras = "unknown", "(unknown)", "", []
    object_count = 0
    for line in payload.splitlines():
        if line.startswith("RENDERER:"):
            renderer = line[len("RENDERER:"):].strip()
        elif line.startswith("SCENE:"):
            scene_file = line[len("SCENE:"):].strip()
        elif line.startswith("OBJECTS:"):
            object_count = int(line[len("OBJECTS:"):].strip() or 0)
        elif line.startswith("DIRS:"):
            dirs = line[len("DIRS:"):].strip()
        elif line and not line.startswith("CAMS:"):
            cameras.append(line.strip())
    return {
        "renderer": renderer,
        "scene_file": scene_file,
        "object_count": object_count,
        "cameras": [c for c in cameras if c],
        "folder_exists": [flag == "1" for flag in dirs],
    }


def preflight(client: MaxClient, include_disabled: bool = False) -> dict[str, Any]:
    """Catch the failures that waste an overnight render, before it starts."""
    setups, token = read_setups(client)
    rows = [summarize(s, i) for i, s in enumerate(setups)]
    active = [r for r in rows if r["render_enabled"] or include_disabled]

    paths = [(r["output"]["path"] or "") for r in active]
    scene = probe_scene(client, paths) if active else {
        "renderer": "unknown", "scene_file": "(unknown)", "object_count": 0,
        "cameras": [], "folder_exists": [],
    }
    camera_names = {c.casefold() for c in scene["cameras"]}
    folder_exists = scene["folder_exists"]

    seen_paths: dict[str, str] = {}
    issues: list[dict[str, Any]] = []
    if active and scene["object_count"] == 0:
        issues.append({
            "setup": "(scene)", "severity": "error", "code": "scene_empty",
            "message": "The open scene has no objects, so these setups refer to a scene that is not loaded.",
        })

    def add(setup_name_: str, severity: str, code: str, message: str) -> None:
        issues.append({"setup": setup_name_, "severity": severity, "code": code, "message": message})

    for position, row in enumerate(active):
        name = row["name"]
        camera = row["camera"]["name"]
        if not camera:
            add(name, "error", "camera_missing", "Setup has no camera assigned.")
        elif camera.casefold() not in camera_names:
            add(name, "error", "camera_not_in_scene", f"Camera '{camera}' is not in the scene any more.")

        width, height = row["resolution"]["width"], row["resolution"]["height"]
        try:
            pixels = int(float(width)) * int(float(height))
        except (TypeError, ValueError):
            pixels = 0
            add(name, "error", "resolution_invalid", f"Resolution is not numeric: {width}x{height}.")
        if pixels and pixels < 640 * 480:
            add(name, "warning", "resolution_small", f"Resolution {width}x{height} looks like a test size.")

        path = row["output"]["path"] or ""
        if not row["output"]["enabled"] or not path:
            add(name, "error", "output_missing", "No output file is set, the render result will not be saved.")
        else:
            if not posixpath.splitext(path.replace("\\", "/"))[1]:
                add(name, "error", "output_no_extension", f"Output path has no file extension: {path}")
            if position < len(folder_exists) and not folder_exists[position]:
                add(name, "error", "output_folder_missing", f"Output folder does not exist: {path}")
            key = path.replace("\\", "/").casefold()
            if key in seen_paths:
                add(name, "error", "output_collision",
                    f"Same output path as setup '{seen_paths[key]}', one render will overwrite the other.")
            else:
                seen_paths[key] = name

        if row["frames"]["enabled"] and row["frames"].get("type") not in (0, None):
            add(name, "warning", "animation_range", "Setup renders a frame range, not a single frame.")

    errors = sum(1 for i in issues if i["severity"] == "error")
    return {
        "token": token,
        "renderer": scene["renderer"],
        "scene_file": scene["scene_file"],
        "scene_objects": scene["object_count"],
        "setups_total": len(rows),
        "setups_checked": len(active),
        "ok": errors == 0,
        "errors": errors,
        "warnings": len(issues) - errors,
        "issues": issues,
        "checked": [r["name"] for r in active],
    }


def _chunked_write_maxscript(blob: str) -> list[str]:
    chunks = [blob[i:i + _CHUNK] for i in range(0, len(blob), _CHUNK)]
    scripts = ['(global PULZE_MCP_BUF = "" ; "ok")']
    for chunk in chunks:
        scripts.append(f'(PULZE_MCP_BUF += "{safe_string(chunk)}" ; "ok")')
    scripts.append(_find_ca_maxscript(
        f"setProperty ca #{DATA_PROP} PULZE_MCP_BUF ; PULZE_MCP_BUF = \"\" ; \"written\""
    ))
    return scripts


def write_setups(client: MaxClient, setups: list[dict[str, Any]], expected_token: str) -> dict[str, Any]:
    """Replace the whole setup list. Refuses if the scene changed since the read."""
    current_blob = read_blob(client)
    if blob_token(current_blob) != expected_token:
        raise ValueError(
            "Scene Manager data changed since it was read (someone edited a setup). "
            "Inspect again and retry with the new token."
        )
    payload = json.dumps(setups, separators=(",", ":"), ensure_ascii=False)
    blob = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    for script in _chunked_write_maxscript(blob):
        _send(client, script)
    verify = read_blob(client)
    if verify != blob:
        raise RuntimeError("Write-back verification failed, Scene Manager data was not replaced.")
    return {
        "written": True,
        "previous_blob": current_blob,
        "previous_token": expected_token,
        "token": blob_token(blob),
    }


def restore_blob(client: MaxClient, blob: str) -> dict[str, Any]:
    """Roll back to a blob returned by a previous write."""
    for script in _chunked_write_maxscript(blob):
        _send(client, script)
    return {"restored": True, "token": blob_token(read_blob(client))}
