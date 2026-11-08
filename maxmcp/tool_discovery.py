"""Progressive MCP tool discovery and lazy dispatch.

The progressive profile advertises a few frequently used tools directly and
reaches every other tool through find_tools/describe_toolset and call_tool.
Tool modules are indexed from source without importing them, then imported
into a private FastMCP registry only when a tool is described or called.
"""

from __future__ import annotations

import ast
import re
from contextvars import ContextVar
from dataclasses import dataclass
from difflib import get_close_matches
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import ValidationError

from .tool_response import ToolEnvelope, make_structured_tool
from .user_config import TOOL_DOCS, setting


META_TOOL_NAMES = frozenset({"list_toolsets", "find_tools", "describe_toolset", "call_tool"})
# Advertised directly by the progressive profile because most tasks use them; every
# other tool is found with find_tools and run through call_tool. Keep this list small:
# each entry costs its schema on every model request.
PROGRESSIVE_DIRECT_TOOLS = frozenset({
    "query_scene", "inspect_object", "create_object", "transform_object", "set_object_property",
    "delete_objects", "clone_objects", "add_modifier", "set_modifier_property", "assign_material",
    "set_material_property", "execute_maxscript", "capture_viewport", "undo_last",
})
_RESULT_CONTRACT = "ToolEnvelope: {ok,result} or {ok:false,error}; optional hint/transport."
_WORD = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    "a an and as at by for from in into is it of on or the to with 3ds max tool tools".split()
)
_active_calls: ContextVar[frozenset[str]] = ContextVar(
    "3dsmax_mcp_progressive_active_calls",
    default=frozenset(),
)


@dataclass(frozen=True)
class ToolsetSpec:
    """A small semantic group whose exact schemas can be requested on demand."""

    name: str
    description: str
    modules: tuple[str, ...]


TOOLSET_SPECS = (
    ToolsetSpec(
        "cosmos",
        "Search, download and import compatible Cosmos models, materials and HDRIs.",
        ("cosmos",),
    ),
    ToolsetSpec(
        "connection",
        "Bridge diagnostics, hung-Max stack capture, installed capabilities, session context, main-thread checks, "
        "and native core reload.",
        ("bridge", "diagnostics", "capabilities", "session_context", "mainthread", "core_reload"),
    ),
    ToolsetSpec(
        "scene",
        "Scene queries, dependency reads, save/reset operations, and undo.",
        ("query_scene", "scene_query", "scene_manage", "scene_patch", "scene_qa"),
    ),
    ToolsetSpec(
        "objects",
        "Create, identify, transform, select, organize, clone, and parent scene nodes.",
        (
            "objects",
            "transform",
            "orientation",
            "hierarchy",
            "selection",
            "visibility",
            "clone",
            "organize",
            "identify",
        ),
    ),
    ToolsetSpec(
        "modeling",
        "Modifier stacks, booleans, splines, custom polygon meshes, and visual vertex/edge/face editing.",
        ("modifiers", "booleans", "splines", "poly_edit", "mesh_ops", "geometry_qa", "contact_check", "component_pick", "loft", "curve_model", "curve_edit"),
    ),
    ToolsetSpec(
        "materials",
        "Materials, texture maps, material networks, palettes, replacement, and smart import.",
        (
            "materials",
            "material_ops",
            "material_network",
            "material_roles",
            "palette_laydown",
            "smart_import",
            "material_replace",
        ),
    ),
    ToolsetSpec(
        "inspection",
        "Object/property inspection, plugin introspection, and reference learning tools.",
        ("inspect", "plugins", "plugin_edit", "learning"),
    ),
    ToolsetSpec(
        "lighting",
        "Renderer-independent lights, environment maps, capabilities and verified edits.",
        ("lighting",),
    ),
    ToolsetSpec(
        "animation",
        "Animation keys, controllers, constraints, and parameter wiring.",
        ("controllers", "keyframes", "wire_params"),
    ),
    ToolsetSpec(
        "files",
        "Inspect, search, and merge external 3ds Max scene files.",
        ("file_access",),
    ),
    ToolsetSpec(
        "viewport",
        "Own AGENT VIEWPORT; navigate, frame, pick surfaces, and capture independently of the user.",
        ("viewport",),
    ),
    ToolsetSpec(
        "automation",
        "MAXScript and Python fallbacks plus direct invocation and smoke-test drivers.",
        ("execute", "tool_test"),
    ),
    ToolsetSpec(
        "data_channel",
        "Data Channel modifier creation, inspection, and operator configuration.",
        ("data_channel",),
    ),
    ToolsetSpec(
        "max_creation_graph",
        "Transactional Max Creation Graph discovery, editing, compilation, and verification.",
        ("mcg",),
    ),
    ToolsetSpec(
        "tyflow",
        "tyFlow creation, graph patching, manifests, wiring, census, simulation reads, and C# Script operators.",
        ("tyflow", "tyflow_graph", "tyflow_patch", "tyflow_manifest", "tyflow_census", "tyflow_script"),
    ),
    ToolsetSpec(
        "railclone",
        "RailClone XML style read/write and generated output inspection (7.3.5+).",
        ("railclone",),
    ),
    ToolsetSpec(
        "scattering",
        "Forest Pack scattering workflows.",
        ("scattering",),
    ),
    ToolsetSpec(
        "scene_effects",
        "Scene effects, state sets, and camera sequence reads.",
        ("effects", "state_sets"),
    ),
    ToolsetSpec(
        "pulze",
        "Pulze Scene Manager setups: inspection, render pre-flight, guarded edits and restore.",
        ("pulze",),
    ),
    ToolsetSpec(
        "rendering",
        "Render configuration and render automation drivers.",
        ("render", "render_automations"),
    ),
)


_SCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas")
_SCHEMA_SINGLES = (
    "items", "additionalProperties", "unevaluatedProperties", "not", "contains", "propertyNames",
    "if", "then", "else",
)
_SCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_NULL_SCHEMA = {"type": "null"}


def compact_input_schema(schema: Any) -> Any:
    """Drop the parts of a generated input schema that cost context but carry no meaning.

    Pydantic titles restate property names, and an optional ``X | None = None``
    field is spelled ``anyOf: [X, null]`` with ``default: null`` although leaving
    it out already means None. Arguments are validated against the Python
    signature, not this schema, so an explicit null is still accepted. A nullable
    field without a None default keeps its null branch.
    """
    if not isinstance(schema, dict):
        return schema
    node = {k: v for k, v in schema.items() if not (k == "title" and isinstance(v, str))}
    for key in _SCHEMA_MAPS:
        if isinstance(node.get(key), dict):
            node[key] = {name: compact_input_schema(sub) for name, sub in node[key].items()}
    for key in _SCHEMA_SINGLES:
        if isinstance(node.get(key), dict):
            node[key] = compact_input_schema(node[key])
    for key in _SCHEMA_LISTS:
        if isinstance(node.get(key), list):
            node[key] = [compact_input_schema(sub) for sub in node[key]]

    variants = node.get("anyOf")
    if "default" in node and node["default"] is None and isinstance(variants, list) and _NULL_SCHEMA in variants:
        rest = [variant for variant in variants if variant != _NULL_SCHEMA]
        if rest:
            del node["default"], node["anyOf"]
            if len(rest) == 1 and isinstance(rest[0], dict):
                node = {**rest[0], **node}
            else:
                node["anyOf"] = rest
    return node


def _tool_manager(server: Any) -> Any:
    manager = getattr(server, "_tool_manager", None) or getattr(server, "tool_manager", None)
    if manager is None:
        raise RuntimeError("FastMCP tool manager is unavailable")
    return manager


DEFAULT_TOOL_DOCS = "hybrid"


def tool_docs_mode() -> str:
    """How advertised tools are documented: MCP_TOOL_DOCS, else `[mcp] tool_docs`, else hybrid.

    - hybrid: PROGRESSIVE_DIRECT_TOOLS keep their full description and JSON Schema; every
      other tool is a one-line summary plus a Python-style signature.
    - signature: every tool is a summary plus signature (smallest).
    - complete: every tool keeps its full description and JSON Schema.

    In hybrid and signature, tool_help(name) returns any tool's full manual and schema.
    """
    return setting(("MCP_TOOL_DOCS", "THREEDSMAX_MCP_TOOL_DOCS"), "tool_docs", TOOL_DOCS, DEFAULT_TOOL_DOCS)


def compact_tool_docs() -> bool:
    """hybrid and signature leave expected_pid out of the advertised tools (named once instead)."""
    return tool_docs_mode() != "complete"


def summary_line(text: str | None) -> str:
    """The docstring's first non-empty line, like a binding reference entry."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


_PY_TYPES = {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "null": "None"}


def _py_type(schema: Any, refs: set[str]) -> str:
    if not isinstance(schema, dict) or not schema:
        return "Any"
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        refs.add(name)
        return name
    if "enum" in schema:
        return "|".join(repr(value) for value in schema["enum"])
    if "const" in schema:
        return repr(schema["const"])
    for key in ("anyOf", "oneOf"):
        if isinstance(schema.get(key), list):
            return "|".join(_py_type(sub, refs) for sub in schema[key])
    kind = schema.get("type")
    if isinstance(kind, list):
        return "|".join(_PY_TYPES.get(item, item) for item in kind)
    if kind == "array":
        return f"list[{_py_type(schema.get('items', {}), refs)}]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        return f"dict[str,{_py_type(extra, refs)}]" if isinstance(extra, dict) and extra else "dict"
    return _PY_TYPES.get(kind, "Any")


def _py_params(schema: dict[str, Any], refs: set[str], skip: tuple[str, ...] = ()) -> str:
    required = set(schema.get("required", []))
    rendered = []
    for name, prop in (schema.get("properties") or {}).items():
        if name in skip:
            continue
        text = f"{name}:{_py_type(prop, refs)}"
        if name not in required:
            text += f"={prop.get('default')!r}"
        rendered.append(text)
    return ", ".join(rendered)


def python_signature(schema: dict[str, Any]) -> str:
    """Render an input schema as a compact Python-style signature plus its nested models."""
    refs: set[str] = set()
    lines = [f"({_py_params(schema, refs, skip=('expected_pid',))})"]
    defs = schema.get("$defs") or {}
    done: set[str] = set()
    while refs - done:
        name = sorted(refs - done)[0]
        done.add(name)
        lines.append(f"{name}={{{_py_params(defs.get(name) or {}, refs)}}}")
    return "\n".join(lines)


def _forbid_unknown_arguments(tool: Any) -> None:
    """A free-form schema no longer constrains argument names, so refuse unknown ones."""
    model = tool.fn_metadata.arg_model
    if model.model_config.get("extra") != "forbid":
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)


def _drop_expected_pid(schema: dict[str, Any]) -> dict[str, Any]:
    """Leave expected_pid out of the advertised schema; calls still accept it."""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "expected_pid" not in properties:
        return schema
    schema = {**schema, "properties": {k: v for k, v in properties.items() if k != "expected_pid"}}
    if "required" in schema:
        schema["required"] = [name for name in schema["required"] if name != "expected_pid"]
    return schema


def _encode_signature(tool: Any) -> None:
    """Advertise a one-line summary plus Python signature; keep the full docs for tool_help.

    The signature carries the argument contract and the advertised schema only says "an
    object". Validation still runs against the Python signature, strictly.
    """
    signature = python_signature(tool.parameters)
    object.__setattr__(tool, "_full_description", tool.description)
    object.__setattr__(tool, "_full_schema", tool.parameters)
    object.__setattr__(tool, "_signature", signature)
    tool.description = f"{summary_line(tool.description)}\n{signature}"
    tool.parameters = {"type": "object"}
    _forbid_unknown_arguments(tool)


def compact_tool_schemas(server: Any, names: Iterable[str] | None = None, *, docs: bool = True) -> None:
    """Compact the advertised schemas of registered tools in place and apply tool_docs_mode().

    Idempotent. docs=False keeps full documentation, for registries that only describe
    tools on demand.
    """
    manager = _tool_manager(server)
    tools = manager.list_tools() if names is None else filter(None, map(manager.get_tool, names))
    mode = tool_docs_mode() if docs else "complete"
    for tool in tools:
        if getattr(tool, "_docs_encoded", False):
            continue
        tool.parameters = compact_input_schema(tool.parameters)
        if mode == "signature" or (mode == "hybrid" and tool.name not in PROGRESSIVE_DIRECT_TOOLS):
            _encode_signature(tool)
        elif mode == "hybrid":
            tool.parameters = _drop_expected_pid(tool.parameters)
        if mode != "complete":
            object.__setattr__(tool, "_docs_encoded", True)


def install_signature_error_hints(server: Any) -> None:
    """Answer invalid arguments to a signature-encoded tool with its signature and tool_help."""
    from mcp.server.fastmcp.exceptions import ToolError

    manager = _tool_manager(server)
    original = manager.call_tool

    async def call_tool(name: str, arguments: dict[str, Any], context: Any = None, convert_result: bool = False) -> Any:
        try:
            return await original(name, arguments, context=context, convert_result=convert_result)
        except ToolError as exc:
            tool = manager.get_tool(name)
            signature = getattr(tool, "_signature", None)
            if not signature:
                raise
            raise ToolError(
                f"{exc}\nSignature: {name}{signature}\nPass numbers, booleans, arrays and objects as JSON "
                f"values, not strings. Full manual and JSON schema: tool_help(name={name!r})."
            ) from exc

    manager.call_tool = call_tool


def register_tool_help(
    public_mcp: Any,
    before_call: Callable[[], None] | None = None,
    transport_provider: Callable[[], dict[str, Any] | None] | None = None,
) -> None:
    """Add tool_help(name), which returns a signature-encoded tool's full manual and schema."""
    manager = _tool_manager(public_mcp)

    def tool_help(name: str) -> dict[str, Any]:
        """Full manual and JSON input schema of one tool; listings show a summary and signature."""
        tool = manager.get_tool(name.strip())
        if tool is None:
            close = get_close_matches(name.strip(), [t.name for t in manager.list_tools()], n=3, cutoff=0.6)
            raise ValueError(f"Unknown tool {name!r}." + (f" Did you mean {', '.join(close)}?" if close else ""))
        return {
            "name": tool.name,
            "description": getattr(tool, "_full_description", tool.description),
            "input_schema": getattr(tool, "_full_schema", tool.parameters),
            "signature": getattr(tool, "_signature", None),
        }

    public_mcp.add_tool(
        make_structured_tool(tool_help, before_call=before_call, transport_provider=transport_provider),
        annotations={"readOnlyHint": True, "idempotentHint": True},
    )


def _decorated_tool_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the registered MCP name without importing a tool module."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        target = decorator.func
        if not (
            isinstance(target, ast.Attribute)
            and target.attr == "tool"
            and isinstance(target.value, ast.Name)
            and target.value.id == "mcp"
        ):
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    return keyword.value.value
        return node.name
    return None


def _module_tools(path: Path) -> tuple[tuple[str, str], ...]:
    """Read the ordered @mcp.tool() names and docstring summaries from one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = _decorated_tool_name(node)
        if name:
            found.append((node.lineno, name, " ".join((ast.get_docstring(node) or "").split())))
    found.sort()
    return tuple((name, doc) for _, name, doc in found)


def _module_tool_names(path: Path) -> tuple[str, ...]:
    """Read the ordered @mcp.tool() names from one Python source file."""
    return tuple(name for name, _ in _module_tools(path))


def _search_words(text: str) -> set[str]:
    """Lowercase search terms with stop words dropped and a plural 's' trimmed."""
    words = (word[:-1] if len(word) > 3 and word.endswith("s") else word for word in _WORD.findall(text.lower()))
    return {word for word in words if word not in _STOP_WORDS}


def _model_dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict(by_alias=True, exclude_none=True)
    return value


def _error_envelope(
    message: str,
    *,
    code: str,
    error_type: str = "ToolDiscoveryError",
    hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {
            "type": error_type,
            "message": message,
            "code": code,
            "retryable": False,
        },
    }
    if hint:
        payload["hint"] = hint
    return ToolEnvelope.model_validate(payload).model_dump(mode="json", exclude_none=True)


class ProgressiveToolCatalog:
    """Source-indexed catalog backed by a non-advertised FastMCP registry."""

    def __init__(
        self,
        *,
        package: str,
        tools_dir: Path,
        hidden_mcp: Any,
        allowed_modules: Iterable[str],
        toolsets: tuple[ToolsetSpec, ...] = TOOLSET_SPECS,
    ) -> None:
        self.package = package
        self.tools_dir = tools_dir
        self.hidden_mcp = hidden_mcp
        self.toolsets = toolsets
        self._allowed_modules = tuple(allowed_modules)
        self._module_tools_cache: dict[str, tuple[str, ...]] | None = None
        self._tool_modules_cache: dict[str, str] | None = None
        self._tool_docs: dict[str, str] = {}
        self._search_index: dict[str, tuple[set[str], set[str], set[str]]] | None = None
        self._validate_toolset_modules()

    def _validate_toolset_modules(self) -> None:
        assigned = [module for spec in self.toolsets for module in spec.modules]
        duplicates = sorted({module for module in assigned if assigned.count(module) > 1})
        missing = sorted(set(self._allowed_modules) - set(assigned))
        extra = sorted(set(assigned) - set(self._allowed_modules))
        if duplicates or missing or extra:
            raise RuntimeError(
                "Invalid progressive toolset map: "
                f"duplicates={duplicates}, missing={missing}, extra={extra}"
            )

    @property
    def _manager(self) -> Any:
        return _tool_manager(self.hidden_mcp)

    @property
    def module_tools(self) -> dict[str, tuple[str, ...]]:
        if self._module_tools_cache is None:
            indexed: dict[str, tuple[str, ...]] = {}
            for module in self._allowed_modules:
                path = self.tools_dir / f"{module}.py"
                if not path.is_file():
                    raise RuntimeError(f"Tool module source not found: {path}")
                tools = _module_tools(path)
                indexed[module] = tuple(name for name, _ in tools)
                self._tool_docs.update(tools)
            self._module_tools_cache = indexed
        return self._module_tools_cache

    @property
    def tool_modules(self) -> dict[str, str]:
        if self._tool_modules_cache is None:
            indexed: dict[str, str] = {}
            for module, names in self.module_tools.items():
                for name in names:
                    previous = indexed.get(name)
                    if previous is not None:
                        raise RuntimeError(
                            f"Tool {name!r} is declared by both {previous!r} and {module!r}"
                        )
                    indexed[name] = module
            self._tool_modules_cache = indexed
        return self._tool_modules_cache

    def _spec(self, name: str) -> ToolsetSpec | None:
        normalized = name.strip().lower()
        return next((spec for spec in self.toolsets if spec.name == normalized), None)

    def _registered_count(self, modules: Iterable[str]) -> int:
        return sum(
            1
            for module in modules
            for name in self.module_tools[module]
            if self._manager.get_tool(name) is not None
        )

    def list_toolsets(self) -> dict[str, Any]:
        toolsets: list[dict[str, Any]] = []
        for spec in self.toolsets:
            total = sum(len(self.module_tools[module]) for module in spec.modules)
            loaded = self._registered_count(spec.modules)
            toolsets.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "tools": [name for module in spec.modules for name in self.module_tools[module]],
                    "tool_count": total,
                    "loaded_tool_count": loaded,
                    "loaded": loaded == total,
                }
            )
        return {
            "profile": "progressive",
            "workflow": "find_tools(query) or describe_toolset(toolset, tools=[...]) -> call_tool",
            "tool_count": len(self.tool_modules),
            "toolsets": toolsets,
        }

    def _toolset_of(self, tool_name: str) -> ToolsetSpec:
        module = self.tool_modules[tool_name]
        return next(spec for spec in self.toolsets if module in spec.modules)

    def load_tools(self, names: Iterable[str], *, verify: bool = True) -> None:
        """Import the modules that declare these tools (unknown names raise KeyError).

        verify=False is for server startup: a module that is itself importing the server
        is only partly initialized and registers its tools when its import finishes.
        """
        for module in dict.fromkeys(self.tool_modules[name] for name in names):
            if verify:
                self._load_module(module)
            else:
                import_module(f".tools.{module}", package=self.package)

    def _describe(self, tool_name: str) -> dict[str, Any]:
        tool = self._manager.get_tool(tool_name)
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
            "annotations": _model_dump(tool.annotations),
        }

    def find_tools(self, query: str, limit: int = 3) -> dict[str, Any]:
        """Rank tools by name, docstring and toolset text; describe the best matches."""
        terms = _search_words(query)
        if not terms:
            raise ValueError("query needs at least one search word, e.g. 'boolean subtract' or 'spline knot'")
        if self._search_index is None:
            self._search_index = {}
            for name in self.tool_modules:
                spec = self._toolset_of(name)
                self._search_index[name] = (
                    _search_words(name.replace("_", " ")),
                    _search_words(self._tool_docs.get(name, "")),
                    _search_words(f"{spec.name} {spec.description}"),
                )
        scored: list[tuple[int, str]] = []
        for name, (name_words, doc_words, toolset_words) in self._search_index.items():
            score = 0
            for term in terms:
                if term in name_words:
                    score += 4
                elif len(term) >= 4 and any(word.startswith(term) or term.startswith(word) for word in name_words if len(word) >= 4):
                    score += 2
                if term in doc_words:
                    score += 1
                if term in toolset_words:
                    score += 1
            if score:
                scored.append((-score, name))
        ranked = [name for _, name in sorted(scored)]
        limit = max(1, min(int(limit), 10))
        best = ranked[:limit]
        self.load_tools(best)
        return {
            "query": query,
            "tools": [{**self._describe(name), "toolset": self._toolset_of(name).name} for name in best],
            "also_matching": ranked[limit:limit + 10],
            "next": (
                "call_tool(name=<tool>, arguments={...}) with arguments matching input_schema."
                if best else
                "No tool matched. Try other words, browse list_toolsets, or use execute_maxscript."
            ),
            "result_contract": _RESULT_CONTRACT,
        }

    def _load_module(self, module: str) -> None:
        expected = self.module_tools[module]
        if not all(self._manager.get_tool(name) is not None for name in expected):
            import_module(f".tools.{module}", package=self.package)
            missing = [name for name in expected if self._manager.get_tool(name) is None]
            if missing:
                raise RuntimeError(
                    f"Lazy-loaded module {module!r} did not register expected tools: {missing}"
                )
        # Also covers modules imported at startup, which may register after the server
        # finished importing; compaction is idempotent.
        # Hidden tools are described on demand, so they keep their full documentation.
        compact_tool_schemas(self.hidden_mcp, expected, docs=False)

    def describe_toolset(self, name: str, tools: Iterable[str] | None = None) -> dict[str, Any]:
        spec = self._spec(name)
        if spec is None:
            available = [item.name for item in self.toolsets]
            raise ValueError(f"Unknown toolset {name!r}. Available toolsets: {available}")

        members = [tool for module in spec.modules for tool in self.module_tools[module]]
        if tools is None:
            selected = members
        else:
            requested = {tool.strip() for tool in tools}
            unknown = sorted(requested - set(members))
            if unknown:
                raise ValueError(f"Toolset {spec.name!r} has no tools {unknown}. Its tools: {members}")
            selected = [tool for tool in members if tool in requested]

        self.load_tools(selected)
        described = [self._describe(tool_name) for tool_name in selected]
        return {
            "name": spec.name,
            "description": spec.description,
            "tool_count": len(described),
            "result_contract": _RESULT_CONTRACT,
            "tools": described,
        }

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and invoke one allowlisted hidden tool with its normal wrapper."""
        normalized = name.strip()
        if normalized in META_TOOL_NAMES:
            return _error_envelope(
                f"Meta-tool {normalized!r} cannot be dispatched through call_tool",
                code="BAD_PARAM",
                hint={"message": "Call discovery meta-tools directly."},
            )

        module = self.tool_modules.get(normalized)
        if module is None:
            close = get_close_matches(normalized, list(self.tool_modules), n=3, cutoff=0.6)
            message = "Find the tool with find_tools(query) and call it by its exact name."
            if close:
                message = f"Did you mean {', '.join(close)}? " + message
            return _error_envelope(
                f"Unknown or unavailable tool: {normalized!r}",
                code="NOT_FOUND",
                hint={"message": message, "suggested_tools": [*close, "find_tools"]},
            )

        active = _active_calls.get()
        if normalized in active:
            return _error_envelope(
                f"Recursive progressive dispatch rejected for {normalized!r}",
                code="BAD_PARAM",
                error_type="RecursiveToolCall",
            )

        token = _active_calls.set(active | {normalized})
        try:
            self._load_module(module)
            tool = self._manager.get_tool(normalized)
            if tool is None:  # pragma: no cover - guarded by _load_module
                return _error_envelope(
                    f"Tool {normalized!r} was not registered after loading {module!r}",
                    code="NOT_FOUND",
                )
            if getattr(tool, "is_async", False):
                return _error_envelope(
                    f"Async tool {normalized!r} is not supported by synchronous progressive dispatch",
                    code="BAD_PARAM",
                )
            if getattr(tool, "context_kwarg", None):
                return _error_envelope(
                    f"Context-injected tool {normalized!r} is not supported by progressive dispatch",
                    code="BAD_PARAM",
                )

            raw_arguments = arguments or {}
            metadata = tool.fn_metadata
            # The model writes `arguments` freehand, without schema-guided generation, so a
            # misspelled name would otherwise be dropped silently and the tool run on defaults.
            fields = metadata.arg_model.model_fields
            known = set(fields) | {field.alias for field in fields.values() if field.alias}
            unknown = sorted(set(raw_arguments) - known)
            try:
                if unknown:
                    raise ValueError(f"unknown argument(s) {unknown}")
                parsed = metadata.arg_model.model_validate(metadata.pre_parse_json(raw_arguments))
            except (ValidationError, ValueError) as exc:
                problems = (
                    "; ".join(f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}" for err in exc.errors())
                    if isinstance(exc, ValidationError) else str(exc)
                )
                return _error_envelope(
                    f"Invalid arguments for {normalized!r}: {problems}",
                    code="BAD_PARAM",
                    error_type="InvalidArguments",
                    hint={
                        "message": "Retry call_tool with arguments matching input_schema.",
                        "input_schema": tool.parameters,
                    },
                )
            kwargs = parsed.model_dump_one_level()

            # tool.fn is the same make_structured_tool wrapper used by eager
            # profiles, preserving safe-mode behavior, transport metadata,
            # hints, and the stable ToolEnvelope contract.
            result = tool.fn(**kwargs)
            return ToolEnvelope.model_validate(result).model_dump(mode="json", exclude_none=True)
        except Exception as exc:
            return _error_envelope(
                f"Could not invoke {normalized!r}: {exc}",
                code="BAD_PARAM",
                error_type=exc.__class__.__name__,
            )
        finally:
            _active_calls.reset(token)


def register_progressive_tools(
    *,
    public_mcp: Any,
    hidden_mcp: Any,
    package: str,
    tools_dir: Path,
    allowed_modules: Iterable[str],
    before_call: Callable[[], None] | None = None,
    transport_provider: Callable[[], dict[str, Any] | None] | None = None,
) -> ProgressiveToolCatalog:
    """Register the public discovery meta-tools and return their catalog."""
    catalog = ProgressiveToolCatalog(
        package=package,
        tools_dir=tools_dir,
        hidden_mcp=hidden_mcp,
        allowed_modules=allowed_modules,
    )

    def find_tools(query: str, limit: int = 3) -> dict[str, Any]:
        """Search the 3ds Max tools not listed directly, by what you need to do.

        Returns the best matches with their exact input schemas, e.g. query="boolean subtract",
        "spline knots", "tyflow birth" or "light intensity". Run one with call_tool.
        """
        return catalog.find_tools(query, limit)

    def list_toolsets() -> dict[str, Any]:
        """Browse every capability group and its tool names."""
        return catalog.list_toolsets()

    def describe_toolset(toolset: str, tools: list[str] | None = None) -> dict[str, Any]:
        """Return descriptions and exact input schemas for a toolset's tools.

        Pass tools=[...] with names from list_toolsets to describe only those; a whole
        group can be large.
        """
        return catalog.describe_toolset(toolset, tools)

    def call_tool(name: str, arguments: dict[str, Any] | None = None) -> ToolEnvelope:
        """Run a 3ds Max tool that is not listed directly, by its exact name.

        arguments must match the input_schema from find_tools or describe_toolset; invalid
        arguments return that schema. Mutating tools change the scene.
        """
        return catalog.call_tool(name, arguments)  # type: ignore[return-value]

    for function in (find_tools, list_toolsets, describe_toolset):
        public_mcp.add_tool(
            make_structured_tool(
                function,
                before_call=before_call,
                transport_provider=transport_provider,
            ),
            annotations={"readOnlyHint": True, "idempotentHint": True},
        )

    # call_tool already returns the forwarded operational ToolEnvelope, so it
    # must not be wrapped a second time.
    public_mcp.add_tool(
        call_tool,
        annotations={"destructiveHint": True},
    )
    return catalog


__all__ = [
    "META_TOOL_NAMES",
    "ProgressiveToolCatalog",
    "TOOLSET_SPECS",
    "ToolsetSpec",
    "PROGRESSIVE_DIRECT_TOOLS",
    "DEFAULT_TOOL_DOCS",
    "compact_input_schema",
    "compact_tool_docs",
    "compact_tool_schemas",
    "install_signature_error_hints",
    "python_signature",
    "register_progressive_tools",
    "register_tool_help",
    "summary_line",
    "tool_docs_mode",
]
