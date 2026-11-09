"""Load the newest _mx build into a running 3ds Max (development only).

Each load copies the module to a new folder under local/mx_live and imports it
from there, so the build output is never locked and a rebuilt module (built
with MCP_MX_DEV_RELOAD) loads beside the copies already in this Max session.

    import mxdev; mx = mxdev.load()

run_python scripts get the same module as `sdk` (maxmcp/inmax/sdk.py); both loaders
share what is loaded.
"""

import importlib.util
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(max_version=2027):
    build = os.path.join(ROOT, "native", "build%d" % max_version, "Release", "python")
    tag = "cp%d%d" % sys.version_info[:2]
    builds = [name for name in os.listdir(build) if name.startswith("_mx.") and tag in name]
    if not builds:
        raise ImportError("no _mx build for Python %d.%d in %s" % (sys.version_info[:2] + (build,)))
    current = sys.modules.get("_mx")
    source = os.path.join(build, builds[0])
    # run_python's own loader (maxmcp/inmax/sdk.py) marks its module _mcp_loaded_mtime.
    loaded = max(getattr(current, "_loaded_mtime", 0), getattr(current, "_mcp_loaded_mtime", 0))
    if current is not None and os.path.getmtime(source) <= loaded:
        return current
    target = os.path.join(ROOT, "local", "mx_live", str(int(time.time() * 1000)))
    os.makedirs(target)
    spec = importlib.util.spec_from_file_location("_mx", shutil.copy2(source, target))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._loaded_mtime = module._mcp_loaded_mtime = os.path.getmtime(source)
    sys.modules["_mx"] = module
    return module
