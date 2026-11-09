"""Write docs/mx-sdk-reference.md from the sdk (_mx) module loaded in a running 3ds Max.

The reference is read from the module itself (maxmcp/mx/_reference.py), the same text
mx.help("sdk...") gives a script. Needs a 3ds Max whose bridge loads _mx; run_python
runs the generator there from the current source:

    .venv/Scripts/python.exe scripts/codegen/gen_mx_reference.py [--pid 1234]
"""

from __future__ import annotations

import argparse
import json
import subprocess
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
    command = [sys.executable, str(ROOT / "scripts" / "dev" / "mcpcall.py")]
    if args.pid:
        command += ["--pid", args.pid]
    command += ["run_python", json.dumps({"code": CODE.format(out=str(OUT)), "where": "max"})]
    done = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
    print(done.stdout)
    if done.returncode != 0:
        print(done.stderr, file=sys.stderr)
        return done.returncode
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
