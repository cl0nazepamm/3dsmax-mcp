"""Generate the native diagnostic tool registry from maxmcp/tools/*.py.

Only directly routable native handlers and execute_maxscript are included.
The external MCP server owns Python orchestration and exact schema validation.
Shared extraction helpers also serve the tool catalog and native smoke cases.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "maxmcp" / "tools"
OUT_PATH = ROOT / "native" / "generated" / "native_tool_registry.inc"

# Heuristic type hint → JSON-schema mapping for catalog previews. Exact
# runtime validation happens in the external server, not here.
TYPE_MAP = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "dict": {"type": "object"},
    "list": {"type": "array"},
    "StrList": {"type": "array", "items": {"type": "string"}},
    "IntList": {"type": "array", "items": {"type": "integer"}},
    "FloatList": {"type": "array", "items": {"type": "number"}},
    "DictList": {"type": "array", "items": {"type": "object"}},
    "DictValue": {"type": "object"},
    "Any": {},
}


def annotation_to_schema(node: ast.expr | None) -> dict[str, Any]:
    if node is None:
        return {}
    if isinstance(node, ast.Name):
        return dict(TYPE_MAP.get(node.id, {}))
    if isinstance(node, ast.Subscript):
        base = node.value.id if isinstance(node.value, ast.Name) else ""
        if base == "Optional":
            inner = node.slice
            return annotation_to_schema(inner)
        if base == "list" or base == "List":
            inner = annotation_to_schema(node.slice)
            return {"type": "array", "items": inner or {}}
        if base == "Literal":
            items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            values: list[Any] = []
            for item in items:
                try:
                    values.append(ast.literal_eval(item))
                except (ValueError, TypeError):
                    return {}
            schema: dict[str, Any] = {"enum": values}
            value_types = {type(value) for value in values}
            if value_types == {str}:
                schema["type"] = "string"
            elif value_types == {bool}:
                schema["type"] = "boolean"
            elif value_types == {int}:
                schema["type"] = "integer"
            elif value_types <= {int, float}:
                schema["type"] = "number"
            return schema
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # `str | None` style — return the non-None side
        for side in (node.left, node.right):
            if isinstance(side, ast.Constant) and side.value is None:
                continue
            return annotation_to_schema(side)
    if isinstance(node, ast.Constant) and node.value is None:
        return {}
    return {}


def is_mcp_tool_decorator(dec: ast.expr) -> bool:
    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
        return dec.func.attr == "tool" and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "mcp"
    if isinstance(dec, ast.Attribute):
        return dec.attr == "tool"
    return False


CMD_TYPE_RE = re.compile(r'cmd_type\s*=\s*["\'](native:[\w_]+|maxscript)["\']')


def find_cmd_type(func: ast.FunctionDef, source: str) -> str | None:
    # Scan the function body's source for cmd_type="native:xxx" or "maxscript".
    # Prefer native over maxscript — hybrid tools try native first.
    start = func.lineno
    end = func.end_lineno or start
    body_src = "\n".join(source.splitlines()[start - 1:end])
    natives = re.findall(r'cmd_type\s*=\s*["\']native:([\w_]+)["\']', body_src)
    if natives:
        return f"native:{natives[0]}"
    if re.search(r'cmd_type\s*=\s*["\']maxscript["\']', body_src):
        return "maxscript"
    # Fallback: plain send_command(maxscript) with no cmd_type kwarg
    if re.search(r'client\.send_command\(\s*[a-zA-Z_]', body_src):
        return "maxscript"
    return None


def build_schema(func: ast.FunctionDef) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for arg, default in zip_args(func):
        if arg.arg in ("self", "cls"):
            continue
        schema = annotation_to_schema(arg.annotation) or {}
        props[arg.arg] = schema
        if default is None:
            required.append(arg.arg)
    result: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        result["required"] = required
    return result


def zip_args(func: ast.FunctionDef):
    args = func.args.args
    defaults = func.args.defaults
    n_no_default = len(args) - len(defaults)
    for i, a in enumerate(args):
        default = None if i < n_no_default else defaults[i - n_no_default]
        yield a, default


def first_doc_line(func: ast.FunctionDef) -> str:
    doc = ast.get_docstring(func) or ""
    # Take the first paragraph (stop at blank line), clamp to 300 chars
    first = doc.split("\n\n", 1)[0].strip()
    first = re.sub(r"\s+", " ", first)
    return first[:300]


# These tools compile/validate a temporary graph in Python before forwarding
# a smaller exact-ID payload. Direct native probes cannot run that orchestration.
SKIP_TOOL_NAMES = {
    # Mixed Python orchestration: status is a local file read, start only arms
    # notifications, and cancel_capture uses a separate native route. Routing
    # every action to the first discovered send_command could send an abort
    # when a native diagnostic caller requested status.
    "render_automations",
    "mcg_apply_modifier",
    "mcg_inspect_instance",
    "mcg_resolve_class",
    "mcg_set_node_parameter",
}

# Python-generated MAXScript wrappers require the external server; direct native
# probes can only pass through explicit execute_maxscript code.
INCLUDE_MAXSCRIPT_NAMES = {"execute_maxscript"}

# Hybrid wrappers sometimes expose Python-only orchestration fields that the
# native route cannot consume directly. Override their direct-native surface
# with the exact payload contract accepted by the selected native handler.
NATIVE_TOOL_OVERRIDES: dict[str, dict[str, Any]] = {
    "assign_material": {
        "description": "Create a material and assign it to objects. Sharing a source object's material requires the external MCP wrapper.",
        "schema": {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}},
                "handles": {"type": "array", "items": {"type": "integer"}},
                "material_class": {"type": "string"},
                "material_name": {"type": "string"},
                "params": {"type": "string"},
            },
            "required": ["material_class"],
        },
    },
    "create_shell_material": {
        "description": (
            "Wrap existing render and export materials in a Shell Material. "
            "Texture-folder material building requires the external MCP server."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "shell_name": {"type": "string"},
                "render_material": {"type": "string"},
                "export_material": {"type": "string"},
                "assign_to": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "render_slot": {"type": "integer"},
                "viewport_slot": {"type": "integer"},
            },
            "required": ["shell_name", "render_material"],
        },
    },
    # The external wrapper selects scan/fix routes dynamically. Native probes
    # get the read-only scan contract only; exposing a fixed mutation
    # route under the same mixed-action schema would bypass that safety split.
    "scene_qa": {
        "cmdType": "native:scene_qa_scan",
        "description": (
            "Scan deterministic non-mesh scene hygiene: names, transforms, "
            "hierarchy/groups, and timeline state."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "checks": {"type": "array", "items": {"type": "string"}},
                "scope": {"type": "string"},
                "names": {"type": "array", "items": {"type": "string"}},
                "handles": {"type": "array", "items": {"type": "integer"}},
                "refs": {"type": "array", "items": {"type": "object"}},
                "max_issues": {"type": "integer"},
                "transform_epsilon": {"type": "number"},
                "far_origin_threshold": {"type": "number"},
            },
        },
    },
}


def extract_tools(path: Path) -> list[dict]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"[warn] skip {path.name}: {e}", file=sys.stderr)
        return []
    tools = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not any(is_mcp_tool_decorator(d) for d in node.decorator_list):
            continue
        override = NATIVE_TOOL_OVERRIDES.get(node.name, {})
        cmd_type = override.get("cmdType") or find_cmd_type(node, source)
        if not cmd_type:
            # Python-only tool (manifest, identify, etc.) — skip
            continue
        if node.name in SKIP_TOOL_NAMES:
            continue
        if cmd_type == "maxscript" and node.name not in INCLUDE_MAXSCRIPT_NAMES:
            # Python-side MAXScript wrapper — its body requires the server.
            continue
        tool = {
            "name": node.name,
            "cmdType": cmd_type,
            "description": first_doc_line(node) or node.name,
            "schema": build_schema(node),
        }
        tool.update(override)
        tools.append(tool)
    return tools


def c_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ").replace("\r", "")


def main() -> int:
    tools: list[dict] = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tools.extend(extract_tools(path))

    # De-duplicate on name (first wins)
    seen: set[str] = set()
    uniq = []
    for t in tools:
        if t["name"] in seen:
            continue
        seen.add(t["name"])
        uniq.append(t)

    # Make sure execute_maxscript is present (register.py exposes it; safe to
    # force-include for explicit native diagnostics).
    if "execute_maxscript" not in seen:
        uniq.append({
            "name": "execute_maxscript",
            "cmdType": "maxscript",
            "description": "Run arbitrary MAXScript code. Subject to safe_mode filter.",
            "schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("// AUTO-GENERATED by scripts/gen_tool_registry.py — do not edit by hand.")
    lines.append(f"// Source: {len(uniq)} tools from maxmcp/tools/*.py")
    lines.append("")
    lines.append("static const NativeTool kNativeTools[] = {")
    for t in uniq:
        lines.append(
            f'    {{"{c_escape(t["name"])}", "{c_escape(t["cmdType"])}"}},'
        )
    lines.append("};")
    lines.append(f"static const size_t kNativeToolCount = sizeof(kNativeTools) / sizeof(kNativeTools[0]);")
    lines.append("")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[gen_tool_registry] wrote {len(uniq)} tools -> {OUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
