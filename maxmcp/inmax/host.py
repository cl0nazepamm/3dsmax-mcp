"""The maxmcp tool layer inside 3ds Max's own Python.

Tool modules import `mcp` and `client` from maxmcp.server. Inside Max that module is a
stand-in: `mcp` is the ToolRegistry, which collects the @mcp.tool() functions, and
`client` is an InProcessClient, so each tool runs its usual Python code and reaches the
native handlers through the bridge's in-process entry point.
"""

from __future__ import annotations

import sys
import threading
import types

from ..registry import ToolRegistry
from .client import InProcessClient

# Modules whose tools only make sense to an MCP client outside Max.
_SKIPPED_MODULES = ("routing", "python_api")

_registry: ToolRegistry | None = None
_activate_lock = threading.Lock()


def activate() -> ToolRegistry:
    """Install the stand-in server module, so mx calls this process's bridge."""
    global _registry
    with _activate_lock:
        if _registry is not None:
            return _registry
        existing = sys.modules.get("maxmcp.server")
        if existing is not None and not getattr(existing, "_INMAX", False):
            raise RuntimeError("maxmcp.server is already loaded; maxmcp.inmax runs only inside 3ds Max")
        client = InProcessClient()
        registry = ToolRegistry(client, skip=_SKIPPED_MODULES)
        server = types.ModuleType("maxmcp.server")
        server._INMAX = True  # type: ignore[attr-defined]
        server.mcp = registry  # type: ignore[attr-defined]
        server.client = client  # type: ignore[attr-defined]
        server.registry = registry  # type: ignore[attr-defined]
        sys.modules["maxmcp.server"] = server
        _registry = registry
        return registry
