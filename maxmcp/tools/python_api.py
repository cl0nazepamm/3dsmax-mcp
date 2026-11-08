"""run_python: one script against the mx Python API instead of many tool calls."""

from __future__ import annotations

import builtins
import ctypes
import threading
import time
import traceback
from typing import Any

from ..server import mcp
from .. import mx
from ..mx import _runtime

_OUTPUT_LIMIT = 20_000
_TRACE_LIMIT = 100
_MODULES = frozenset({
    "math", "cmath", "json", "re", "itertools", "functools", "collections", "statistics", "random",
    "dataclasses", "typing", "enum", "copy", "string", "decimal", "fractions", "operator", "textwrap", "numbers",
})
_BUILTINS = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytes", "callable", "chr", "classmethod", "complex", "dict",
    "dir", "divmod", "enumerate", "filter", "float", "format", "frozenset", "getattr", "hasattr", "hash", "hex",
    "id", "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next", "object", "oct",
    "ord", "pow", "property", "range", "repr", "reversed", "round", "set", "setattr", "slice", "sorted",
    "staticmethod", "str", "sum", "super", "tuple", "type", "zip", "__build_class__",
    "ArithmeticError", "AssertionError", "AttributeError", "Exception", "IndexError", "KeyError", "LookupError",
    "NotImplementedError", "RuntimeError", "StopIteration", "TimeoutError", "TypeError", "ValueError",
    "ZeroDivisionError",
)


def _import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    if name in {"mx", "maxmcp.mx"}:
        return mx
    if level == 0 and name.split(".")[0] in _MODULES:
        return builtins.__import__(name, globals, locals, fromlist, level)
    raise ImportError(f"{name!r} is not available in run_python; use mx, or one of: {', '.join(sorted(_MODULES))}")


def _plain(value: Any, depth: int = 0) -> Any:
    """A JSON-safe copy of a script's result; Nodes become {name, handle}."""
    if depth > 20:
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, mx.Node):
        return {"name": value.name, "handle": value.handle}
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v, depth + 1) for v in value]
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


@mcp.tool()
def run_python(code: str, timeout_s: float = 300) -> dict:
    """Run a Python script against 3ds Max through the `mx` API: one script instead of many tool calls.

    Prefer it for multi-step work (build, measure, loop, verify). `mx` is pre-imported; every
    call goes through the same validated, guarded tools as the MCP tools and raises
    mx.MaxToolError (.code, .message, .hint) on failure. Units are scene units; positions are
    (x, y, z). Each tool call is its own undo step.

    Objects (return mx.Node): mx.box(name, width, length, height, pos=(x,y,z), pos_mode="ground"|"center"),
      mx.sphere(name, radius, pos, pos_mode), mx.cylinder(name, radius, height, pos, pos_mode),
      mx.plane(name, width, length, pos, pos_mode), mx.create(type, name, params, pos, ...), mx.get(name),
      mx.find(pattern="Wall*", class_name=..., layer=...) -> [Node], mx.selection(), mx.scene() -> overview,
      mx.clone(target, mode, offset, count) -> [Node], mx.parent(child, parent), mx.select(*targets),
      mx.delete(*targets). pos_mode "ground" puts the base on pos, "center" the bbox center.
      Node: .name .handle .bbox (min, max) .center .size .info() .move(x,y,z) .rotate(x,y,z) degrees
      .scale(x,y,z) .set(property, value) .delete().
    Modeling: mx.boolean(target, operation="subtract"|"union"|"intersect", operands=[nodes], cutters=[specs]),
      mx.add_modifier(target, modifier, params="segs:48"), mx.set_modifier(target, property_name,
      property_value, modifier_index=1), mx.remove_modifier(target, modifier), mx.mesh(name, vertices, faces),
      mx.inspect_mesh(target), mx.mesh_edit(target, operations, expected_mesh), mx.spline(name, points,
      closed=False), mx.curve(definition, name, action="create"|"preview"), mx.loft(sections, name),
      mx.qa(target), mx.contacts(names, against).
    Shading: mx.material(targets, material_class="PhysicalMaterial", material_name, params),
      mx.share_material(targets, source), mx.set_material(target, property, value),
      mx.area_light(name, width, height, at, aim_at, intensity, unit), mx.point_light(name, at, intensity, unit),
      mx.sun(name, direction, intensity, unit), mx.lights([LightSpec dicts]), mx.light_capabilities().
      Lights need intensity; check mx.light_capabilities() for the renderer's units (photometric: "cd").
    View: mx.capture(views=["front", "three_quarter"], shading="clay", edges=True) returns the image file,
      mx.open_viewport(), mx.frame(*targets), mx.orbit(yaw, pitch), mx.look(eye, target), mx.viewport_capture().
    Any tool: mx.tools.<tool_name>(**arguments) returns that tool's result; mx.help("tool_name") returns its
      manual. Raw MAXScript: mx.maxscript(code). Property values may be numbers, bools or (x, y, z).

    print() output and a variable named `result` are returned, with the list of tool calls made.
    Imports are limited to mx and the standard math/json/re/itertools/functools/collections family.
    Do not render unless the user asks.
    """
    if not code.strip():
        return {"status": "error", "error": "code is empty"}
    printed: list[str] = []
    size = 0

    def capture_print(*values: Any, sep: str = " ", end: str = "\n", **_: Any) -> None:
        nonlocal size
        text = sep.join(str(v) for v in values) + end
        if size < _OUTPUT_LIMIT:
            printed.append(text[: _OUTPUT_LIMIT - size])
        size += len(text)

    safe_builtins = {name: getattr(builtins, name) for name in _BUILTINS}
    safe_builtins.update({"__import__": _import, "print": capture_print})
    namespace: dict[str, Any] = {"__builtins__": safe_builtins, "__name__": "__mx_script__", "mx": mx}

    deadline = time.monotonic() + max(1.0, min(float(timeout_s), 3600.0))
    run = _runtime.begin_run(deadline)

    def report() -> dict[str, Any]:
        output = "".join(printed)
        if size > _OUTPUT_LIMIT:
            output += f"\n... output truncated ({size} characters printed)"
        calls = run.trace[:_TRACE_LIMIT]
        summary: dict[str, Any] = {"output": output, "calls": calls}
        if len(run.trace) > _TRACE_LIMIT:
            summary["calls_total"] = len(run.trace)
        return summary

    try:
        compiled = compile(code, "<script>", "exec")
    except SyntaxError as exc:
        _runtime.end_run()
        return {"status": "error", "error": f"SyntaxError: {exc.msg} (line {exc.lineno})",
                "details": {"line": exc.lineno, "source": (exc.text or "").rstrip()}}
    try:
        with _Interrupter(threading.get_ident(), run, deadline):
            exec(compiled, namespace)
    except BaseException as exc:  # noqa: BLE001 - report every script failure, including the timeout
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<script>"]
        line = frames[-1].lineno if frames else None
        source = code.splitlines()[line - 1].strip() if line and line <= len(code.splitlines()) else ""
        details = {"line": line, "source": source, **report()}
        if isinstance(exc, mx.MaxToolError):
            details.update({"tool": exc.tool, "code": exc.code, "hint": exc.hint})
        text = str(exc) or (f"time limit of {timeout_s:g} s reached" if isinstance(exc, TimeoutError) else "")
        message = f"{type(exc).__name__}: {text}" + (f" (script line {line})" if line else "")
        return {"status": "error", "error": message, "details": _plain(details)}
    finally:
        _runtime.end_run()

    summary = report()
    if "result" in namespace:
        summary["result"] = _plain(namespace["result"])
    return summary
