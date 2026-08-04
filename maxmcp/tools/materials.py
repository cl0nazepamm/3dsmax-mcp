import json as _json
import re
from datetime import datetime
from pathlib import Path

from ..helpers.maxscript import safe_string
from ..helpers.native_compat import is_missing_native_route_error
from ..max_client import MaxBridgeError

from ..server import mcp, client


_MATERIAL_LIBRARY_SOURCES = {"current", "medit", "combined", "all"}


def _error_json(message: str) -> str:
    return _json.dumps({"error": message})


def _normalize_material_library_source(source: str) -> str:
    normalized = (source or "all").strip().lower()
    aliases = {
        "currentmateriallibrary": "current",
        "current_material_library": "current",
        "library": "current",
        "temporary": "current",
        "temp": "current",
        "scratch": "current",
        "scratchpad": "current",
        "meditmaterials": "medit",
        "medit_materials": "medit",
        "material_editor": "medit",
        "material-editor": "medit",
        "editor": "medit",
        "slots": "medit",
        "both": "all",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized


def _ms_path(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _default_material_backup_dir() -> Path:
    return Path.home() / "Documents" / "3dsmax-mcp-material-backups"


def _backup_filename(label: str, stamp: str, prefix: str) -> str:
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix.strip())
    return f"{safe_prefix}{label}_{stamp}.mat"


def _flag_failed_material_backups(raw_result: str) -> str:
    try:
        payload = _json.loads(raw_result)
    except (TypeError, ValueError):
        return raw_result
    saved = payload.get("saved")
    if not isinstance(saved, list):
        return raw_result
    failed = [
        item for item in saved
        if isinstance(item, dict) and not item.get("saved") and not item.get("skipped")
    ]
    if not failed:
        return raw_result
    payload["status"] = "error"
    payload["error"] = "one or more material library backups failed"
    return _json.dumps(payload)


def _material_library_summary_maxscript(source: str, include_empty_slots: bool) -> str:
    source_literal = safe_string(source)
    include_empty = "true" if include_empty_slots else "false"
    return f"""
(
    local requestedSource = "{source_literal}"
    local includeEmptySlots = {include_empty}

    fn mcpJsonEscape value = (
        local s = if value == undefined then "" else (value as string)
        s = substituteString s "\\\\" "\\\\\\\\"
        s = substituteString s "\\"" "\\\\\\""
        s = substituteString s "\\t" "\\\\t"
        s = substituteString s "\\r" "\\\\r"
        s = substituteString s "\\n" "\\\\n"
        s
    )

    fn mcpJoinJson items = (
        local s = "["
        for i = 1 to items.count do (
            if i > 1 do s += ","
            s += items[i]
        )
        s += "]"
        s
    )

    fn mcpMaterialJson m idx sourceName = (
        if m == undefined then (
            "{{\\"index\\":" + (idx as string) + ",\\"source\\":\\"" + sourceName + "\\",\\"empty\\":true}}"
        ) else (
            local used = false
            local subCount = 0
            local texCount = 0
            local handle = ""
            try (used = isMtlUsedInSceneMtl m) catch (used = false)
            try (subCount = getNumSubMtls m) catch (subCount = 0)
            try (texCount = getNumSubTexmaps m) catch (texCount = 0)
            try (handle = (getHandleByAnim m) as string) catch (handle = "")
            "{{\\"index\\":" + (idx as string) +
                ",\\"source\\":\\"" + sourceName +
                "\\",\\"empty\\":false" +
                ",\\"name\\":\\"" + (mcpJsonEscape m.name) + "\\"" +
                ",\\"class\\":\\"" + (mcpJsonEscape (classOf m)) + "\\"" +
                ",\\"superClass\\":\\"" + (mcpJsonEscape (superClassOf m)) + "\\"" +
                ",\\"handle\\":\\"" + (mcpJsonEscape handle) + "\\"" +
                ",\\"usedInScene\\":" + (if used then "true" else "false") +
                ",\\"subMaterialCount\\":" + (subCount as string) +
                ",\\"subTexmapCount\\":" + (texCount as string) +
            "}}"
        )
    )

    fn mcpCurrentLibraryJson = (
        local items = #()
        local libCount = 0
        local libFile = ""
        try (libCount = currentMaterialLibrary.count) catch (libCount = 0)
        try (libFile = getMatLibFileName()) catch (libFile = "")
        for i = 1 to libCount do (
            local m = undefined
            try (m = currentMaterialLibrary[i]) catch (m = undefined)
            if m != undefined or includeEmptySlots do append items (mcpMaterialJson m i "current")
        )
        local temporary = (libFile == undefined or libFile == "")
        "{{\\"source\\":\\"current\\",\\"label\\":\\"currentMaterialLibrary\\",\\"file\\":\\"" +
            (mcpJsonEscape libFile) + "\\",\\"isTemporary\\":" + (if temporary then "true" else "false") +
            ",\\"count\\":" + (items.count as string) + ",\\"materials\\":" + (mcpJoinJson items) + "}}"
    )

    fn mcpMeditMaterialsJson = (
        local items = #()
        local slotCount = 0
        try (slotCount = meditMaterials.count) catch (slotCount = 0)
        for i = 1 to slotCount do (
            local m = undefined
            try (m = meditMaterials[i]) catch (m = undefined)
            if m != undefined or includeEmptySlots do append items (mcpMaterialJson m i "medit")
        )
        "{{\\"source\\":\\"medit\\",\\"label\\":\\"meditMaterials\\",\\"slotCount\\":" +
            (slotCount as string) + ",\\"count\\":" + (items.count as string) +
            ",\\"materials\\":" + (mcpJoinJson items) + "}}"
    )

    fn mcpCombinedJson = (
        local items = #()
        local currentCount = 0
        local slotCount = 0
        try (currentCount = currentMaterialLibrary.count) catch (currentCount = 0)
        try (slotCount = meditMaterials.count) catch (slotCount = 0)
        for i = 1 to currentCount do (
            local m = undefined
            try (m = currentMaterialLibrary[i]) catch (m = undefined)
            if m != undefined or includeEmptySlots do append items (mcpMaterialJson m i "current")
        )
        for i = 1 to slotCount do (
            local m = undefined
            try (m = meditMaterials[i]) catch (m = undefined)
            if m != undefined or includeEmptySlots do append items (mcpMaterialJson m i "medit")
        )
        "{{\\"source\\":\\"combined\\",\\"label\\":\\"currentMaterialLibrary+meditMaterials\\",\\"count\\":" +
            (items.count as string) + ",\\"materials\\":" + (mcpJoinJson items) + "}}"
    )

    if requestedSource == "current" then (
        mcpCurrentLibraryJson()
    ) else if requestedSource == "medit" then (
        mcpMeditMaterialsJson()
    ) else if requestedSource == "combined" then (
        mcpCombinedJson()
    ) else (
        local currentJson = mcpCurrentLibraryJson()
        local meditJson = mcpMeditMaterialsJson()
        local warnings = #()
        local libFile = ""
        local libCount = 0
        try (libFile = getMatLibFileName()) catch (libFile = "")
        try (libCount = currentMaterialLibrary.count) catch (libCount = 0)
        if libCount > 0 and (libFile == undefined or libFile == "") do (
            append warnings "\\"currentMaterialLibrary has no backing .mat file; use backup_material_library before restarting 3ds Max\\""
        )
        "{{\\"source\\":\\"all\\",\\"currentMaterialLibrary\\":" + currentJson +
            ",\\"meditMaterials\\":" + meditJson +
            ",\\"warnings\\":" + (mcpJoinJson warnings) + "}}"
    )
)
"""


def _material_library_backup_maxscript(
    source: str,
    current_path: Path,
    medit_path: Path,
    combined_path: Path,
) -> str:
    source_literal = safe_string(source)
    current_literal = safe_string(_ms_path(current_path))
    medit_literal = safe_string(_ms_path(medit_path))
    combined_literal = safe_string(_ms_path(combined_path))
    return f"""
(
    local requestedSource = "{source_literal}"
    local currentPath = @"{current_literal}"
    local meditPath = @"{medit_literal}"
    local combinedPath = @"{combined_literal}"

    fn mcpJsonEscape value = (
        local s = if value == undefined then "" else (value as string)
        s = substituteString s "\\\\" "\\\\\\\\"
        s = substituteString s "\\"" "\\\\\\""
        s = substituteString s "\\t" "\\\\t"
        s = substituteString s "\\r" "\\\\r"
        s = substituteString s "\\n" "\\\\n"
        s
    )

    fn mcpJoinJson items = (
        local s = "["
        for i = 1 to items.count do (
            if i > 1 do s += ","
            s += items[i]
        )
        s += "]"
        s
    )

    fn mcpEnsureParentDir path = (
        local dir = getFilenamePath path
        if dir != undefined and dir != "" do (
            try ((dotNetClass "System.IO.Directory").CreateDirectory dir) catch ()
        )
    )

    fn mcpSaveResult sourceName path count saved skipped errorMsg = (
        "{{\\"source\\":\\"" + sourceName +
            "\\",\\"path\\":\\"" + (mcpJsonEscape path) +
            "\\",\\"count\\":" + (count as string) +
            ",\\"saved\\":" + (if saved then "true" else "false") +
            ",\\"skipped\\":" + (if skipped then "true" else "false") +
            ",\\"error\\":\\"" + (mcpJsonEscape errorMsg) + "\\"}}"
    )

    fn mcpSaveCurrent path = (
        local count = 0
        local saved = false
        local errorMsg = ""
        try (count = currentMaterialLibrary.count) catch (count = 0)
        if count <= 0 then (
            mcpSaveResult "current" path count false true "currentMaterialLibrary is empty"
        ) else (
            try (
                mcpEnsureParentDir path
                saveTempMaterialLibrary currentMaterialLibrary path
                saved = doesFileExist path
            ) catch (
                errorMsg = getCurrentException()
                saved = false
            )
            mcpSaveResult "current" path count saved false errorMsg
        )
    )

    fn mcpBuildMeditLibrary = (
        local lib = materialLibrary()
        local slotCount = 0
        try (slotCount = meditMaterials.count) catch (slotCount = 0)
        for i = 1 to slotCount do (
            local m = undefined
            try (m = meditMaterials[i]) catch (m = undefined)
            if m != undefined do append lib (copy m)
        )
        lib
    )

    fn mcpBuildCombinedLibrary = (
        local lib = materialLibrary()
        local currentCount = 0
        local slotCount = 0
        try (currentCount = currentMaterialLibrary.count) catch (currentCount = 0)
        try (slotCount = meditMaterials.count) catch (slotCount = 0)
        for i = 1 to currentCount do (
            local m = undefined
            try (m = currentMaterialLibrary[i]) catch (m = undefined)
            if m != undefined do append lib (copy m)
        )
        for i = 1 to slotCount do (
            local m = undefined
            try (m = meditMaterials[i]) catch (m = undefined)
            if m != undefined do append lib (copy m)
        )
        lib
    )

    fn mcpSaveTempLib sourceName lib path = (
        local count = lib.count
        local saved = false
        local errorMsg = ""
        if count <= 0 then (
            mcpSaveResult sourceName path count false true (sourceName + " material library is empty")
        ) else (
            try (
                mcpEnsureParentDir path
                saveTempMaterialLibrary lib path
                saved = doesFileExist path
            ) catch (
                errorMsg = getCurrentException()
                saved = false
            )
            mcpSaveResult sourceName path count saved false errorMsg
        )
    )

    local results = #()
    if requestedSource == "all" or requestedSource == "current" do append results (mcpSaveCurrent currentPath)
    if requestedSource == "all" or requestedSource == "medit" do append results (mcpSaveTempLib "medit" (mcpBuildMeditLibrary()) meditPath)
    if requestedSource == "all" or requestedSource == "combined" do append results (mcpSaveTempLib "combined" (mcpBuildCombinedLibrary()) combinedPath)

    "{{\\"source\\":\\"" + requestedSource + "\\",\\"saved\\":" + (mcpJoinJson results) + "}}"
)
"""


@mcp.tool()
def get_materials() -> str:
    """List all materials assigned to objects in the current 3ds Max scene."""
    if client.native_available:
        try:
            response = client.send_command(_json.dumps({}), cmd_type="native:get_materials")
            return response.get("result", "[]")
        except RuntimeError:
            pass

    maxscript = r"""(
        local arr = #()
        local matSet = #()
        for obj in objects where obj.material != undefined do (
            local mat = obj.material
            local idx = findItem matSet mat.name
            if idx == 0 then (
                append matSet mat.name
                local objNames = for o in objects where o.material != undefined \
                    and o.material.name == mat.name collect o.name
                local objNameArr = for n in objNames collect ("\"" + n + "\"")
                local objStr = "["
                for i = 1 to objNameArr.count do (
                    if i > 1 do objStr += ","
                    objStr += objNameArr[i]
                )
                objStr += "]"
                local entry = "{" + \
                    "\"name\":\"" + mat.name + "\"," + \
                    "\"class\":\"" + ((classOf mat) as string) + "\"," + \
                    "\"assignedTo\":" + objStr + \
                "}"
                append arr entry
            )
        )
        local result = "["
        for i = 1 to arr.count do (
            if i > 1 do result += ","
            result += arr[i]
        )
        result += "]"
        result
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "[]")


@mcp.tool()
def get_material_library(source: str = "all", include_empty_slots: bool = False) -> str:
    """Inspect the active material library scratchpad and Material Editor slots.

    source accepts:
      current — 3ds Max currentMaterialLibrary, also known as the temporary
        material library or scratchpad.
      medit — the 24 Compact Material Editor sample slots (meditMaterials).
      combined — currentMaterialLibrary followed by meditMaterials.
      all — currentMaterialLibrary and meditMaterials as separate sections.
    """
    normalized = _normalize_material_library_source(source)
    if normalized not in _MATERIAL_LIBRARY_SOURCES:
        return _error_json(
            "source must be one of: current, medit, combined, all"
        )

    if client.native_available:
        try:
            response = client.send_command(
                _json.dumps({
                    "source": normalized,
                    "include_empty_slots": include_empty_slots,
                }),
                cmd_type="native:get_material_library",
            )
            return response.get("result", "{}")
        except (MaxBridgeError, RuntimeError) as exc:
            if not is_missing_native_route_error(exc):
                raise

    maxscript = _material_library_summary_maxscript(normalized, include_empty_slots)
    response = client.send_command(maxscript, cmd_type="maxscript")
    return response.get("result", "{}")


@mcp.tool()
def backup_material_library(
    source: str = "all",
    backup_dir: str = "",
    file_path: str = "",
    prefix: str = "",
) -> str:
    """Save material-library scratchpads to .mat files without changing the scene.

    source accepts current, medit, combined, or all. The default all writes three
    files: currentMaterialLibrary, meditMaterials, and a combined scratchpad.
    Pass file_path only when saving a single source; backup_dir is used for
    timestamped backups.
    """
    normalized = _normalize_material_library_source(source)
    if normalized not in _MATERIAL_LIBRARY_SOURCES:
        return _error_json(
            "source must be one of: current, medit, combined, all"
        )
    if file_path and normalized == "all":
        return _error_json("file_path can only be used with a single source")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if file_path:
        exact_path = Path(file_path).expanduser()
        exact_path.parent.mkdir(parents=True, exist_ok=True)
        current_path = medit_path = combined_path = exact_path
    else:
        root = Path(backup_dir).expanduser() if backup_dir else _default_material_backup_dir()
        root.mkdir(parents=True, exist_ok=True)
        current_path = root / _backup_filename("currentMaterialLibrary", stamp, prefix)
        medit_path = root / _backup_filename("meditMaterials", stamp, prefix)
        combined_path = root / _backup_filename("combined_material_scratchpad", stamp, prefix)

    if client.native_available:
        try:
            response = client.send_command(
                _json.dumps({
                    "source": normalized,
                    "current_path": _ms_path(current_path),
                    "medit_path": _ms_path(medit_path),
                    "combined_path": _ms_path(combined_path),
                }),
                cmd_type="native:backup_material_library",
                timeout=45.0,
            )
            return _flag_failed_material_backups(
                response.get("result", "{}")
            )
        except (MaxBridgeError, RuntimeError) as exc:
            if not is_missing_native_route_error(exc):
                raise

    maxscript = _material_library_backup_maxscript(
        normalized,
        current_path=current_path,
        medit_path=medit_path,
        combined_path=combined_path,
    )
    response = client.send_command(maxscript, cmd_type="maxscript", timeout=45.0)
    return _flag_failed_material_backups(response.get("result", "{}"))
