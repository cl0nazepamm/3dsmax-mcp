"""Progressive MCP tool discovery and lazy dispatch.

The progressive profile keeps the public MCP surface to three meta-tools.  Tool
modules are indexed from source without importing them, then imported into a
private FastMCP registry only when a toolset is described or a tool is called.
"""

from __future__ import annotations

import ast
from contextvars import ContextVar
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Iterable

from .tool_response import ToolEnvelope, make_structured_tool


META_TOOL_NAMES = frozenset({"list_toolsets", "describe_toolset", "call_tool"})
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
        "connection",
        "Bridge diagnostics, installed capabilities, session context, and main-thread checks.",
        ("bridge", "capabilities", "session_context", "mainthread"),
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
        ("modifiers", "booleans", "splines", "poly_edit", "mesh_ops", "geometry_qa", "component_pick", "loft", "curve_model", "curve_edit"),
    ),
    ToolsetSpec(
        "materials",
        "Materials, texture maps, material networks, palettes, replacement, and smart import.",
        (
            "materials",
            "material_ops",
            "material_network",
            "palette_laydown",
            "smart_import",
            "material_replace",
        ),
    ),
    ToolsetSpec(
        "inspection",
        "Object/property inspection, plugin introspection, and reference learning tools.",
        ("inspect", "plugins", "learning"),
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
        "Raw MAXScript fallback plus direct invocation and smoke-test drivers.",
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
        "tyFlow creation, graph patching, manifests, wiring, census, and simulation reads.",
        ("tyflow", "tyflow_graph", "tyflow_patch", "tyflow_manifest", "tyflow_census"),
    ),
    ToolsetSpec(
        "railclone",
        "RailClone style graph inspection and exact parameter edits.",
        ("railclone",),
    ),
    ToolsetSpec(
        "scattering",
        "Forest Pack scattering workflows.",
        ("scattering",),
    ),
    ToolsetSpec(
        "floor_plan",
        "Grid-defined floor-plan generation.",
        ("floor_plan",),
    ),
    ToolsetSpec(
        "scene_effects",
        "Scene effects, state sets, and camera sequence reads.",
        ("effects", "state_sets"),
    ),
    ToolsetSpec(
        "rendering",
        "Render configuration and render automation drivers.",
        ("render", "render_automations"),
    ),
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


def _module_tool_names(path: Path) -> tuple[str, ...]:
    """Read the ordered @mcp.tool() names from one Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = _decorated_tool_name(node)
        if name:
            found.append((node.lineno, name))
    found.sort()
    return tuple(name for _, name in found)


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
        manager = getattr(self.hidden_mcp, "_tool_manager", None) or getattr(
            self.hidden_mcp, "tool_manager", None
        )
        if manager is None:
            raise RuntimeError("FastMCP tool manager is unavailable")
        return manager

    @property
    def module_tools(self) -> dict[str, tuple[str, ...]]:
        if self._module_tools_cache is None:
            indexed: dict[str, tuple[str, ...]] = {}
            for module in self._allowed_modules:
                path = self.tools_dir / f"{module}.py"
                if not path.is_file():
                    raise RuntimeError(f"Tool module source not found: {path}")
                indexed[module] = _module_tool_names(path)
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
                    "tool_count": total,
                    "loaded_tool_count": loaded,
                    "loaded": loaded == total,
                }
            )
        return {
            "profile": "progressive",
            "workflow": "list_toolsets -> describe_toolset -> call_tool",
            "tool_count": len(self.tool_modules),
            "toolsets": toolsets,
        }

    def _load_module(self, module: str) -> None:
        expected = self.module_tools[module]
        if all(self._manager.get_tool(name) is not None for name in expected):
            return
        import_module(f".tools.{module}", package=self.package)
        missing = [name for name in expected if self._manager.get_tool(name) is None]
        if missing:
            raise RuntimeError(
                f"Lazy-loaded module {module!r} did not register expected tools: {missing}"
            )

    def describe_toolset(self, name: str) -> dict[str, Any]:
        spec = self._spec(name)
        if spec is None:
            available = [item.name for item in self.toolsets]
            raise ValueError(f"Unknown toolset {name!r}. Available toolsets: {available}")

        for module in spec.modules:
            self._load_module(module)

        tools: list[dict[str, Any]] = []
        for module in spec.modules:
            for tool_name in self.module_tools[module]:
                tool = self._manager.get_tool(tool_name)
                if tool is None:  # pragma: no cover - guarded by _load_module
                    continue
                tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.parameters,
                        "annotations": _model_dump(tool.annotations),
                    }
                )

        return {
            "name": spec.name,
            "description": spec.description,
            "tool_count": len(tools),
            "result_contract": "ToolEnvelope: {ok,result} or {ok:false,error}; optional hint/transport.",
            "tools": tools,
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
            return _error_envelope(
                f"Unknown or unavailable tool: {normalized!r}",
                code="NOT_FOUND",
                hint={
                    "message": "Choose an allowlisted tool from describe_toolset.",
                    "suggested_tools": ["list_toolsets", "describe_toolset"],
                },
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
            prepared = metadata.pre_parse_json(raw_arguments)
            parsed = metadata.arg_model.model_validate(prepared)
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
    """Register the three compact public meta-tools and return their catalog."""
    catalog = ProgressiveToolCatalog(
        package=package,
        tools_dir=tools_dir,
        hidden_mcp=hidden_mcp,
        allowed_modules=allowed_modules,
    )

    def list_toolsets() -> dict[str, Any]:
        """List compact capability groups. Describe one before calling an operational tool."""
        return catalog.list_toolsets()

    def describe_toolset(toolset: str) -> dict[str, Any]:
        """Load one toolset and return its tool names, descriptions, and exact input schemas."""
        return catalog.describe_toolset(toolset)

    def call_tool(name: str, arguments: dict[str, Any] | None = None) -> ToolEnvelope:
        """Call one allowlisted operational tool by name with its exact argument object.

        This proxy can mutate the scene when the selected tool is mutating. Unknown
        names, meta-tool recursion, and recursive dispatch are rejected.
        """
        return catalog.call_tool(name, arguments)  # type: ignore[return-value]

    for function in (list_toolsets, describe_toolset):
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
    "register_progressive_tools",
]
