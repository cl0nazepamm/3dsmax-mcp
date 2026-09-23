"""Pulze Scene Manager reads and guarded writes.

Scene Manager keeps its setups in a scene custom attribute on the root node
(``SceneManager_Attributes``), not in its UI: ``SceneManager_Dat`` is a
base64-encoded JSON array, one object per setup. Reading it needs no Pulze
function call and no window, so setup inspection works headless.

Two rules decide whether a write survives:

* The camera is resolved by node handle (``node.handle``), not by name.
* An open Scene Manager window owns the data: it keeps its own copy and writes
  it back over the attribute, so an external write made while it is open is
  lost. Writes are refused while the window exists.

Every write is one MAXScript call: window and undo checks, target and token
re-check, the new blob, a single undo record, read-back, and rollback if the
read-back fails. Nothing is staged in a global. The previous blob is saved as
a backup file before the call, bound to the Max process and scene file.

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
import os
import posixpath
import tempfile
import time
import uuid
from typing import Any

from ..max_client import MaxClient
from .maxscript import safe_string

CA_NAME = "SceneManager_Attributes"
DATA_PROP = "SceneManager_Dat"
WINDOW_TITLE = "Scene Manager"
MAX_RESOLUTION = 32768
BACKUP_DIR = os.path.join(tempfile.gettempdir(), "3dsmax-mcp", "pulze_backups")


class PulzeUnavailable(RuntimeError):
    """Scene Manager data is not present in the open scene."""


class PulzeRefused(RuntimeError):
    """A write or restore was refused before anything changed."""


# MAXScript shared by every call: find the attribute, hash like blob_token(),
# identify the scene, and detect a Scene Manager window owned by this Max.
_MS_LIB = r'''
    fn pzFindCA = (
        local ca = undefined
        for i = 1 to (custAttributes.count rootNode) while ca == undefined do (
            local c = custAttributes.get rootNode i
            if (matchPattern (c as string) pattern:"*%(ca)s*") do ca = c
        )
        ca
    )
    fn pzToken s = (
        local sha = dotNetObject "System.Security.Cryptography.SHA1Managed"
        local h = sha.ComputeHash ((dotNetClass "System.Text.Encoding").UTF8.GetBytes s)
        sha.Dispose()
        local hex = ""
        for i = 1 to 6 do hex += formattedPrint h[i] format:"02x"
        hex
    )
    fn pzScene = (
        ((dotNetClass "System.Diagnostics.Process").GetCurrentProcess()).Id as string + "|" + maxFilePath + maxFileName
    )
    fn pzWindowOpen = (
        local open = false
        local maxHwnd = windows.getMAXHWND()
        for h in (windows.getChildrenHWND 0) where (h[6] == maxHwnd or h[7] == maxHwnd) and (matchPattern h[5] pattern:"*%(title)s*") while not open do (
            local src = try ((dotNetClass "System.Windows.Interop.HwndSource").FromHwnd (dotNetObject "System.IntPtr" (h[1] as integer64))) catch undefined
            open = if src == undefined then true else (try (src.RootVisual.IsVisible) catch true)
        )
        open
    )
''' % {"ca": CA_NAME, "title": WINDOW_TITLE}


def _send(client: MaxClient, maxscript: str, timeout: float = 60.0) -> str:
    response = client.send_command(maxscript, timeout=timeout)
    return str(response.get("result", ""))


def blob_token(blob: str) -> str:
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------- reads

def read_state(client: MaxClient) -> dict[str, Any]:
    """Blob, token, scene identity and window state in one call."""
    raw = _send(client, "(" + _MS_LIB + r'''
    local ca = pzFindCA()
    if ca == undefined then "__PULZE_ABSENT__" else (
        local b = getProperty ca #%(prop)s
        if b == undefined do b = ""
        "S|" + pzScene() + "\n" + "W|" + (pzWindowOpen() as string) + "\n" + "B|" + b
    )
)''' % {"prop": DATA_PROP})
    if raw == "__PULZE_ABSENT__":
        raise PulzeUnavailable(
            "No Scene Manager data in this scene. Open a scene saved with Pulze "
            "Scene Manager, or add at least one setup first."
        )
    try:
        s_line, w_line, b_line = raw.split("\n", 2)
        if not (s_line.startswith("S|") and w_line.startswith("W|") and b_line.startswith("B|")):
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("Unexpected Scene Manager read result") from exc
    blob = b_line[2:].strip()
    return {"blob": blob, "token": blob_token(blob), "scene": s_line[2:],
            "window_open": w_line[2:].strip().lower() == "true"}


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


def encode_setups(setups: list[dict[str, Any]]) -> str:
    payload = json.dumps(setups, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def read_setups(client: MaxClient) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state = read_state(client)
    return decode_setups(state["blob"]), state


# ------------------------------------------------------------------ summary

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


def _notes_text(setup: dict[str, Any]) -> str | None:
    notes = setup.get("notes")
    if isinstance(notes, dict):
        text = notes.get("text")
        return str(text) if text else None
    return str(notes) if notes else None


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
        "notes": _notes_text(setup),
        "status": setup.get("status"),
    }


# --------------------------------------------------------------- validation

def positive_int(value: Any, label: str) -> int:
    """Whole number 1..MAX_RESOLUTION, given as int or digit string. Scene Manager stores strings."""
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number of pixels, not a boolean")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or not 1 <= value <= MAX_RESOLUTION:
        raise ValueError(f"{label} must be a whole number from 1 to {MAX_RESOLUTION}, got {value!r}")
    return value


def stored_resolution_ok(width: Any, height: Any) -> bool:
    try:
        positive_int(width, "width")
        positive_int(height, "height")
        return True
    except ValueError:
        return False


def select_setup(setups: list[dict[str, Any]], edit: dict[str, Any]) -> int:
    """Exactly one of index / id / name. Ambiguous ids or names are refused."""
    keys = [k for k in ("index", "id", "name") if k in edit]
    if len(keys) != 1:
        raise ValueError("Each edit needs exactly one selector: index, id or name.")
    key = keys[0]
    value = edit[key]
    if key == "index":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("index must be an integer.")
        if not 0 <= value < len(setups):
            raise ValueError(f"Setup index {value} is out of range (0..{len(setups) - 1}).")
        return value
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string.")
    if key == "id":
        hits = [i for i, s in enumerate(setups) if s.get("id") == value]
        if len(hits) > 1:
            raise ValueError(f"Setup id '{value}' is used by setups {hits}; select by index.")
        if not hits:
            raise ValueError(f"No setup with id '{value}'.")
        return hits[0]
    names = [setup_name(s, i) for i, s in enumerate(setups)]
    hits = [i for i, n in enumerate(names) if n == value]
    if not hits:
        hits = [i for i, n in enumerate(names) if n.casefold() == value.casefold()]
    if len(hits) > 1:
        raise ValueError(f"Setup name '{value}' matches setups {hits}; select by index or id.")
    if not hits:
        raise ValueError(f"No setup called '{value}'. Available: {', '.join(names) or '(none)'}")
    return hits[0]


def resolve_camera(client: MaxClient, name: str) -> int:
    """Scene Manager resolves a setup camera by node handle, so the name must be unique."""
    answer = _send(client, f'''(
    local hits = getNodeByName "{safe_string(name)}" exact:true all:true
    if hits.count == 0 then "__MISSING__"
    else if hits.count > 1 then ("__AMBIGUOUS__|" + hits.count as string)
    else if (superClassOf hits[1] != camera) then "__NOT_A_CAMERA__"
    else (hits[1].handle as string)
)''').strip()
    if answer == "__MISSING__":
        raise ValueError(f"No object called '{name}' in the scene.")
    if answer.startswith("__AMBIGUOUS__"):
        raise ValueError(f"{answer.split('|')[1]} objects are called '{name}'; rename them so the camera is unique.")
    if answer == "__NOT_A_CAMERA__":
        raise ValueError(f"'{name}' is in the scene but is not a camera.")
    return int(answer)


# ---------------------------------------------------------------- preflight

def probe_scene(client: MaxClient, paths: list[str], cams: list[tuple[str, Any]]) -> dict[str, Any]:
    """One round trip: renderer, scene, output folders and each setup camera's handle."""
    encoded = ",".join(f'"{safe_string(p)}"' for p in paths)
    handles = ",".join(
        (str(int(h)) if isinstance(h, (int, str)) and str(h).strip().isdigit() else "-1") for _, h in cams)
    payload = _send(client, f'''(
    local ss = stringStream ""
    local probe = #({encoded})
    local dirs = ""
    for p in probe do (
        local folder = try (getFilenamePath p) catch ""
        dirs += (if folder != "" and (doesDirectoryExist folder) then "1" else "0")
    )
    format "RENDERER:%\\n" (try ((classOf renderers.current) as string) catch "unknown") to:ss
    format "SCENE:%\\n" (if maxFileName == "" then "(unsaved)" else maxFileName) to:ss
    format "OBJECTS:%\\n" objects.count to:ss
    format "DIRS:%\\n" dirs to:ss
    for h in #({handles}) do (
        local n = if h < 0 then undefined else (maxOps.getNodeByHandle h)
        if n == undefined then format "CAM:missing|\\n" to:ss
        else if (superClassOf n != camera) then format "CAM:not_camera|%\\n" n.name to:ss
        else format "CAM:ok|%\\n" n.name to:ss
    )
    ss as string
)''')
    out = {"renderer": "unknown", "scene_file": "(unknown)", "object_count": 0,
           "folder_exists": [], "camera_nodes": []}
    for line in payload.splitlines():
        if line.startswith("RENDERER:"):
            out["renderer"] = line[9:].strip()
        elif line.startswith("SCENE:"):
            out["scene_file"] = line[6:].strip()
        elif line.startswith("OBJECTS:"):
            out["object_count"] = int(line[8:].strip() or 0)
        elif line.startswith("DIRS:"):
            out["folder_exists"] = [f == "1" for f in line[5:].strip()]
        elif line.startswith("CAM:"):
            status, _, node_name = line[4:].partition("|")
            out["camera_nodes"].append((status, node_name))
    return out


def camera_issue(name: str | None, handle: Any, node: tuple[str, str] | None) -> tuple[str, str, str] | None:
    """Pure decision for one setup camera: (severity, code, message) or None."""
    if not name and handle in (None, "", -1):
        return ("error", "camera_missing", "Setup has no camera assigned.")
    if handle in (None, "") or not str(handle).strip().lstrip("-").isdigit():
        return ("error", "camera_unbound",
                f"Camera '{name}' has no node handle; Scene Manager cannot resolve it. Reassign it with pulze_set.")
    status, node_name = node or ("missing", "")
    if status == "missing":
        return ("error", "camera_handle_invalid",
                f"Camera '{name}' (handle {handle}) no longer exists. A camera with the same name does not fix this; "
                "reassign it with pulze_set so Scene Manager gets the new handle.")
    if status == "not_camera":
        return ("error", "camera_handle_not_camera", f"Handle {handle} now belongs to '{node_name}', which is not a camera.")
    if name and node_name != name:
        return ("warning", "camera_renamed",
                f"Setup stores camera name '{name}' but handle {handle} is now '{node_name}'. It renders the right camera; "
                "reassign with pulze_set to refresh the stored name.")
    return None


def preflight(client: MaxClient, include_disabled: bool = False) -> dict[str, Any]:
    """Catch the failures that waste an overnight render, before it starts."""
    setups, state = read_setups(client)
    rows = [summarize(s, i) for i, s in enumerate(setups)]
    active = [r for r in rows if r["render_enabled"] or include_disabled]
    scene = probe_scene(client, [(r["output"]["path"] or "") for r in active],
                        [(r["camera"]["name"], r["camera"]["handle"]) for r in active]) if active else None

    issues: list[dict[str, Any]] = []

    def add(setup: str, severity: str, code: str, message: str) -> None:
        issues.append({"setup": setup, "severity": severity, "code": code, "message": message})

    if scene and scene["object_count"] == 0:
        add("(scene)", "error", "scene_empty",
            "The open scene has no objects, so these setups refer to a scene that is not loaded.")
    seen_paths: dict[str, str] = {}
    for pos, row in enumerate(active):
        name = row["name"]
        node = scene["camera_nodes"][pos] if scene and pos < len(scene["camera_nodes"]) else None
        issue = camera_issue(row["camera"]["name"], row["camera"]["handle"], node)
        if issue:
            add(name, *issue)

        width, height = row["resolution"]["width"], row["resolution"]["height"]
        if not stored_resolution_ok(width, height):
            add(name, "error", "resolution_invalid",
                f"Resolution {width}x{height} is not two whole numbers from 1 to {MAX_RESOLUTION}.")
        elif int(width) * int(height) < 640 * 480:
            add(name, "warning", "resolution_small", f"Resolution {width}x{height} looks like a test size.")

        path = row["output"]["path"] or ""
        if not row["output"]["enabled"] or not path:
            add(name, "warning", "output_missing",
                "No output file is set, the render result is only kept in the frame buffer.")
        else:
            if not posixpath.splitext(path.replace("\\", "/"))[1]:
                add(name, "error", "output_no_extension", f"Output path has no file extension: {path}")
            exists = scene["folder_exists"] if scene else []
            if pos < len(exists) and not exists[pos]:
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
        "token": state["token"],
        "window_open": state["window_open"],
        "renderer": scene["renderer"] if scene else "unknown",
        "scene_file": scene["scene_file"] if scene else "(unknown)",
        "scene_objects": scene["object_count"] if scene else 0,
        "setups_total": len(rows),
        "setups_checked": len(active),
        "ok": errors == 0,
        "errors": errors,
        "warnings": len(issues) - errors,
        "issues": issues,
        "checked": [r["name"] for r in active],
    }


# ------------------------------------------------------------------- writes

def _commit_script(expected_scene: str, expected_token: str, new_blob: str, label: str) -> str:
    return "(" + _MS_LIB + r'''
    local result = undefined
    try (
        if theHold.Holding() do throw "PZ|undo_open|Another undo operation is in progress in 3ds Max; finish it and retry."
        if pzWindowOpen() do throw "PZ|window_open|The Scene Manager window is open and owns the setups; close it, then retry."
        local ca = pzFindCA()
        if ca == undefined do throw "PZ|absent|Scene Manager data is gone from this scene."
        if pzScene() != "%(scene)s" do throw "PZ|scene_changed|A different scene or 3ds Max process is active now."
        local cur = getProperty ca #%(prop)s
        if cur == undefined do cur = ""
        if pzToken cur != "%(token)s" do throw ("PZ|token_stale|Scene Manager data changed since it was read (" + (pzToken cur) + ").")
        local nb = "%(blob)s"
        undo "%(label)s" on (setProperty ca #%(prop)s nb)
        if (getProperty ca #%(prop)s) != nb do (
            setProperty ca #%(prop)s cur
            throw "PZ|readback_failed|Read-back did not match; the previous data was put back."
        )
        result = "OK|" + pzToken nb
    ) catch (
        result = "__ERROR__|" + (getCurrentException() as string)
    )
    result
)''' % {"scene": safe_string(expected_scene), "prop": DATA_PROP, "token": expected_token,
         "blob": new_blob, "label": safe_string(label)}


def _commit(client: MaxClient, expected_scene: str, expected_token: str, new_blob: str, label: str) -> str:
    raw = _send(client, _commit_script(expected_scene, expected_token, new_blob, label), timeout=120.0)
    if raw.startswith("OK|"):
        token = raw[3:].strip()
        if token != blob_token(new_blob):
            raise RuntimeError("Write reported success but the stored data hashes differently.")
        return token
    detail = raw.split("|", 1)[1] if raw.startswith("__ERROR__|") else raw
    if "PZ|" in detail:
        _, code, message = detail[detail.index("PZ|"):].split("|", 2)
        raise PulzeRefused(f"{code}: {message.strip()} Nothing was changed.")
    raise RuntimeError(f"Scene Manager write failed: {detail}")


def _backup_path(backup_id: str) -> str:
    if not backup_id or not all(c in "0123456789abcdef" for c in backup_id):
        raise ValueError("backup_id is not a valid id.")
    return os.path.join(BACKUP_DIR, f"{backup_id}.json")


def _save_backup(record: dict[str, Any]) -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = _backup_path(record["backup_id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, path)


def _load_backup(backup_id: str) -> dict[str, Any]:
    with open(_backup_path(backup_id), encoding="utf-8") as f:
        return json.load(f)


def write_setups(client: MaxClient, state: dict[str, Any], setups: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the setup list atomically. `state` is the read the edits were based on."""
    if state["window_open"]:
        raise PulzeRefused("window_open: The Scene Manager window is open and owns the setups; "
                           "close it, then retry. Nothing was changed.")
    new_blob = encode_setups(setups)
    backup_id = uuid.uuid4().hex[:16]
    _save_backup({"backup_id": backup_id, "created": time.time(), "scene": state["scene"],
                  "before_blob": state["blob"], "before_token": state["token"],
                  "after_token": blob_token(new_blob), "applied": False})
    try:
        token = _commit(client, state["scene"], state["token"], new_blob, "MCP: edit Pulze setups")
    except Exception:
        os.remove(_backup_path(backup_id))
        raise
    record = _load_backup(backup_id)
    record["applied"] = True
    _save_backup(record)
    return {"written": True, "backup_id": backup_id, "token": token,
            "undo": "One undo step in 3ds Max: 'MCP: edit Pulze setups'."}


def restore_backup(client: MaxClient, backup_id: str) -> dict[str, Any]:
    """Put back the setups from before a pulze_set, only onto the same scene and only if untouched since."""
    path = _backup_path(backup_id)
    if not os.path.exists(path):
        raise ValueError(f"No backup '{backup_id}'.")
    record = _load_backup(backup_id)
    if not record.get("applied"):
        raise PulzeRefused("not_applied: That edit never completed, so there is nothing to restore.")
    state = read_state(client)
    if state["scene"] != record["scene"]:
        raise PulzeRefused("scene_changed: The backup belongs to another scene or 3ds Max process. Nothing was changed.")
    if state["token"] == record["before_token"]:
        return {"restored": True, "already": True, "token": state["token"]}
    if state["token"] != record["after_token"]:
        raise PulzeRefused("newer_edits: Scene Manager data changed after that edit; restoring would overwrite "
                           "newer work. Nothing was changed.")
    if state["window_open"]:
        raise PulzeRefused("window_open: The Scene Manager window is open and owns the setups; "
                           "close it, then retry. Nothing was changed.")
    token = _commit(client, state["scene"], state["token"], record["before_blob"], "MCP: restore Pulze setups")
    after = read_state(client)
    if after["blob"] != record["before_blob"]:
        raise RuntimeError("Restore read-back does not match the backup.")
    record["restored"] = time.time()
    _save_backup(record)
    return {"restored": True, "token": token}
