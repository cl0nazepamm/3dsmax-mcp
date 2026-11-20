"""mx: a Python API for 3ds Max over the guarded 3dsmax-mcp tools.

    import maxmcp.mx as mx
    block = mx.box("Block", width=100, length=60, height=40, pos=(900, 0, 0))
    print(block.bbox)

Hand-made helpers live in the domain modules (objects, modeling, shading, view) and
are re-exported here. Every tool is also callable as mx.tools.<name>(**arguments).
mx.help() lists the tools by group, mx.find_tools("words") searches them, mx.help(name)
returns one tool's manual and mx.maxscript(code) runs raw MAXScript.

Inside 3ds Max, run_python also sets mx.sdk (and `sdk` in the script): the _mx scene
object model bound from the 3ds Max SDK. mx.help("sdk") documents it. There the helpers
return sdk nodes; outside 3ds Max they return mx.Node, which offers the same core members
under the same names (mx/_node.py).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from ._node import Box, Node, Point3
from ._runtime import (MaxToolError, call, find_tools, help, maxscript, ms_value, node_handle, node_name,
                       set_backend, show, tools)

_DOMAINS = ("objects", "modeling", "shading", "view")
_CORE = ["Box", "MaxToolError", "Node", "Point3", "call", "find_tools", "help", "maxscript", "ms_value",
         "node_handle", "node_name", "set_backend", "show", "tools"]


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
