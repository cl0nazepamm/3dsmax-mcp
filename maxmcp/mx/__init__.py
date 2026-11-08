"""mx: a Python API for 3ds Max over the guarded 3dsmax-mcp tools.

    import maxmcp.mx as mx
    block = mx.box("Block", width=100, length=60, height=40, pos=(900, 0, 0))
    print(block.bbox)

Hand-made helpers live in the domain modules (objects, modeling, shading, view) and
are re-exported here. Every tool is also callable as mx.tools.<name>(**arguments);
mx.help(name) returns its manual and mx.maxscript(code) runs raw MAXScript.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from ._runtime import MaxToolError, Node, call, help, maxscript, ms_value, node_name, set_backend, tools

_DOMAINS = ("objects", "modeling", "shading", "view")
_CORE = ["MaxToolError", "Node", "call", "help", "maxscript", "ms_value", "node_name", "set_backend", "tools"]


def __getattr__(name: str) -> Any:
    # Domain helpers load on first use, so one domain module never blocks another.
    for domain in _DOMAINS:
        module = import_module(f".{domain}", __name__)
        if name in getattr(module, "__all__", ()):
            return getattr(module, name)
    raise AttributeError(f"module 'mx' has no attribute {name!r}")


def __dir__() -> list[str]:
    names = list(_CORE)
    for domain in _DOMAINS:
        names += list(getattr(import_module(f".{domain}", __name__), "__all__", ()))
    return sorted(names)
