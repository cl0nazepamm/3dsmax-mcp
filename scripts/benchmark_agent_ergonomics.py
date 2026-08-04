"""Benchmark canonical MCP agent tasks against a live 3ds Max bridge.

Measures bridge round trips and approximate payload tokens. This script does
not render. It creates a temporary object and optionally cleans it up.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maxmcp.max_client import MaxClient  # noqa: E402


def approx_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(1, len(text) // 4)


@dataclass
class BenchResult:
    name: str
    cmd_type: str
    ok: bool
    elapsed_ms: float
    request_tokens: int
    response_tokens: int
    summary: str = ""
    error: str = ""


@dataclass
class Bench:
    client: MaxClient
    results: list[BenchResult] = field(default_factory=list)
    round_trips: int = 0

    def call(self, name: str, cmd_type: str, payload: dict[str, Any] | str = "") -> dict[str, Any]:
        command = json.dumps(payload) if isinstance(payload, dict) else payload
        started = time.perf_counter()
        ok = False
        error = ""
        response: dict[str, Any] = {}
        try:
            response = self.client.send_command(command, cmd_type=cmd_type)
            ok = bool(response.get("success", True))
        except Exception as exc:  # benchmark should continue and report failure
            error = str(exc)
            response = {"error": error}
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.round_trips += 1
        result_text = response.get("result") or response.get("error") or ""
        self.results.append(
            BenchResult(
                name=name,
                cmd_type=cmd_type,
                ok=ok,
                elapsed_ms=elapsed_ms,
                request_tokens=approx_tokens(command),
                response_tokens=approx_tokens(response),
                summary=str(result_text)[:160],
                error=error,
            )
        )
        return response


def parse_result(response: dict[str, Any]) -> dict[str, Any]:
    raw = response.get("result", "")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        return json.loads(raw)
    return {}


def run_benchmark(cleanup: bool) -> dict[str, Any]:
    bench = Bench(MaxClient())
    stamp = int(time.time())
    name = f"MCP_Bench_{stamp}"

    overview = bench.call("overview", "native:scene_snapshot", {})
    create = parse_result(bench.call(
        "create_box",
        "native:create_object",
        {"type": "box", "name": name, "length": 20, "width": 20, "height": 20, "pos_mode": "ground"},
    ))
    handle = create.get("handle")

    target: dict[str, Any] = {"handle": handle} if handle else {"name": name}
    bench.call("transform", "native:transform_object", {**target, "move": [5, 0, 0]})
    bench.call("add_modifier", "native:add_modifier", {**target, "modifier": "Bend"})
    bench.call(
        "assign_material",
        "native:assign_material",
        {
            "handles": [handle] if handle else [],
            "names": [] if handle else [name],
            "material_class": "Standard",
            "material_name": f"{name}_Mat",
        },
    )
    baseline = parse_result(bench.call("delta_baseline", "native:scene_delta", {"capture": True}))
    seq = baseline.get("currentSeq", 0)
    bench.call("delta_unchanged_since", "native:scene_delta", {"unchanged_since": seq})

    if cleanup:
        bench.call("cleanup_delete", "native:delete_objects", {"handles": [handle] if handle else [], "names": [] if handle else [name]})

    _ = overview  # keep task sequence explicit without printing scene data
    total_request = sum(r.request_tokens for r in bench.results)
    total_response = sum(r.response_tokens for r in bench.results)
    return {
        "round_trips": bench.round_trips,
        "approx_tokens": {
            "request": total_request,
            "response": total_response,
            "total": total_request + total_response,
        },
        "steps": [r.__dict__ for r in bench.results],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="Delete the temporary benchmark object at the end.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args()

    report = run_benchmark(cleanup=args.cleanup)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"round_trips: {report['round_trips']}")
    print(f"approx_tokens: {report['approx_tokens']['total']} total")
    for step in report["steps"]:
        status = "ok" if step["ok"] else "fail"
        print(f"{status:4} {step['name']:22} {step['elapsed_ms']:8.1f} ms {step['cmd_type']}")
    return 0 if all(step["ok"] for step in report["steps"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
