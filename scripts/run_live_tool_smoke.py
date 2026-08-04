"""Live integration smoke for MCP tools against a running 3ds Max instance.

This is NOT pytest — every call goes through the real named-pipe/TCP bridge.

Usage (Max must be open with MCP Bridge loaded):

    python scripts/run_live_tool_smoke.py --tier read
    python scripts/run_live_tool_smoke.py --tier native
    python scripts/run_live_tool_smoke.py --tier full
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient  # noqa: E402
from maxmcp.tool_response import _json_or_raw  # noqa: E402

NATIVE_JSON = ROOT / "native" / "generated" / "tool_smoke_cases.json"
FULL_JSON = ROOT / "native" / "generated" / "full_tool_smoke_cases.json"

TIER_LIMIT = {
    "read": 0,
    "fixture": 1,
    "native": 2,
    "mutate": 2,
    "full": 2,
}

FLAG_SKIP_DEFAULT = 1
FLAG_EXPECT_ERROR = 2


def _looks_like_error(result: Any) -> bool:
    if result is None:
        return True
    if isinstance(result, dict):
        if result.get("ok") is False:
            return True
        err = result.get("error")
        if err:
            return True
    if isinstance(result, str):
        lower = result.strip().lower()
        if not lower:
            return True
        if lower.startswith("error"):
            return True
        if "blocked by safe mode" in lower:
            return True
        if " not found:" in lower:
            return True
        if lower.startswith("failed"):
            return True
    return False


def _load_cases(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing — run: python scripts/gen_tool_smoke.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


SMOKE_TARGET = "MCP_SmokeTarget"
SMOKE_SPAWN = "MCP_SmokeSpawn"


def _ensure_smoke_fixture(client: MaxClient) -> None:
    payload = {
        "type": "box",
        "name": SMOKE_TARGET,
        "length": 10,
        "width": 10,
        "height": 10,
        "pos": [9999, 9999, 0],
        "pos_mode": "ground",
    }
    try:
        _invoke_bridge_tool(
            client,
            {"cmdType": "native:create_object", "input": payload},
        )
        _invoke_bridge_tool(
            client,
            {
                "cmdType": "native:assign_material",
                "input": {
                    "names": [SMOKE_TARGET],
                    "material_class": "PhysicalMaterial",
                    "material_name": "MCP_SmokeMtl",
                },
            },
        )
    except RuntimeError:
        pass


def _cleanup_smoke_fixture(client: MaxClient) -> None:
    try:
        _invoke_bridge_tool(
            client,
            {
                "cmdType": "native:delete_objects",
                "input": {"names": [SMOKE_TARGET, SMOKE_SPAWN, f"{SMOKE_TARGET}001"]},
            },
        )
    except RuntimeError:
        pass


def _invoke_bridge_tool(client: MaxClient, case: dict) -> Any:
    """Call a smoke case through MaxClient — no MCP Python server import needed."""
    cmd_type = case.get("cmdType", "maxscript")
    tool_input = case.get("input") or {}
    if cmd_type == "maxscript":
        command = tool_input.get("code", "")
    else:
        command = json.dumps(tool_input, ensure_ascii=False)
    response = client.send_command(command, cmd_type=cmd_type)
    if not response.get("success", True) and response.get("error"):
        raise RuntimeError(str(response["error"]))
    raw = response.get("result", "")
    return _json_or_raw(raw) if isinstance(raw, str) else raw


def _run_native_cases_individually(
    client: MaxClient,
    tier: str,
    include_skipped: bool,
    dry_run: bool,
) -> dict:
    """Run each smoke case as its own bridge call — safer than one monolithic burst."""
    tier_limit = TIER_LIMIT.get(tier, 0)
    cases = _load_cases(NATIVE_JSON)
    results: list[dict] = []

    if tier_limit >= 1 and not dry_run:
        _ensure_smoke_fixture(client)

    for case in cases:
        if case.get("tier", 0) > tier_limit:
            results.append({
                "tool": case["tool"],
                "status": "skipped",
                "reason": "tier",
            })
            continue
        if (case.get("flags", 0) & FLAG_SKIP_DEFAULT) and not include_skipped:
            results.append({
                "tool": case["tool"],
                "status": "skipped",
                "reason": "skip_default",
            })
            continue
        if case["tool"] in {"invoke_tool", "run_tool_smoke"}:
            results.append({
                "tool": case["tool"],
                "status": "skipped",
                "reason": "meta_tool",
            })
            continue

        row = {
            "tool": case["tool"],
            "tier": case.get("tier", 0),
            "input": case.get("input", {}),
        }
        if dry_run:
            row["status"] = "dry_run"
            results.append(row)
            continue

        started = time.perf_counter()
        try:
            parsed = _invoke_bridge_tool(client, case)
            row["result"] = parsed
            err = None
        except Exception as exc:
            parsed = None
            err = str(exc)
            row["error"] = err

        row["elapsedMs"] = round((time.perf_counter() - started) * 1000, 2)
        expect_error = bool(case.get("flags", 0) & FLAG_EXPECT_ERROR)
        got_error = err is not None or _looks_like_error(parsed)
        row["status"] = "passed" if (expect_error and got_error) or (not expect_error and not got_error) else "failed"
        results.append(row)

    if tier_limit >= 1 and not dry_run:
        _cleanup_smoke_fixture(client)

    return _summarize(results)


def _run_native_smoke(client: MaxClient, tier: str, include_skipped: bool, dry_run: bool) -> dict:
    payload = {
        "tier": "all" if tier in {"native", "full", "mutate"} else tier,
        "includeSkipped": include_skipped,
        "dryRun": dry_run,
    }
    response = client.send_command(
        json.dumps(payload),
        cmd_type="native:tool_smoke",
        timeout=600.0,
    )
    if not response.get("success", True) and response.get("error"):
        raise RuntimeError(response["error"])
    raw = response.get("result", "{}")
    return _json_or_raw(raw) if isinstance(raw, str) else raw


def _resolve_python_tool(module_name: str, tool_name: str) -> Callable[..., str]:
    mod = importlib.import_module(f"maxmcp.tools.{module_name}")
    fn = getattr(mod, tool_name, None)
    if fn is None or not callable(fn):
        raise AttributeError(f"maxmcp.tools.{module_name}.{tool_name} not found")
    return fn


def _run_python_case(case: dict, tier_limit: int, include_skipped: bool, dry_run: bool) -> dict:
    row = {
        "tool": case["tool"],
        "cmdType": case.get("cmdType", "python-only"),
        "tier": case.get("tier", 0),
        "flags": case.get("flags", 0),
    }

    if case.get("tier", 0) > tier_limit:
        row["status"] = "skipped"
        row["reason"] = "tier"
        return row

    if (case.get("flags", 0) & FLAG_SKIP_DEFAULT) and not include_skipped:
        row["status"] = "skipped"
        row["reason"] = "skip_default"
        return row

    row["input"] = case.get("input", {})
    if dry_run:
        row["status"] = "dry_run"
        return row

    started = time.perf_counter()
    try:
        fn = _resolve_python_tool(case["module"], case["tool"])
        raw = fn(**case.get("input", {}))
        parsed = _json_or_raw(raw)
        row["result"] = parsed
        err = None
    except Exception as exc:
        parsed = None
        err = str(exc)
        row["error"] = err

    row["elapsedMs"] = round((time.perf_counter() - started) * 1000, 2)
    expect_error = bool(case.get("flags", 0) & FLAG_EXPECT_ERROR)
    got_error = err is not None or _looks_like_error(parsed)
    row["status"] = "passed" if (expect_error and got_error) or (not expect_error and not got_error) else "failed"
    return row


def _summarize(results: list[dict]) -> dict:
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = sum(1 for r in results if r.get("status") == "failed")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    dry = sum(1 for r in results if r.get("status") == "dry_run")
    return {
        "total": len([r for r in results if r.get("status") not in {"skipped", "dry_run"}]),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "dryRun": dry,
        "results": results,
    }


def _run_full_smoke(include_skipped: bool, dry_run: bool) -> dict:
    client = MaxClient()
    native_report = _run_native_smoke(client, "native", include_skipped, dry_run)
    native_tools = {c["tool"] for c in _load_cases(NATIVE_JSON)}

    full_cases = _load_cases(FULL_JSON)
    extra_results: list[dict] = []
    for case in full_cases:
        if case["tool"] in native_tools:
            continue
        extra_results.append(_run_python_case(case, 2, include_skipped, dry_run))

    combined = {
        "native": native_report,
        "pythonOnly": _summarize(extra_results),
    }
    combined["passed"] = native_report.get("passed", 0) + combined["pythonOnly"]["passed"]
    combined["failed"] = native_report.get("failed", 0) + combined["pythonOnly"]["failed"]
    combined["skipped"] = native_report.get("skipped", 0) + combined["pythonOnly"]["skipped"]
    combined["dryRun"] = native_report.get("dryRun", 0) + combined["pythonOnly"]["dryRun"]
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description="Live MCP tool smoke against 3ds Max")
    parser.add_argument(
        "--tier",
        choices=["read", "fixture", "native", "full"],
        default="read",
        help="read=safe reads; fixture=+object-bound; native/all native cases; full=native+python-only",
    )
    parser.add_argument("--include-skipped", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Run all cases in one native:tool_smoke call (fast but can stress Max)",
    )
    args = parser.parse_args()

    try:
        if args.tier == "full":
            report = _run_full_smoke(args.include_skipped, args.dry_run)
        else:
            client = MaxClient()
            if args.batch:
                report = _run_native_smoke(client, args.tier, args.include_skipped, args.dry_run)
            else:
                report = _run_native_cases_individually(
                    client, args.tier, args.include_skipped, args.dry_run
                )
    except Exception as exc:
        print(f"[fail] live smoke: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2))
    failed = report.get("failed", 0)
    if isinstance(report.get("native"), dict):
        failed = max(failed, report["native"].get("failed", 0))
    if failed:
        print(f"[fail] {failed} tool(s) failed", file=sys.stderr)
        return 1

    print("[ok] live tool smoke complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
