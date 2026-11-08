"""Run one mx script: captured print(), a `result` variable, the tool-call trace and a time limit.

Shared by run_python in the MCP server and by maxmcp.inmax inside 3ds Max, so a script
behaves the same wherever it runs.
"""

from __future__ import annotations

import contextlib
import ctypes
import threading
import time
import traceback
from typing import Any, Callable, ContextManager

from . import _runtime
from ._runtime import MaxToolError, Node

OUTPUT_LIMIT = 20_000
TRACE_LIMIT = 100


def plain(value: Any, depth: int = 0) -> Any:
    """A JSON-safe copy of a script's result; Nodes become {name, handle}."""
    if depth > 20:
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Node):
        return {"name": value.name, "handle": value.handle}
    if isinstance(value, dict):
        return {str(k): plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v, depth + 1) for v in value]
    return repr(value)


class _Interrupter:
    """Raise TimeoutError in the script thread after the deadline, never inside a tool call."""

    def __init__(self, thread_id: int, run: Any, deadline: float):
        self.thread_id, self.run, self.deadline = thread_id, run, deadline
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def _watch(self) -> None:
        while not self.done.wait(min(0.25, max(0.0, self.deadline - time.monotonic()))):
            if time.monotonic() >= self.deadline and not self.run.in_call:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self.thread_id), ctypes.py_object(TimeoutError))
                return

    def __enter__(self) -> "_Interrupter":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.done.set()


def execute(
    code: str,
    namespace: dict[str, Any],
    timeout_s: float,
    *,
    transaction: Callable[[], ContextManager[Any]] | None = None,
) -> dict[str, Any]:
    """Run `code` in `namespace`, whose print() this replaces. Returns the run_python reply.

    transaction() wraps the run, e.g. one undo step that rolls back when the script raises;
    it must re-raise the script's exception.
    """
    if not code.strip():
        return {"status": "error", "error": "code is empty"}
    printed: list[str] = []
    size = 0

    def capture_print(*values: Any, sep: str = " ", end: str = "\n", **_: Any) -> None:
        nonlocal size
        text = sep.join(str(v) for v in values) + end
        if size < OUTPUT_LIMIT:
            printed.append(text[: OUTPUT_LIMIT - size])
        size += len(text)

    builtins_ = namespace.get("__builtins__")
    if isinstance(builtins_, dict):
        builtins_["print"] = capture_print
    namespace["print"] = capture_print

    try:
        compiled = compile(code, "<script>", "exec")
    except SyntaxError as exc:
        return {"status": "error", "error": f"SyntaxError: {exc.msg} (line {exc.lineno})",
                "details": {"line": exc.lineno, "source": (exc.text or "").rstrip()}}

    timeout_s = max(1.0, min(float(timeout_s), 3600.0))
    run = _runtime.begin_run(time.monotonic() + timeout_s)

    def report() -> dict[str, Any]:
        output = "".join(printed)
        if size > OUTPUT_LIMIT:
            output += f"\n... output truncated ({size} characters printed)"
        summary: dict[str, Any] = {"output": output, "calls": run.trace[:TRACE_LIMIT]}
        if len(run.trace) > TRACE_LIMIT:
            summary["calls_total"] = len(run.trace)
        return summary

    try:
        with (transaction or contextlib.nullcontext)(), _Interrupter(threading.get_ident(), run, run.deadline):
            exec(compiled, namespace)
    except BaseException as exc:  # noqa: BLE001 - report every script failure, including the timeout
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<script>"]
        line = frames[-1].lineno if frames else None
        lines = code.splitlines()
        source = lines[line - 1].strip() if line and line <= len(lines) else ""
        details: dict[str, Any] = {"line": line, "source": source, **report()}
        if isinstance(exc, MaxToolError):
            details.update({"tool": exc.tool, "code": exc.code, "hint": exc.hint})
        text = str(exc) or (f"time limit of {timeout_s:g} s reached" if isinstance(exc, TimeoutError) else "")
        message = f"{type(exc).__name__}: {text}" + (f" (script line {line})" if line else "")
        return {"status": "error", "error": message, "details": plain(details)}
    finally:
        _runtime.end_run()

    summary = report()
    if "result" in namespace:
        summary["result"] = plain(namespace["result"])
    return summary
