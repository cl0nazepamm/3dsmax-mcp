"""Write docs/mx-sdk-reference.md from the sdk (_mx) module loaded in a running 3ds Max.

The reference is read from the module itself (maxmcp/mx/_reference.py), the same text
mx.help("sdk...") gives a script. Needs a 3ds Max whose bridge loads _mx; run_python
runs the generator there from the current source:

    .venv/Scripts/python.exe scripts/codegen/gen_mx_reference.py [--pid 1234]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "mx-sdk-reference.md"

CODE = """
from maxmcp.mx import _reference
text = _reference.reference(sdk, "sdk (_mx)")
with open({out!r}, "w", encoding="utf-8", newline="\\n") as handle:
    handle.write(text)
result = {{"build": sdk.BUILD, "chars": len(text)}}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pid", help="3ds Max process id, when several run")
    args = parser.parse_args()
    if args.pid:
        os.environ.pop("MCP_MAX_PIPE", None)
        os.environ["MCP_MAX_PID"] = args.pid
    # run_python is the one tool scripts cannot call through mx, so it is called as a function.
    sys.path.insert(0, str(ROOT))
    from maxmcp import server  # noqa: F401 - loading the server registers the tools
    from maxmcp.tools.system.python_api import run_python

    reply = run_python(CODE.format(out=str(OUT)), where="max")
    print(json.dumps(reply, indent=2))
    if reply.get("status") == "error" or "result" not in reply:
        return 1
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
