"""Generate native/generated/chat_tool_registry.inc from src/tools/*.py.

Walks every @mcp.tool() function, extracts its name/docstring/signature,
and emits a C++ initializer list that llm_client.cpp includes. Routing is
by cmd_type: native handlers have `cmd_type="native:<name>"` somewhere in
their body; tools without one are skipped (they'd need the Python server
to run). execute_maxscript is added manually as a catch-all.

Invoked by native/CMakeLists.txt before compiling llm_client.cpp.
If this script fails, llm_client.cpp's #else branch keeps chat working
with a hand-coded registry.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "src" / "tools"
OUT_PATH = ROOT / "native" / "generated" / "chat_tool_registry.inc"

# Heuristic type hint → JSON-schema mapping. Good-enough for the LLM; exact
# runtime validation happens server-side, not here.
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


# Tools that route through native:chat_ui are for external MCP drivers; the
# in-Max chat itself must not expose them (would let the model call its own
# send/reload/clear handlers and recurse).
SKIP_CMD_TYPES = {"native:chat_ui"}


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
        cmd_type = find_cmd_type(node, source)
        if not cmd_type:
            # Python-only tool (manifest, identify, etc.) — skip
            continue
        if cmd_type in SKIP_CMD_TYPES:
            continue
        tools.append({
            "name": node.name,
            "cmdType": cmd_type,
            "description": first_doc_line(node) or node.name,
            "schema": build_schema(node),
        })
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
    # force-include so the LLM always has the catch-all).
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
    lines.append(f"// Source: {len(uniq)} tools from src/tools/*.py")
    lines.append("")
    lines.append("static const ChatTool kChatTools[] = {")
    for t in uniq:
        schema_json = json.dumps(t["schema"], separators=(",", ":"))
        lines.append(
            f'    {{"{c_escape(t["name"])}", "{c_escape(t["cmdType"])}", '
            f'"{c_escape(t["description"])}", "{c_escape(schema_json)}"}},'
        )
    lines.append("};")
    lines.append(f"static const size_t kChatToolCount = sizeof(kChatTools) / sizeof(kChatTools[0]);")
    lines.append("")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[gen_tool_registry] wrote {len(uniq)} tools -> {OUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
