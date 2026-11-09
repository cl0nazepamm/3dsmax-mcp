"""_mx, the scene object model bound from the 3ds Max SDK, for scripts that run inside Max.

A release bridge ships the _mx built with it beside the plugin:
    Contents/bin/mcp_bridge_2027.gup
    Contents/bin/mx_2027/_mx.cp313-win_amd64.pyd
A development bridge that loads its core from a build tree (mcp_core.path beside the
.gup) loads that tree's newest _mx instead. Each build is copied before it loads, so
the build output stays unlocked, and a rebuilt module (MCP_MX_DEV_RELOAD gives every
build its own binding types) loads beside the copies already in this Max session.
MCP_MX_PATH names a module file to load instead of either.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

from . import binding

_TAG = f"cp{sys.version_info[0]}{sys.version_info[1]}"


def _newest(folder: Path) -> Path | None:
    builds = [path for path in folder.glob("_mx.*.pyd") if _TAG in path.name] if folder.is_dir() else []
    return max(builds, key=lambda path: path.stat().st_mtime) if builds else None


def _dev_build(bridge: Path) -> Path | None:
    """The newest _mx in the build tree of a hot-core development bridge."""
    locator = bridge.parent / "mcp_core.path"
    if not locator.is_file():
        return None
    core = Path(locator.read_text(encoding="utf-8").strip().strip('"'))
    # <build>/Release/core/mcp_core.dll -> <build>/Release/python
    return _newest(core.parent.parent / "python")


def locate() -> tuple[Path | None, bool]:
    """(module file, whether it is a development build) for the bridge loaded in this Max."""
    override = os.environ.get("MCP_MX_PATH")
    if override:
        return Path(override), True
    bridge = binding.bridge_path()
    if bridge is None:
        return None, False
    dev = _dev_build(bridge)
    if dev is not None:
        return dev, True
    year = re.search(r"(\d{4})", bridge.stem)
    folder = bridge.parent / (f"mx_{year.group(1)}" if year else "mx")
    return _newest(folder), False


def _load_file(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_mx", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load _mx from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load() -> ModuleType | None:
    """The _mx module matching this Max's bridge, loading it on first use; None when there is none."""
    current = sys.modules.get("_mx")
    try:
        path, dev = locate()
    except OSError:
        path, dev = None, False
    if path is None or not path.is_file():
        return current
    stamp = path.stat().st_mtime
    # native/python/mxdev.py marks what it loaded with _loaded_mtime; the same build is
    # never loaded twice (its binding types would clash).
    loaded = max(getattr(current, "_mcp_loaded_mtime", 0), getattr(current, "_loaded_mtime", 0))
    if current is not None and (not dev or stamp <= loaded):
        return current
    if dev:
        live = Path(tempfile.gettempdir()) / "3dsmax-mcp" / "mx_live" / str(int(time.time() * 1000))
        live.mkdir(parents=True, exist_ok=True)
        path = Path(shutil.copy2(path, live))
    module = _load_file(path)
    module._mcp_loaded_mtime = module._loaded_mtime = stamp  # type: ignore[attr-defined]
    sys.modules["_mx"] = module
    return module
