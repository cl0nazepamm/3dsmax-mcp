"""Core of the mx Python API: call any 3dsmax-mcp tool from Python (the node type is in _node).

Every call goes through the same validation, guards, undo handling and readback as
the MCP tools. Tool results come back as plain dicts and lists; failures raise
MaxToolError with the tool's error code and hint.
"""

from __future__ import annotations

import threading
import time
from difflib import get_close_matches
from importlib import import_module
from typing import Any, Callable

# A script calling run_python would start a script inside a script.
_BLOCKED = frozenset({"run_python"})

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
    """Route calls to `backend(tool, arguments) -> ToolEnvelope dict`; None restores the registry.

    A backend may also provide describe(name) -> (signature text, manual, inspect.Signature
    or None) | None and names() -> iterable of tool names, for mx.help and mx.tools.
    """
    global _backend
    _backend = backend


def _registry() -> Any:
    """This process's ToolRegistry: the MCP server's, or maxmcp.inmax's inside 3ds Max."""
    return import_module("..server", __package__).registry


def _source(method: str) -> Any:
    """The backend when it provides `method`, else the registry."""
    return _backend if _backend is not None and hasattr(_backend, method) else _registry()


class Run:
    """One script run on this thread: its deadline, call trace, warnings, and whether a call is in flight."""

    def __init__(self, deadline: float | None):
        self.deadline = deadline
        self.trace: list[dict[str, Any]] = []
        # What the bridge said beside a call's result (an open dialog, a retargeted Max, a
        # script that ran with prompts shown). A script sees only results, so the run keeps these.
        self.warnings: list[Any] = []
        # Image files to hand back with the reply (renders, captures), so they need no second call.
        self.images: list[str] = []
        self.in_call = False


def begin_run(deadline: float | None = None) -> Run:
    """Start recording calls made on this thread (run_python)."""
    _local.run = Run(deadline)
    return _local.run


def end_run() -> None:
    _local.run = None


IMAGE_LIMIT = 4


def show(image: Any) -> bool:
    """Return an image file with this script's reply, so you see it without reading the file.

    image: a path, or a result dict with a `file` (a capture or a render). At most four
    images per script are returned; False when this one is not (no script is running, the
    limit is reached, or it is not a path). render_setup shows its renders by itself.
    """
    path = image.get("file") if isinstance(image, dict) else image
    run = getattr(_local, "run", None)
    if run is None or not isinstance(path, (str, bytes)) and not hasattr(path, "__fspath__"):
        return False
    text = str(path)
    if text in run.images:
        return True
    if len(run.images) >= IMAGE_LIMIT:
        return False
    run.images.append(text)
    return True


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
        envelope = (_backend or _registry())(tool, arguments)
    finally:
        if run is not None:
            run.in_call = False
    if run is not None:
        run.trace.append({"tool": tool, "ok": bool(envelope.get("ok")),
                          "ms": round((time.perf_counter() - started) * 1000)})
        for warning in envelope.get("warnings") or ():
            entry = f"{tool}: {warning}" if isinstance(warning, str) else {"tool": tool, "warning": warning}
            if entry not in run.warnings:
                run.warnings.append(entry)
    if not envelope.get("ok"):
        raise MaxToolError(tool, envelope.get("error"), envelope.get("hint"))
    return envelope.get("result")


def _describe(name: str) -> Any:
    """(signature text, manual, Python signature) of one tool, or None for an unknown name."""
    return _source("describe").describe(name)


def _names() -> set[str]:
    return set(_source("names").names())


def _tool_help(name: str) -> str:
    signature, manual, _ = _describe(name)
    return f"{name}{signature}\n\n{manual}"


def help(name: str = "") -> str:  # noqa: A001 - mirrors Python's help() for tools
    """What mx.tools offers, as text to print.

    help() lists every tool by group. help("modeling") lists one group with each tool's
    signature and summary. help("boolean_operation") is one tool's signature and full manual.
    "sdk", "sdk.Node" and "sdk.Node.pos" document the sdk module inside 3ds Max.
    """
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
    groups = _source("groups").groups()
    if not name:
        lines = ['mx.tools by group. mx.help("<group>") lists one, mx.help("<tool>") is a tool\'s manual, '
                 'mx.find_tools("words") searches.']
        for group, (description, names) in groups.items():
            listed = [tool for tool in names if tool not in _BLOCKED]
            lines.append(f"\n{group}: {description}\n  " + ", ".join(listed))
        return "\n".join(lines)
    if name in groups:
        description, names = groups[name]
        registry = _source("summary")
        lines = [f"{name}: {description}"]
        for tool in names:
            if tool not in _BLOCKED:
                lines.append(f"\n{tool}{_describe(tool)[0]}\n  {registry.summary(tool)}")
        return "\n".join(lines)
    if _describe(name) is None:
        close = get_close_matches(name, sorted(_names() - _BLOCKED), n=3, cutoff=0.6)
        raise MaxToolError(name, {"code": "NOT_FOUND", "message": (
            f"unknown tool {name!r}." + (f" Did you mean {', '.join(close)}?" if close else "")
            + " mx.help() lists every tool by group.")})
    return _tool_help(name)


def find_tools(query: str, limit: int = 3) -> str:
    """Search the tools by what you need, e.g. "boolean subtract" or "spline knots".

    Returns the signature and manual of the best matches, as text to print, and names the
    other tools that matched.
    """
    ranked = [name for name in _source("search").search(query) if name not in _BLOCKED]
    if not ranked:
        return f'No tool matches {query!r}. Try other words; mx.help() lists every tool by group.'
    limit = max(1, min(int(limit), 10))
    text = ("\n\n" + "-" * 60 + "\n\n").join(_tool_help(name) for name in ranked[:limit])
    if ranked[limit:]:
        text += "\n\n" + "-" * 60 + "\n\nAlso matching: " + ", ".join(ranked[limit:limit + 10])
    return text


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


def node_name(target: Any) -> str:
    """The name of a node (mx.Node or sdk node) or a name string, for tools that target by name."""
    if isinstance(target, str):
        return target
    name = getattr(target, "name", None)
    return name if isinstance(name, str) else str(target)


def node_handle(target: Any) -> int | None:
    """The handle of a node (mx.Node or sdk node), for tools that take one; None for a name or a node without."""
    handle = None if isinstance(target, str) else getattr(target, "handle", None)
    return handle if isinstance(handle, int) and not isinstance(handle, bool) and handle else None
