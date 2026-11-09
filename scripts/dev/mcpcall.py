"""Run one 3dsmax-mcp tool from the current source, in a fresh process.

The MCP server keeps the Python code it started with. This script imports
maxmcp anew on every run and calls the tool through the server's own registry
(the argument validation and envelope wrapper mx.tools uses), over the same
named pipe, so a Python tool change is testable without restarting the
server. --pid or --pipe pins one 3ds Max, so parallel agents can each drive
their own instance. Keep mutating calls to one Max sequential.

    python scripts/dev/mcpcall.py query_scene '{"action": "overview"}'
    python scripts/dev/mcpcall.py --pid 2112 reload_native_core
    python scripts/dev/mcpcall.py create_object @local/args.json
    python scripts/dev/mcpcall.py create_object - < args.json
    python scripts/dev/mcpcall.py --list [substring]
    python scripts/dev/mcpcall.py --manual create_object

Prints the ToolEnvelope as JSON and a timing line on stderr. Exit status: 0
when the envelope is ok, 1 when it is not, 2 for usage errors.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_arguments(text: str | None) -> dict[str, Any]:
    """Tool arguments from JSON text, @file or - (stdin); none means {}."""
    if text is None:
        return {}
    if text == "-":
        text = sys.stdin.read()
    elif text.startswith("@"):
        text = Path(text[1:]).read_text(encoding="utf-8-sig")
    value = json.loads(text) if text.strip() else {}
    if not isinstance(value, dict):
        raise ValueError("arguments must be a JSON object")
    return value


def load_registry() -> Any:
    """The server's tool registry, imported from this checkout's source."""
    sys.path.insert(0, str(ROOT))
    from maxmcp import server

    logging.getLogger().setLevel(logging.WARNING)
    return server.registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tool", nargs="?", help="tool name, or a name filter with --list")
    parser.add_argument("arguments", nargs="?", help="JSON object, @file or - for stdin (default {})")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--pid", type=int, help="3ds Max process to target")
    target.add_argument("--pipe", help="named pipe to target")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="list tool names and their modules")
    mode.add_argument("--manual", action="store_true", help="print the tool's signature and manual")
    parser.add_argument("--compact", action="store_true", help="print the envelope on one line")
    args = parser.parse_args(argv)

    if args.pid is not None:
        if args.pid <= 0:
            parser.error("--pid must be a positive process ID")
        os.environ.pop("MCP_MAX_PIPE", None)
        os.environ["MCP_MAX_PID"] = str(args.pid)
    elif args.pipe:
        os.environ["MCP_MAX_PIPE"] = args.pipe
    if not args.list and not args.tool:
        parser.error("a tool name is required")
    try:
        arguments = load_arguments(args.arguments)
    except (OSError, ValueError) as exc:
        parser.error(f"bad arguments: {exc}")

    sys.stdout.reconfigure(encoding="utf-8")
    registry = load_registry()
    if args.list:
        needle = (args.tool or "").lower()
        for name, module in sorted(registry.index.items()):
            if needle in name.lower():
                print(f"{name}  ({module})")
        return 0
    if args.manual:
        described = registry.describe(args.tool)
        if described is None:
            parser.error(f"unknown tool {args.tool!r}; see --list")
        print(f"{args.tool}{described[0]}\n\n{described[1]}")
        return 0

    started = time.perf_counter()
    envelope = registry.call(args.tool, arguments)
    elapsed = (time.perf_counter() - started) * 1000
    print(json.dumps(envelope, ensure_ascii=False, indent=None if args.compact else 2, default=str))
    ok = envelope.get("ok") is True
    print(f"mcpcall: {args.tool} {'ok' if ok else 'failed'} in {elapsed:.0f} ms", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
