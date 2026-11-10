"""run_python: one script against the mx API instead of many tool calls.

The script runs inside 3ds Max when it can: mx calls reach the native bridge in-process,
pymxs is in scope and the whole script is one undo step. Otherwise (bridge safe mode on,
an older bridge, or a Max whose Python differs from the server's) it runs here, in the
MCP server, sandboxed, with each tool call its own undo step.
"""

from __future__ import annotations

import builtins
import json
import sys
import sysconfig
from typing import Any

from ...assets import ASSETS
from ...server import client, mcp
from ... import mx
from ...core.maxscript import safe_string
from ...core.max_client import MaxBridgeError
from ...mx import _script

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
    raise ImportError(f"{name!r} is not available here; use mx, or one of: {', '.join(sorted(_MODULES))}")


def _run_in_server(code: str, timeout_s: float) -> dict[str, Any]:
    """Sandboxed: only mx and the standard math/json/re family, no files or processes."""
    safe_builtins: dict[str, Any] = {name: getattr(builtins, name) for name in _BUILTINS}
    safe_builtins["__import__"] = _import
    namespace: dict[str, Any] = {"__builtins__": safe_builtins, "__name__": "__mx_script__", "mx": mx}
    reply = _script.execute(code, namespace, timeout_s)
    reply["ran_in"] = "server"
    return reply


def _launcher(code: str, timeout_s: float) -> str:
    """MAXScript that runs the script through maxmcp.inmax inside Max and returns its JSON reply.

    No hold may be open around the script: the runner opens the script's own undo step
    (script_hold), which refuses while another hold is open. So no `undo on`, and no
    python.Execute either, which runs its code inside a hold of its own; builtins.exec
    called through python.import opens none.

    Max keeps imported modules for the session, so maxmcp is imported anew when its
    source changed since the last script (a development checkout, or an upgrade).
    """
    package = str(ASSETS.parent)
    root = str(ASSETS.parent.parent)
    site = sysconfig.get_paths()["purelib"]
    source = (
        "import os, sys\n"
        f"for _path in ({root!r}, {site!r}):\n"
        "    if _path not in sys.path: sys.path.append(_path)\n"
        f"_stamp = max((os.path.getmtime(os.path.join(_d, _f)) for _d, _, _fs in os.walk({package!r}) "
        "for _f in _fs if _f.endswith('.py')), default=0)\n"
        "if getattr(sys, '_maxmcp_stamp', None) != _stamp:\n"
        "    for _name in [n for n in sys.modules if n == 'maxmcp' or n.startswith('maxmcp.')]:\n"
        "        del sys.modules[_name]\n"
        "    sys._maxmcp_stamp = _stamp\n"
        "import maxmcp.inmax.runner as _runner\n"
        f"_runner.launch({code!r}, {float(timeout_s)!r}, {tuple(sys.version_info[:2])!r})\n"
    )
    bootstrap = safe_string(f"exec({ascii(source)}, {{}})")
    return f'''(
    if theHold.Holding() then
        "{{\\"status\\":\\"error\\",\\"code\\":\\"USER_BUSY\\",\\"error\\":\\"3ds Max has an open undo operation; finish it, then run the script again.\\"}}"
    else (
        global MCP_RunPython_Result
        local previous = MCP_RunPython_Result
        local reply
        MCP_RunPython_Result = undefined
        try (
            local py = python.import "builtins"
            py.exec "{bootstrap}" (py.dict())
            reply = MCP_RunPython_Result
        ) catch (
            MCP_RunPython_Result = previous
            throw()
        )
        MCP_RunPython_Result = previous
        if classof reply != String do throw "run_python returned no result."
        reply
    )
)'''


def _run_in_max(code: str, timeout_s: float) -> tuple[dict[str, Any] | None, str]:
    """(reply, "") when the script ran inside Max, else (None, why it could not)."""
    try:
        response = client.send_command(_launcher(code, timeout_s), cmd_type="maxscript", timeout=timeout_s + 30)
    except MaxBridgeError as exc:
        return None, f"3ds Max refused to run Python ({exc})"
    text = response.get("result")
    try:
        reply = json.loads(text) if isinstance(text, str) else None
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        return None, f"unexpected reply from 3ds Max: {str(text)[:200]}"
    if "unavailable" in reply:
        return None, str(reply["unavailable"])
    return reply, ""


@mcp.tool()
def run_python(code: str, timeout_s: float = 300, where: str = "auto") -> dict:
    """Run a Python script against 3ds Max through the `mx` API: one script instead of many tool calls.

    Prefer it for multi-step work (build, measure, loop, verify). `mx` is pre-imported; every
    call goes through the same validated, guarded tools as the MCP tools and raises
    mx.MaxToolError (.code, .message, .hint) on failure. Units are scene units; positions are
    (x, y, z).

    Where it runs (`ran_in` in the reply): inside 3ds Max when the bridge allows Python
    (safe_mode off) and Max's Python matches the server's. There `sdk`, `rt` (pymxs.runtime)
    and pymxs are in scope too, and everything the script changes (tool calls, sdk and rt
    edits) is ONE undo step, rolled back entirely if the script raises. An undo inside the
    script (mx.tools.undo_last(), sdk.undo()) first commits what it changed so far as its own
    step. Otherwise in the MCP server: imports limited to mx and the standard math/json/re
    family, each tool call its own undo step. where="max" or "server" forces one.

    sdk (inside Max; also mx.sdk): the scene object model bound straight from the 3ds Max SDK,
      calls in microseconds. sdk.node("Ball"), sdk.nodes("Wall*"), sdk.create("Sphere", "Ball",
      pos=(0, 0, 10), radius=10), node.pos / .rot (degrees) / .scale / .transform settable,
      node.look_at(point), node.params.radius = 12,
      node.add_modifier("Bend", angle=30), node.modifiers, node.material = sdk.material("PhysicalMaterial",
      "Red", base_color=(1, 0, 0)), with sdk.at(10): ..., with sdk.animate(): ..., node.bbox,
      sdk.contacts(nodes). mx.help("sdk") is its index, mx.help("sdk.Node") one class. sdk nodes and
      mx.Node are separate types; each side accepts the other's nodes. In pymxs a node is
      rt.getAnimByHandle(node.handle).

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
      mx.qa(target) -> {"counts": {...}, ...}, mx.contacts(names, against).
    Shading: mx.material(targets, material_class="PhysicalMaterial", material_name, params),
      mx.share_material(targets, source), mx.set_material(target, property, value).
    Light and render, the same call on every renderer (Octane, FStorm, V-Ray, Corona, Scanline, ART):
      mx.render_setup() tells what the current renderer offers and what the scene has.
      mx.render_setup(renderer="octane", quality="draft", sun={"elevation": 40, "azimuth": 220},
      lights=[{"name": "Lamp", "kind": "sphere", "radius": 4, "position": (0, 0, 150), "intensity": 800,
      "unit": "lm", "kelvin": 2700}], environment={"path": "sky.exr"}, camera={"name": "Cam",
      "position": (300, -400, 150), "aim_at": (0, 0, 100), "fov": 55}, render={"camera": "Cam",
      "size": (1280, 720)}) creates or updates each thing by name in one step and returns the
      rendered image with the reply. Light kinds: rectangle, disk, sphere, cylinder, point. Never
      build renderer-specific light, sun or camera classes by hand; mx.help("render_setup") is the manual.
    View: mx.capture(views=["front", "three_quarter"], shading="clay", edges=True) returns the image file,
      mx.open_viewport(), mx.frame(*targets), mx.orbit(yaw, pitch), mx.look(eye, target), mx.viewport_capture().
      mx.show(path_or_capture) returns an image with the reply, so you see it without reading the file.
    Any tool: mx.tools.<tool_name>(**arguments) returns that tool's result. mx.help() lists every tool by
      group, mx.find_tools("boolean subtract") returns the manuals of the best matches and mx.help("tool_name")
      one tool's manual. Raw MAXScript: mx.maxscript(code). Property values may be numbers, bools or (x, y, z).

    print() output and a variable named `result` are returned, with the list of tool calls made
    and, in `warnings`, what 3ds Max said beside a call's result (an open dialog, a call that
    went to another Max, a script that ran with its prompts shown).
    Do not render unless the user asks.
    """
    if where not in {"auto", "max", "server"}:
        return {"status": "error", "error": "where must be 'auto', 'max' or 'server'"}
    reason = ""
    if where != "server":
        reply, reason = _run_in_max(code, timeout_s)
        if reply is not None:
            return reply
        if where == "max":
            return {"status": "error", "error": f"Cannot run inside 3ds Max: {reason}"}
    reply = _run_in_server(code, timeout_s)
    if reason:
        reply["note"] = f"Ran in the MCP server, not inside 3ds Max: {reason}"
    return reply
