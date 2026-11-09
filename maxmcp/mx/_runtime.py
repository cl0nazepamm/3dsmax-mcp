"""Core of the mx Python API: call any 3dsmax-mcp tool from Python, plus the Node handle.

Every call goes through the same validation, guards, undo handling and readback as
the MCP tools. Tool results come back as plain dicts and lists; failures raise
MaxToolError with the tool's error code and hint.
"""

from __future__ import annotations

import inspect
import threading
import time
from typing import Any, Callable

# Tools mx must not call: they would recurse or only make sense to an MCP client.
_BLOCKED = frozenset({"run_python", "call_tool", "find_tools", "list_toolsets", "describe_toolset", "tool_help"})

Backend = Callable[[str, dict[str, Any]], dict[str, Any]]
_backend: Backend | None = None
_local = threading.local()


class MaxToolError(RuntimeError):
    """A tool call failed. `code` is the tool's error code (BAD_PARAM, NOT_FOUND, ...)."""

    def __init__(self, tool: str, error: dict[str, Any] | None, hint: dict[str, Any] | None = None):
        error = error or {}
        self.tool = tool
        self.code = str(error.get("code") or "ERROR")
        self.message = str(error.get("message") or "tool call failed")
        self.retryable = bool(error.get("retryable"))
        self.details = error.get("details")
        self.hint = hint
        text = f"{tool}: [{self.code}] {self.message}"
        if hint and hint.get("message"):
            text += f" (hint: {hint['message']})"
        super().__init__(text)


def set_backend(backend: Backend | None) -> None:
    """Route calls to `backend(tool, arguments) -> ToolEnvelope dict`; None restores the server.

    A backend may also provide describe(name) -> (signature text, manual, inspect.Signature
    or None) | None and names() -> iterable of tool names, for mx.help and mx.tools.
    """
    global _backend
    _backend = backend


def _server_backend(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from .. import server
    from ..tool_discovery import invoke_tool

    registered = server.mcp._tool_manager.get_tool(tool)
    if registered is not None:
        return invoke_tool(registered, arguments)
    if server.progressive_catalog is not None:
        return server.progressive_catalog.call_tool(tool, arguments)
    return {"ok": False, "error": {"code": "NOT_FOUND", "message": f"tool {tool!r} is not available in this profile"}}


class Run:
    """One script run on this thread: its deadline, call trace, and whether a call is in flight."""

    def __init__(self, deadline: float | None):
        self.deadline = deadline
        self.trace: list[dict[str, Any]] = []
        self.in_call = False


def begin_run(deadline: float | None = None) -> Run:
    """Start recording calls made on this thread (run_python)."""
    _local.run = Run(deadline)
    return _local.run


def end_run() -> None:
    _local.run = None


def call(tool: str, /, **arguments: Any) -> Any:
    """Run one tool by name and return its result; raise MaxToolError on failure.

    `tool` is positional-only, so a tool may take an argument named `tool` (invoke_tool).
    """
    if tool in _BLOCKED:
        raise MaxToolError(tool, {"code": "BAD_PARAM", "message": f"{tool} cannot be called from mx"})
    run = getattr(_local, "run", None)
    if run is not None and run.deadline is not None and time.monotonic() > run.deadline:
        raise TimeoutError("run_python time limit reached; no further tool calls were made")
    arguments = {k: v for k, v in arguments.items() if v is not None}
    started = time.perf_counter()
    if run is not None:
        run.in_call = True
    try:
        envelope = (_backend or _server_backend)(tool, arguments)
    finally:
        if run is not None:
            run.in_call = False
    if run is not None:
        run.trace.append({"tool": tool, "ok": bool(envelope.get("ok")),
                          "ms": round((time.perf_counter() - started) * 1000)})
    if not envelope.get("ok"):
        raise MaxToolError(tool, envelope.get("error"), envelope.get("hint"))
    return envelope.get("result")


def _registered_tool(name: str) -> Any:
    from .. import server

    tool = server.mcp._tool_manager.get_tool(name)
    if tool is None and server.progressive_catalog is not None and name in server.progressive_catalog.tool_modules:
        server.progressive_catalog.load_tools([name])
        tool = server.progressive_catalog.hidden_mcp._tool_manager.get_tool(name)
    return tool


def _describe(name: str) -> tuple[str, str, inspect.Signature | None] | None:
    """(signature text, manual, Python signature) of one tool from the active backend."""
    if _backend is not None and hasattr(_backend, "describe"):
        return _backend.describe(name)
    from ..tool_discovery import python_signature

    tool = _registered_tool(name)
    if tool is None:
        return None
    try:
        signature = inspect.signature(tool.fn)
    except (TypeError, ValueError):
        signature = None
    schema = getattr(tool, "_full_schema", tool.parameters)
    return python_signature(schema), getattr(tool, "_full_description", tool.description) or "", signature


def _names() -> set[str]:
    if _backend is not None and hasattr(_backend, "names"):
        return set(_backend.names())
    from .. import server

    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    if server.progressive_catalog is not None:
        names |= set(server.progressive_catalog.tool_modules)
    return names


def help(name: str) -> str:  # noqa: A001 - mirrors Python's help() for tools
    """The full manual of one tool, with its Python signature; "sdk..." documents the sdk module."""
    if name == "sdk" or name.startswith("sdk."):
        import sys

        from . import _reference

        module = sys.modules.get("_mx")
        if module is None:
            raise MaxToolError(name, {"code": "NOT_FOUND", "message": (
                "sdk is available only in run_python scripts that run inside 3ds Max (ran_in: max)")})
        try:
            return _reference.help_text(module, name)
        except LookupError as exc:
            raise MaxToolError(name, {"code": "NOT_FOUND", "message": str(exc)}) from None
    described = _describe(name)
    if described is None:
        raise MaxToolError(name, {"code": "NOT_FOUND", "message": f"unknown tool {name!r}"})
    signature, manual, _ = described
    return f"{name}{signature}\n\n{manual}"


class _Tools:
    """Every tool as a Python function: mx.tools.<name>(**arguments) -> result."""

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_") or name in _BLOCKED:
            raise AttributeError(name)

        def tool_function(**arguments: Any) -> Any:
            return call(name, **arguments)

        tool_function.__name__ = name
        try:
            described = _describe(name)
        except Exception:
            described = None
        if described is not None:
            tool_function.__doc__ = described[1]
            if described[2] is not None:
                tool_function.__signature__ = described[2]  # type: ignore[attr-defined]
        return tool_function

    def __dir__(self) -> list[str]:
        return sorted(_names() - _BLOCKED)


tools = _Tools()


def maxscript(code: str) -> Any:
    """Run MAXScript (escape hatch) and return its result value."""
    return call("execute_maxscript", code=code)


class Node:
    """A scene node by name and handle. Methods call the guarded tools and refresh readback."""

    __slots__ = ("name", "handle", "data")

    def __init__(self, name: str, handle: int | None = None, data: dict[str, Any] | None = None):
        self.name = name
        self.handle = int(handle) if handle not in (None, "", 0) else None
        self.data = data or {}

    @classmethod
    def from_result(cls, result: Any) -> "Node":
        """Build from a tool result or entry that carries `name` and optionally `handle`."""
        if isinstance(result, Node):
            return result
        if not isinstance(result, dict) or not result.get("name"):
            raise ValueError(f"result has no node name: {result!r}")
        return cls(str(result["name"]), result.get("handle"), result)

    def __repr__(self) -> str:
        return f"Node({self.name!r}, handle={self.handle})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Node) and (self.handle, self.name) == (other.handle, other.name)

    def __hash__(self) -> int:
        return hash((self.handle, self.name))

    def info(self) -> dict[str, Any]:
        """inspect_object readback: class, bounding box, position, modifiers, material."""
        self.data = call("inspect_object", name=self.name)
        return self.data

    @property
    def bbox(self) -> tuple[list[float], list[float]]:
        """World bounding box as (min, max)."""
        box = self.info()["boundingBox"]
        return box["min"], box["max"]

    @property
    def center(self) -> list[float]:
        low, high = self.bbox
        return [(a + b) / 2 for a, b in zip(low, high)]

    @property
    def size(self) -> list[float]:
        low, high = self.bbox
        return [b - a for a, b in zip(low, high)]

    def _target(self) -> dict[str, Any]:
        return {"handle": self.handle} if self.handle else {"name": self.name}

    def move(self, x: float = 0, y: float = 0, z: float = 0, coordinate_system: str = "world") -> "Node":
        """Move by an offset."""
        call("transform_object", **self._target(), move=[x, y, z], coordinate_system=coordinate_system)
        return self

    def rotate(self, x: float = 0, y: float = 0, z: float = 0, coordinate_system: str = "world") -> "Node":
        """Rotate by Euler degrees."""
        call("transform_object", **self._target(), rotate=[x, y, z], coordinate_system=coordinate_system)
        return self

    def scale(self, x: float = 1, y: float = 1, z: float = 1, coordinate_system: str = "world") -> "Node":
        """Scale by factors."""
        call("transform_object", **self._target(), scale=[x, y, z], coordinate_system=coordinate_system)
        return self

    def set(self, property: str, value: Any) -> "Node":  # noqa: A002 - tool argument name
        """Set one node property (name, wirecolor, renderable, ...); numbers, bools and tuples convert."""
        target = {"handle": self.handle} if self.handle else {"name": self.name}
        call("set_object_property", **target, property=property, value=ms_value(value))
        if property == "name":
            self.name = str(value)
        return self

    def delete(self) -> None:
        """Delete this node."""
        if self.handle:
            call("delete_objects", handles=[self.handle])
        else:
            call("delete_objects", names=[self.name])


def ms_value(value: Any) -> str:
    """A Python value as the MAXScript text the property tools take; strings pass unchanged."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)) and len(value) in (2, 3, 4) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
    ):
        return "[" + ",".join(repr(v) for v in value) + "]"
    raise TypeError(f"cannot convert {value!r} to a MAXScript value; pass the MAXScript text as a string")


def node_name(target: "Node | str") -> str:
    """The name of a Node, an sdk node or a name string, for tools that target by name."""
    if isinstance(target, str):
        return target
    name = getattr(target, "name", None)
    return name if isinstance(name, str) else str(target)
