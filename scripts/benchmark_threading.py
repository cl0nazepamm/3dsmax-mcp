"""A/B benchmark: same handler, direct (worker thread) vs forced main thread.

Uses _forceMainThread flag to run the exact same handler both ways.
Run with: uv run python scripts/benchmark_threading.py
"""

import json
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from maxmcp.max_client import MaxClient


def send_raw(client: MaxClient, cmd_type: str, command: str = "",
             force_main: bool = False, timeout: float = 10.0):
    """Send a command with optional _forceMainThread flag."""
    from uuid import uuid4
    request_id = str(uuid4())[:8]
    req = {
        "command": command,
        "type": cmd_type,
        "requestId": request_id,
        "protocolVersion": 2,
    }
    if force_main:
        req["_forceMainThread"] = True

    request = json.dumps(req, ensure_ascii=True)
    response_data = client._send_via_pipe(request, timeout)
    resp = json.loads(response_data)
    return resp


def bench_ab(client: MaxClient, label: str, cmd_type: str,
             command: str = "", rounds: int = 50):
    """Run the same handler in both modes and compare."""
    results = {}

    for mode_label, force in [("DIRECT", False), ("MAIN_THREAD", True)]:
        times = []
        server_times = []
        thread_mode = "?"

        for _ in range(rounds):
            t0 = time.perf_counter()
            resp = send_raw(client, cmd_type, command, force_main=force)
            t1 = time.perf_counter()
            times.append((t1 - t0) * 1000)

            meta = resp.get("meta", {})
            thread_mode = meta.get("threadMode", "?")
            server_ms = meta.get("durationMs", -1)
            if server_ms >= 0:
                server_times.append(server_ms)

        avg = sum(times) / len(times)
        mn = min(times)
        mx = max(times)
        p50 = sorted(times)[len(times) // 2]
        results[mode_label] = {"avg": avg, "min": mn, "max": mx, "p50": p50,
                               "mode": thread_mode}

    d = results["DIRECT"]
    m = results["MAIN_THREAD"]
    speedup = m["avg"] / d["avg"] if d["avg"] > 0 else float("inf")

    print(f"\n  {label}")
    print(f"    DIRECT:      avg={d['avg']:7.3f}ms  min={d['min']:7.3f}ms  "
          f"p50={d['p50']:7.3f}ms  max={d['max']:7.3f}ms  [{d['mode']}]")
    print(f"    MAIN_THREAD: avg={m['avg']:7.3f}ms  min={m['min']:7.3f}ms  "
          f"p50={m['p50']:7.3f}ms  max={m['max']:7.3f}ms  [{m['mode']}]")
    print(f"    --> {speedup:.1f}x faster with direct mode")


def main():
    client = MaxClient(transport="pipe")

    # Warm up
    for _ in range(5):
        send_raw(client, "ping")

    print("=" * 80)
    print("  A/B BENCHMARK: same handler, DIRECT vs FORCED MAIN THREAD")
    print("  Each test: 50 rounds per mode, same data, same code path")
    print("=" * 80)

    bench_ab(client, "scene_info (full scene traversal)",
             "native:scene_info")

    bench_ab(client, "selection (read current selection)",
             "native:selection")

    bench_ab(client, "scene_snapshot (scene overview + class counts)",
             "native:scene_snapshot")

    bench_ab(client, "get_materials (enumerate all materials)",
             "native:get_materials")

    bench_ab(client, "introspect_class Box (PB2 + FPInterface dump)",
             "native:introspect_class", json.dumps({"class_name": "Box"}))

    bench_ab(client, "learn_scene_patterns (full scene analysis)",
             "native:learn_scene_patterns")

    bench_ab(client, "list_plugin_classes (DllDir scan)",
             "native:list_plugin_classes",
             json.dumps({"superclass": "geometryobject", "limit": 10}))

    bench_ab(client, "discover_classes *Physical* (DLL search)",
             "native:discover_classes",
             json.dumps({"pattern": "*Physical*"}))

    bench_ab(client, "scene_delta (capture baseline)",
             "native:scene_delta", json.dumps({"capture": True}))

    print("\n" + "=" * 80)
    print("  Direct = runs on pipe worker thread (no main-thread roundtrip)")
    print("  Main   = PostMessage -> WndProc -> condition_variable notify")
    print("=" * 80)


if __name__ == "__main__":
    main()
