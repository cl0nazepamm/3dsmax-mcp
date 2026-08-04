"""Small live smoke test for the 3ds Max MCP bridge."""

from __future__ import annotations

import json
import sys

from maxmcp.tools.bridge import get_bridge_status
from maxmcp.tools.scene_manage import manage_scene
from maxmcp.tools.session_context import get_session_context


def main() -> int:
    checks = [
        ("get_bridge_status", lambda: json.loads(get_bridge_status())),
        ("get_session_context", lambda: json.loads(get_session_context())),
        ("manage_scene hold", lambda: manage_scene("hold")),
        ("manage_scene fetch", lambda: manage_scene("fetch")),
    ]

    failed = False
    held = False
    for name, fn in checks:
        try:
            result = fn()
            if name == "manage_scene hold":
                held = True
            print(f"[ok] {name}")
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except ValueError:
                    pass
            print(json.dumps(result, indent=2) if not isinstance(result, str) else result)
        except Exception as exc:  # pragma: no cover - live smoke path
            failed = True
            print(f"[fail] {name}: {exc}", file=sys.stderr)
            if held and name != "manage_scene fetch":
                try:
                    print("[cleanup] manage_scene fetch")
                    print(manage_scene("fetch"))
                except Exception as cleanup_exc:
                    print(f"[cleanup fail] manage_scene fetch: {cleanup_exc}", file=sys.stderr)
                finally:
                    held = False

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
