r"""Live parity checks for the native ports added in the current worktree.

Run only against a disposable/held 3ds Max scene:

    .venv\Scripts\python.exe scripts\live_native_port_parity.py

The harness executes each public Python wrapper twice against the same Max
session: once through the native route and once through its retained MAXScript
fallback. It prints a compact JSON report and exits non-zero on an unexpected
parity failure. The caller is responsible for File Hold/Fetch around the run.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import server as _server  # noqa: E402,F401
from src.max_client import MaxBridgeError, MaxClient  # noqa: E402
from src.tools import capabilities, identify, material_ops, materials  # noqa: E402


PREFIX = "MCP_PortParity_"
OUT_ROOT = Path(tempfile.gettempdir()) / "3dsmax-mcp-native-port-parity"


class RoutedClient:
    """Expose a selected wrapper branch while using the real live transport."""

    def __init__(self, live: MaxClient, native_available: bool) -> None:
        self._live = live
        self.native_available = native_available

    def send_command(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._live.send_command(*args, **kwargs)


def parse_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def deep_diff(expected: Any, actual: Any, path: str = "$") -> list[str]:
    diffs: list[str] = []
    if type(expected) is not type(actual):
        return [
            f"{path}: type {type(expected).__name__} != "
            f"{type(actual).__name__}"
        ]
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}"
            if key not in expected:
                diffs.append(f"{child}: unexpected")
            elif key not in actual:
                diffs.append(f"{child}: missing")
            else:
                diffs.extend(deep_diff(expected[key], actual[key], child))
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            diffs.append(f"{path}: length {len(expected)} != {len(actual)}")
        for index, (left, right) in enumerate(zip(expected, actual)):
            diffs.extend(deep_diff(left, right, f"{path}[{index}]"))
    elif expected != actual:
        diffs.append(f"{path}: {expected!r} != {actual!r}")
    return diffs


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(
        self,
        name: str,
        passed: bool,
        *,
        details: Any = None,
        expected_difference: bool = False,
    ) -> None:
        item: dict[str, Any] = {
            "name": name,
            "passed": bool(passed),
        }
        if expected_difference:
            item["expected_difference"] = True
        if details not in (None, [], {}, ""):
            item["details"] = details
        self.checks.append(item)

    @property
    def passed(self) -> bool:
        return all(item["passed"] for item in self.checks)

    def payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "passed_count": sum(item["passed"] for item in self.checks),
            "check_count": len(self.checks),
            "checks": self.checks,
        }


def call_wrapper(
    module: Any,
    function: Any,
    live: MaxClient,
    *,
    native: bool,
    **kwargs: Any,
) -> Any:
    with patch.object(module, "client", RoutedClient(live, native)):
        return function(**kwargs)


def run_maxscript(live: MaxClient, code: str) -> str:
    response = live.send_command(code, cmd_type="maxscript")
    return str(response.get("result", ""))


def setup_fixture(live: MaxClient) -> None:
    script = r"""(
        resetMaxFile #noPrompt;
        while currentMaterialLibrary.count > 0 do deleteItem currentMaterialLibrary 1;
        global MCP_PortParity_Render = PhysicalMaterial();
        global MCP_PortParity_Export = PhysicalMaterial();
        MCP_PortParity_Render.name = "MCP_PortParity_Render";
        MCP_PortParity_Export.name = "MCP_PortParity_Export";
        append currentMaterialLibrary MCP_PortParity_Render;
        append currentMaterialLibrary MCP_PortParity_Export;
        meditMaterials[1] = MCP_PortParity_Render;
        meditMaterials[2] = MCP_PortParity_Export;
        local used = box name:"MCP_PortParity_UsedMaterial" width:8 length:8 height:8 pos:[0,0,4];
        used.material = MCP_PortParity_Render;
        local exportHolder = box name:"MCP_PortParity_ExportMaterial" width:8 length:8 height:8 pos:[0,-15,4];
        exportHolder.material = MCP_PortParity_Export;
        local baselineTarget = box name:"MCP_PortParity_ShellBaseline" width:8 length:8 height:8 pos:[15,0,4];
        local nativeTarget = box name:"MCP_PortParity_ShellNative" width:8 length:8 height:8 pos:[30,0,4];
        local missingBaseline = box name:"MCP_PortParity_MissingBaseline" width:8 length:8 height:8 pos:[45,0,4];
        local missingNative = box name:"MCP_PortParity_MissingNative" width:8 length:8 height:8 pos:[60,0,4];
        local duplicateA = box name:"MCP_PortParity_Duplicate" width:6 length:6 height:6 pos:[75,0,3];
        local duplicateB = box name:"MCP_PortParity_Duplicate" width:6 length:6 height:6 pos:[85,0,3];
        local root = box name:"MCP_PortParity_CaptureRoot" width:10 length:10 height:10 pos:[0,25,5];
        local child = sphere name:"MCP_PortParity_CaptureChild" radius:3 pos:[0,25,14];
        child.parent = root;
        local inst = instance root;
        inst.name = "MCP_PortParity_CaptureInstance";
        inst.pos = [20,25,5];
        local collideA = box name:"MCP_PortParity_A:B" width:7 length:7 height:7 pos:[40,25,3.5];
        local collideB = sphere name:"MCP_PortParity_A?B" radius:4 pos:[55,25,4];
        local hidden = box name:"MCP_PortParity_HiddenSentinel" width:5 length:5 height:5 pos:[70,25,2.5];
        hidden.isHidden = true;
        viewport.setType #view_persp_user;
        viewport.setTM (matrix3 [1,0,0] [0,0.8,-0.6] [0,0.6,0.8] [12,-140,85]);
        viewport.SetFOV 45.0;
        select #(root, inst, collideA, collideB);
        completeRedraw();
        "fixture-ready"
    )"""
    result = run_maxscript(live, script)
    if result != "fixture-ready":
        raise RuntimeError(f"Fixture setup failed: {result}")


def fixture_state(live: MaxClient) -> str:
    script = r"""(
        local selHandles = for n in selection collect ((getHandleByAnim n) as string);
        local hiddenStates = for n in objects where matchPattern n.name pattern:"MCP_PortParity_*" collect (n.name + ":" + (n.isHidden as string));
        sort selHandles;
        sort hiddenStates;
        (viewport.getType() as string) + "|" +
        ((viewport.getTM()) as string) + "|" +
        ((viewport.GetFOV()) as string) + "|" +
        (selHandles as string) + "|" +
        (hiddenStates as string)
    )"""
    return run_maxscript(live, script)


def reset_test_view_and_selection(live: MaxClient) -> None:
    script = r"""(
        viewport.setType #view_persp_user;
        viewport.setTM (matrix3 [1,0,0] [0,0.8,-0.6] [0,0.6,0.8] [12,-140,85]);
        viewport.SetFOV 45.0;
        local root = getNodeByName "MCP_PortParity_CaptureRoot";
        local inst = getNodeByName "MCP_PortParity_CaptureInstance";
        local collideA = getNodeByName "MCP_PortParity_A:B";
        local collideB = getNodeByName "MCP_PortParity_A?B";
        select #(root, inst, collideA, collideB);
        for n in objects where matchPattern n.name pattern:"MCP_PortParity_*" do n.isHidden = false;
        (getNodeByName "MCP_PortParity_HiddenSentinel").isHidden = true;
        completeRedraw();
        "state-reset"
    )"""
    result = run_maxscript(live, script)
    if result != "state-reset":
        raise RuntimeError(f"Could not reset capture state: {result}")


def logical_backup(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": payload.get("source"),
        "saved": [
            {
                "source": item.get("source"),
                "count": item.get("count"),
                "saved": item.get("saved"),
                "skipped": item.get("skipped"),
                "error": item.get("error"),
            }
            for item in payload.get("saved", [])
        ],
        "status": payload.get("status"),
        "error": payload.get("error"),
    }


def inspect_saved_library(live: MaxClient, path: str) -> str:
    safe_path = path.replace("\\", "/").replace('"', '\\"')
    script = f"""(
        local lib = loadTempMaterialLibrary @"{safe_path}";
        local parts = #();
        for m in lib do append parts (((classOf m) as string) + ":" + m.name);
        sort parts;
        (lib.count as string) + "|" + (parts as string)
    )"""
    return run_maxscript(live, script)


def shell_state(live: MaxClient, node_name: str) -> str:
    safe = node_name.replace("\\", "\\\\").replace('"', '\\"')
    script = f"""(
        local n = getNodeByName "{safe}";
        local m = if n != undefined then n.material else undefined;
        if m == undefined then "undefined" else (
            local originalName = if m.originalMaterial == undefined then "undefined" else m.originalMaterial.name;
            local bakedClass = if m.bakedMaterial == undefined then "undefined" else ((classOf m.bakedMaterial) as string);
            ((classOf m) as string) + "|" + originalName + "|" + bakedClass + "|" +
            (m.renderMtlIndex as string) + "|" + (m.viewportMtlIndex as string)
        )
    )"""
    return run_maxscript(live, script)


def current_library_contains(live: MaxClient, name: str) -> bool:
    safe = name.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'(local found = false; for m in currentMaterialLibrary where '
        f'm != undefined and m.name == "{safe}" do found = true; found)'
    )
    return run_maxscript(live, script).lower() == "true"


def duplicate_assignments_unchanged(live: MaxClient) -> bool:
    script = r"""(
        local matches = for n in objects where n.name == "MCP_PortParity_Duplicate" collect n;
        local clean = matches.count == 2;
        for n in matches where n.material != undefined do clean = false;
        clean
    )"""
    return run_maxscript(live, script).lower() == "true"


def validate_capture_files(entries: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for entry in entries:
        path = Path(entry["image_path"])
        if not path.is_file():
            errors.append(f"missing: {path}")
        elif path.stat().st_size <= 128:
            errors.append(f"too small: {path} ({path.stat().st_size} bytes)")
    return errors


def run() -> Report:
    report = Report()
    live = MaxClient(transport="pipe")

    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True)

    # Capabilities: exact public-wrapper parity.
    try:
        native_caps = parse_json(
            call_wrapper(
                capabilities,
                capabilities.get_plugin_capabilities,
                live,
                native=True,
            )
        )
        script_caps = parse_json(
            call_wrapper(
                capabilities,
                capabilities.get_plugin_capabilities,
                live,
                native=False,
            )
        )
        cap_diffs = deep_diff(script_caps, native_caps)
        report.check(
            "plugin capabilities match MAXScript exactly",
            not cap_diffs,
            details=cap_diffs[:40],
        )
    except Exception as exc:
        report.check(
            "plugin capabilities match MAXScript exactly",
            False,
            details=f"{type(exc).__name__}: {exc}",
        )

    setup_fixture(live)

    # Material-library reads: every source and empty-slot mode.
    for source in ("current", "medit", "combined", "all"):
        for include_empty in (False, True):
            native_value = parse_json(
                call_wrapper(
                    materials,
                    materials.get_material_library,
                    live,
                    native=True,
                    source=source,
                    include_empty_slots=include_empty,
                )
            )
            script_value = parse_json(
                call_wrapper(
                    materials,
                    materials.get_material_library,
                    live,
                    native=False,
                    source=source,
                    include_empty_slots=include_empty,
                )
            )
            diffs = deep_diff(script_value, native_value)
            report.check(
                f"material library {source} empty={include_empty}",
                not diffs,
                details=diffs[:40],
            )

    # Backup: public wrapper, all three sources, plus raw standalone-chat args.
    baseline_backup_dir = OUT_ROOT / "backup_baseline"
    native_backup_dir = OUT_ROOT / "backup_native"
    baseline_backup = parse_json(
        call_wrapper(
            materials,
            materials.backup_material_library,
            live,
            native=False,
            source="all",
            backup_dir=str(baseline_backup_dir),
            prefix="baseline_",
        )
    )
    native_backup = parse_json(
        call_wrapper(
            materials,
            materials.backup_material_library,
            live,
            native=True,
            source="all",
            backup_dir=str(native_backup_dir),
            prefix="native_",
        )
    )
    backup_diffs = deep_diff(
        logical_backup(baseline_backup),
        logical_backup(native_backup),
    )
    report.check(
        "material backup logical result matches MAXScript",
        not backup_diffs,
        details=backup_diffs,
    )

    baseline_saved = {
        item["source"]: item
        for item in baseline_backup.get("saved", [])
        if item.get("saved")
    }
    native_saved = {
        item["source"]: item
        for item in native_backup.get("saved", [])
        if item.get("saved")
    }
    for source in ("current", "medit", "combined"):
        left = baseline_saved.get(source)
        right = native_saved.get(source)
        if not left or not right:
            report.check(
                f"material backup reload {source}",
                False,
                details={"baseline": left, "native": right},
            )
            continue
        left_summary = inspect_saved_library(live, left["path"])
        right_summary = inspect_saved_library(live, right["path"])
        report.check(
            f"material backup reload {source}",
            left_summary == right_summary,
            details=(
                None
                if left_summary == right_summary
                else {"baseline": left_summary, "native": right_summary}
            ),
        )

    direct_backup_dir = OUT_ROOT / "backup_direct_native"
    direct_response = live.send_command(
        json.dumps(
            {
                "source": "all",
                "backup_dir": str(direct_backup_dir),
                "prefix": "direct_",
            }
        ),
        cmd_type="native:backup_material_library",
        timeout=45.0,
    )
    direct_backup = parse_json(direct_response.get("result", "{}"))
    direct_files = [
        Path(item["path"]).is_file()
        for item in direct_backup.get("saved", [])
        if item.get("saved")
    ]
    report.check(
        "standalone native backup accepts public schema",
        len(direct_files) == 3 and all(direct_files),
        details=direct_backup if not (len(direct_files) == 3 and all(direct_files)) else None,
    )

    # Shell wrapper: assigned render/export, missing export, swapped slots.
    baseline_shell = parse_json(
        call_wrapper(
            material_ops,
            material_ops.create_shell_material,
            live,
            native=False,
            shell_name="MCP_PortParity_Shell_Baseline",
            render_material="MCP_PortParity_Render",
            export_material="MCP_PortParity_Export",
            assign_to=["MCP_PortParity_ShellBaseline"],
            render_slot=1,
            viewport_slot=0,
        )
    )
    native_shell = parse_json(
        call_wrapper(
            material_ops,
            material_ops.create_shell_material,
            live,
            native=True,
            shell_name="MCP_PortParity_Shell_Native",
            render_material="MCP_PortParity_Render",
            export_material="MCP_PortParity_Export",
            assign_to=["MCP_PortParity_ShellNative"],
            render_slot=1,
            viewport_slot=0,
        )
    )
    baseline_state = shell_state(live, "MCP_PortParity_ShellBaseline")
    native_state = shell_state(live, "MCP_PortParity_ShellNative")
    report.check(
        "Shell material assigned state and swapped slots match",
        baseline_state == native_state,
        details=(
            None
            if baseline_state == native_state
            else {"baseline": baseline_state, "native": native_state}
        ),
    )
    report.check(
        "Shell native result preserves original success fields",
        native_shell.get("workflow") == baseline_shell.get("workflow") == "shell_wrap"
        and native_shell.get("render_material") == baseline_shell.get("render_material")
        and native_shell.get("export_material") == baseline_shell.get("export_material")
        and native_shell.get("assigned_count") == baseline_shell.get("assigned_count") == 1,
        details={"baseline": baseline_shell, "native": native_shell},
    )

    baseline_missing = parse_json(
        call_wrapper(
            material_ops,
            material_ops.create_shell_material,
            live,
            native=False,
            shell_name="MCP_PortParity_MissingExport_Baseline",
            render_material="MCP_PortParity_Render",
            export_material="MCP_PortParity_DoesNotExist",
            assign_to=["MCP_PortParity_MissingBaseline"],
        )
    )
    native_missing = parse_json(
        call_wrapper(
            material_ops,
            material_ops.create_shell_material,
            live,
            native=True,
            shell_name="MCP_PortParity_MissingExport_Native",
            render_material="MCP_PortParity_Render",
            export_material="MCP_PortParity_DoesNotExist",
            assign_to=["MCP_PortParity_MissingNative"],
        )
    )
    baseline_missing_state = shell_state(live, "MCP_PortParity_MissingBaseline")
    native_missing_state = shell_state(live, "MCP_PortParity_MissingNative")
    report.check(
        "Shell missing optional export remains successful",
        baseline_missing.get("status") == native_missing.get("status") == "success"
        and baseline_missing_state == native_missing_state,
        details={
            "baseline_result": baseline_missing,
            "native_result": native_missing,
            "baseline_state": baseline_missing_state,
            "native_state": native_missing_state,
        },
    )

    unassigned_name = "MCP_PortParity_UnassignedNative"
    unassigned = parse_json(
        call_wrapper(
            material_ops,
            material_ops.create_shell_material,
            live,
            native=True,
            shell_name=unassigned_name,
            render_material="MCP_PortParity_Render",
        )
    )
    report.check(
        "unassigned native Shell remains reachable",
        unassigned.get("status") == "success"
        and current_library_contains(live, unassigned_name),
        details=unassigned,
    )

    duplicate_error = ""
    try:
        call_wrapper(
            material_ops,
            material_ops.create_shell_material,
            live,
            native=True,
            shell_name="MCP_PortParity_AmbiguousShell",
            render_material="MCP_PortParity_Render",
            assign_to=["MCP_PortParity_Duplicate"],
        )
    except MaxBridgeError as exc:
        duplicate_error = exc.bridge_message
    report.check(
        "ambiguous native Shell assignment is atomic",
        "AMBIGUOUS" in duplicate_error
        and duplicate_assignments_unchanged(live)
        and not current_library_contains(live, "MCP_PortParity_AmbiguousShell"),
        details=duplicate_error,
        expected_difference=True,
    )

    native_missing_render = ""
    script_missing_render = ""
    for use_native in (True, False):
        try:
            missing_result = parse_json(call_wrapper(
                material_ops,
                material_ops.create_shell_material,
                live,
                native=use_native,
                shell_name=(
                    "MCP_PortParity_MissingRenderNative"
                    if use_native
                    else "MCP_PortParity_MissingRenderScript"
                ),
                render_material="MCP_PortParity_NoSuchMaterial",
            ))
            if not use_native and isinstance(missing_result, dict):
                script_missing_render = str(
                    missing_result.get("error", "")
                )
        except MaxBridgeError as exc:
            if use_native:
                native_missing_render = exc.bridge_message
            else:
                script_missing_render = exc.bridge_message
    report.check(
        "Shell missing render errors match",
        "Render material not found" in native_missing_render
        and "Render material not found" in script_missing_render,
        details={
            "baseline": script_missing_render,
            "native": native_missing_render,
        },
    )

    # Isolated capture: grouping/files plus native state restoration.
    reset_test_view_and_selection(live)
    native_before = fixture_state(live)
    native_capture_root = OUT_ROOT / "capture_native"
    with patch.object(identify, "COMMS_DIR", str(native_capture_root)):
        native_capture = parse_json(
            call_wrapper(
                identify,
                identify.isolate_and_capture_selected,
                live,
                native=True,
            )
        )
    native_after = fixture_state(live)
    native_capture_errors = validate_capture_files(native_capture)
    native_paths = [entry["image_path"].casefold() for entry in native_capture]
    report.check(
        "native isolated capture restores selection visibility and viewport",
        native_before == native_after,
        details=(
            None
            if native_before == native_after
            else {"before": native_before, "after": native_after}
        ),
    )
    report.check(
        "native isolated capture writes one valid unique image per group",
        len(native_capture) == 3
        and len(native_paths) == len(set(native_paths))
        and not native_capture_errors,
        details={
            "entries": native_capture,
            "file_errors": native_capture_errors,
        },
    )

    reset_test_view_and_selection(live)
    baseline_capture_root = OUT_ROOT / "capture_baseline"
    with patch.object(identify, "COMMS_DIR", str(baseline_capture_root)):
        baseline_capture = parse_json(
            call_wrapper(
                identify,
                identify.isolate_and_capture_selected,
                live,
                native=False,
            )
        )
    baseline_capture_errors = validate_capture_files(baseline_capture)
    baseline_groups = sorted(
        (entry["name"], sorted(entry["instances"]))
        for entry in baseline_capture
    )
    native_groups = sorted(
        (entry["name"], sorted(entry["instances"]))
        for entry in native_capture
    )
    report.check(
        "isolated capture grouping matches MAXScript",
        native_groups == baseline_groups,
        details=(
            None
            if native_groups == baseline_groups
            else {"baseline": baseline_groups, "native": native_groups}
        ),
    )
    report.check(
        "native capture closes MAXScript filename-collision gap",
        len({entry["image_path"].casefold() for entry in baseline_capture})
        < len(baseline_capture)
        and len(set(native_paths)) == len(native_capture),
        details={
            "baseline_paths": [
                entry["image_path"] for entry in baseline_capture
            ],
            "native_paths": [entry["image_path"] for entry in native_capture],
            "baseline_file_errors": baseline_capture_errors,
        },
        expected_difference=True,
    )

    return report


def main() -> int:
    try:
        report = run()
        payload = report.payload()
    except Exception as exc:
        payload = {
            "passed": False,
            "infrastructure_error": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
