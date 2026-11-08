"""The maxmcp tool layer inside 3ds Max's own Python, as the backend of mx.

Tool modules import `mcp` and `client` from maxmcp.server. Inside Max that module is a
stand-in: `mcp` collects the @mcp.tool() functions and `client` is an InProcessClient, so
each tool runs its usual Python code and reaches the native handlers through the
bridge's in-process entry point. A tool module is imported the first time one of its
tools is called. Arguments are validated against the tool's Python signature.
"""

from __future__ import annotations

import inspect
import sys
import threading
import types
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from .client import InProcessClient

# Modules whose tools only make sense to an MCP client outside Max.
_SKIPPED_MODULES = frozenset({"routing", "python_api"})


class _ToolCollector:
    """Stands in for FastMCP: @mcp.tool() registers, @mcp.resource() is ignored."""

    def __init__(self) -> None:
        self.functions: dict[str, Callable[..., Any]] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            self.functions[args[0].__name__] = args[0]
            return args[0]

        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.functions[kwargs.get("name") or fn.__name__] = fn
            return fn

        return decorate

    def resource(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return lambda fn: fn

    prompt = resource


class Host:
    """mx backend inside Max: call(tool, arguments) -> ToolEnvelope dict, plus describe/names."""

    def __init__(self, server: types.ModuleType, index: dict[str, str]) -> None:
        self.server = server
        self.index = index
        self._wrapped: dict[str, Callable[..., dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def _function(self, name: str) -> Callable[..., Any] | None:
        module = self.index.get(name)
        if module is None:
            return None
        if name not in self.server.mcp.functions:
            import_module(f"maxmcp.tools.{module}")
        return self.server.mcp.functions.get(name)

    def _tool(self, name: str) -> Callable[..., dict[str, Any]] | None:
        with self._lock:
            if name not in self._wrapped:
                fn = self._function(name)
                if fn is None:
                    return None
                from pydantic import validate_call

                from ..tool_response import make_structured_tool

                client = self.server.client
                validated = validate_call(fn, config={"arbitrary_types_allowed": True})
                self._wrapped[name] = make_structured_tool(
                    validated, before_call=client.clear_last_response, transport_provider=client.get_last_transport)
            return self._wrapped[name]

    def __call__(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        wrapped = self._tool(tool)
        if wrapped is None:
            return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"unknown tool {tool!r}"}}
        return wrapped(**arguments)

    def describe(self, name: str) -> tuple[str, str, inspect.Signature | None] | None:
        fn = self._function(name)
        if fn is None:
            return None
        signature = inspect.signature(fn)
        return str(signature), inspect.getdoc(fn) or "", signature

    def names(self) -> list[str]:
        return sorted(self.index)


_host: Host | None = None
_activate_lock = threading.Lock()


def activate() -> Host:
    """Install the stand-in server module and route mx through this process's bridge."""
    global _host
    with _activate_lock:
        if _host is not None:
            return _host
        existing = sys.modules.get("maxmcp.server")
        if existing is not None and not getattr(existing, "_INMAX", False):
            raise RuntimeError("maxmcp.server is already loaded; maxmcp.inmax runs only inside 3ds Max")
        server = types.ModuleType("maxmcp.server")
        server._INMAX = True  # type: ignore[attr-defined]
        server.mcp = _ToolCollector()  # type: ignore[attr-defined]
        server.client = InProcessClient()  # type: ignore[attr-defined]
        server.progressive_catalog = None  # type: ignore[attr-defined]
        sys.modules["maxmcp.server"] = server

        from ..tool_discovery import _module_tools

        tools_dir = Path(__file__).resolve().parents[1] / "tools"
        index: dict[str, str] = {}
        for path in sorted(tools_dir.glob("*.py")):
            if path.stem.startswith("_") or path.stem in _SKIPPED_MODULES:
                continue
            for name, _ in _module_tools(path):
                index.setdefault(name, path.stem)

        from .. import mx

        _host = Host(server, index)
        mx.set_backend(_host)
        return _host
