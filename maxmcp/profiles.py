"""Tool profiles: which tools an MCP client lists, and how they are documented.

Every tool is in the ToolRegistry whatever the profile, so a run_python script reaches all
of them as mx.tools.<name>(**arguments). The profile only decides what a client lists:

    script       run_python and the instance routing tools (the default)
    full         every tool
    core         every tool outside the specialty groups
    progressive  the routing tools, DIRECT_TOOLS, and find_tools, list_toolsets,
                 describe_toolset and call_tool, which reach the rest by name

MCP_TOOL_PROFILE, else `[mcp] tool_profile` in the user's mcp_config.ini, selects one.
Clients send every listed tool to the model with each request, so in full, core and
progressive `tool_docs` (MCP_TOOL_DOCS) decides how much of each tool is sent:

    hybrid     DIRECT_TOOLS keep their manual and JSON Schema; every other tool is its
               manual's first line and a Python-style signature (the default)
    signature  every tool is a first line and a signature
    complete   every tool keeps its manual and JSON Schema
"""

from __future__ import annotations

import inspect
from difflib import get_close_matches
from typing import Any, Callable

from .core.tool_response import ToolEnvelope, make_structured_tool
from .registry import ToolRegistry, signature_text, summary_line
from .user_config import TOOL_DOCS, TOOL_PROFILES, setting

ROUTING_MODULE = "system.routing"
SCRIPT_MODULE = "system.python_api"

# The tools most tasks use. progressive lists them beside the discovery tools, and hybrid
# docs keep their full manual. Keep the list small: each entry costs its schema on every
# model request.
DIRECT_TOOLS = frozenset({
    "query_scene", "inspect_object", "create_object", "transform_object", "set_object_property",
    "delete_objects", "clone_objects", "add_modifier", "set_modifier_property", "assign_material",
    "set_material_property", "execute_maxscript", "capture_viewport", "undo_last", "run_python",
})
DISCOVERY_TOOLS = ("find_tools", "list_toolsets", "describe_toolset", "call_tool")

# What core leaves out: plugin work, and the less common modules of the general groups.
_SPECIALTY_GROUPS = frozenset({"data_channel", "mcg", "pulze", "railclone", "scattering", "tyflow"})
_SPECIALTY_MODULES = frozenset({
    "animation.wire_params", "rendering.effects", "rendering.render_automations", "rendering.state_sets",
    "system.core_reload",  # Development: native core hot reload.
})

_READ_ONLY = frozenset({
    "cosmos_search", "pulze_inspect", "pulze_preflight", "material_roles", "query_scene", "resolve_node_refs",
    "analyze_node_orientation", "geometry_qa", "contact_check", "pick_component", "capture_viewport",
    "capture_multi_view", "capture_screen", "watch_scene", "walk_references", "map_class_relationships",
    "learn_scene_patterns", "search_max_files", "batch_file_info", "mcg_get_context", "mcg_list_graphs",
    "mcg_inspect_graph", "mcg_search_operators",
})
_READ_ONLY_PREFIXES = ("get_", "inspect_", "discover_", "list_")
_DESTRUCTIVE = frozenset({
    "mesh_edit", "pulze_set", "pulze_restore", "scene_patch", "scene_qa", "delete_objects",
    "collapse_modifier_stack", "batch_replace_materials", "replace_material", "remove_modifier",
    "batch_rename_objects", "manage_layers", "manage_groups", "manage_selection_sets", "merge_from_file",
    "delete_effect", "undo_last", "mcg_cleanup_workspace", "mcg_reload_operators",
})
_IDEMPOTENT = frozenset({
    "cosmos_search", "pulze_inspect", "pulze_preflight", "material_roles", "cosmos_download", "get_bridge_status",
    "get_plugin_capabilities", "query_scene", "resolve_node_refs", "get_object_properties",
    "analyze_node_orientation", "contact_check", "get_hierarchy", "get_instances", "get_dependencies",
    "get_materials", "get_material_library", "get_material_slots", "inspect_material_network", "inspect_object",
    "inspect_properties", "get_session_context", "capture_viewport", "capture_multi_view", "capture_screen",
    "select_objects", "set_visibility", "mcg_get_context", "mcg_list_graphs", "mcg_inspect_graph",
    "mcg_search_operators",
})

SCRIPT_INSTRUCTIONS = (
    "Work in 3ds Max by writing run_python scripts against the mx API; it is the only operational tool. "
    "mx.tools.<name>(**arguments) runs any of the ~180 3dsmax-mcp tools. mx.help() lists them by group, "
    "mx.find_tools('what you need') searches them, and mx.help(name) prints one tool's manual."
)
PROGRESSIVE_INSTRUCTIONS = (
    "Common 3ds Max tools are listed directly. For anything else, call find_tools with what you need "
    "(e.g. 'boolean subtract', 'spline knots', 'light intensity'), then run the match with "
    "call_tool(name, arguments). Never call a tool name that is not listed as a top-level tool."
)
_LISTED_INSTRUCTIONS = (
    "For work with more than one step (build, edit, measure, verify), write one run_python script "
    "with the mx API instead of calling tools one by one; use single tools for one-off reads or edits."
)
_SIGNATURE_INSTRUCTIONS = (
    "Tools listed with a one-line summary and a Python-style signature take arguments by those names; "
    "pass numbers, booleans, arrays and objects as JSON values, not strings. Call tool_help(name) for a "
    "tool's full manual and JSON schema when the signature is not enough."
)
_EXPECTED_PID_INSTRUCTIONS = (
    "Every tool except the instance routing tools also accepts expected_pid (int): the call is "
    "refused, with nothing sent, unless it reaches the 3ds Max with that PID."
)
_RESULT_CONTRACT = "ToolEnvelope: {ok,result} or {ok:false,error}; optional hint/transport."


def tool_hints(name: str) -> dict[str, bool]:
    """The MCP annotation hints of one tool, by its name."""
    hints: dict[str, bool] = {}
    if name in _READ_ONLY or name.startswith(_READ_ONLY_PREFIXES):
        hints["readOnlyHint"] = True
    if name in _DESTRUCTIVE:
        hints["destructiveHint"] = True
    if name in _IDEMPOTENT:
        hints["idempotentHint"] = True
    return hints


def tool_annotations(name: str) -> Any:
    """tool_hints as the ToolAnnotations FastMCP registers, or None without hints."""
    from mcp.types import ToolAnnotations

    hints = tool_hints(name)
    return ToolAnnotations(**hints) if hints else None


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


def _without_expected_pid(schema: dict[str, Any]) -> dict[str, Any]:
    """Leave expected_pid out of a listed schema; calls still accept it."""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "expected_pid" not in properties:
        return schema
    schema = {**schema, "properties": {k: v for k, v in properties.items() if k != "expected_pid"}}
    if "required" in schema:
        schema["required"] = [name for name in schema["required"] if name != "expected_pid"]
    return schema


def _forbid_unknown_arguments(tool: Any) -> None:
    """A free-form schema no longer constrains argument names, so refuse unknown ones."""
    model = tool.fn_metadata.arg_model
    if model.model_config.get("extra") != "forbid":
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)


def _error(message: str, code: str, hint: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {"type": "ToolDiscoveryError", "message": message, "code": code, "retryable": False},
    }
    if hint:
        payload["hint"] = hint
    return ToolEnvelope.model_validate(payload).model_dump(mode="json", exclude_none=True)


class ToolProfile:
    """One profile and its tool_docs mode, as the MCP server applies them."""

    def __init__(self, name: str, docs: str) -> None:
        self.name = name
        self.docs = docs
        # Tools listed as a signature: what tool_help returns for each.
        self._manuals: dict[str, dict[str, Any]] = {}

    @classmethod
    def configured(cls) -> ToolProfile:
        """The profile an environment variable or the user's mcp_config.ini selects."""
        return cls(
            setting(("MCP_TOOL_PROFILE", "THREEDSMAX_MCP_TOOL_PROFILE"), "tool_profile", TOOL_PROFILES),
            setting(("MCP_TOOL_DOCS", "THREEDSMAX_MCP_TOOL_DOCS"), "tool_docs", TOOL_DOCS),
        )

    @property
    def scripted(self) -> bool:
        """Whether run_python is the only operational tool a client lists."""
        return self.name == "script"

    @property
    def _compact(self) -> bool:
        """Whether listed tools are shortened, with expected_pid named once in the instructions."""
        return not self.scripted and self.docs != "complete"

    def lists(self, module: str, name: str) -> bool:
        """Whether an MCP client is shown the tool `name` that maxmcp/tools/<module>.py declares."""
        if module in (ROUTING_MODULE, SCRIPT_MODULE) or self.name == "full":
            return True
        if self.name == "core":
            return module.split(".")[0] not in _SPECIALTY_GROUPS and module not in _SPECIALTY_MODULES
        return self.name == "progressive" and name in DIRECT_TOOLS

    def startup_modules(self, registry: ToolRegistry) -> list[str]:
        """The modules of maxmcp/tools to import at startup, so that the tools a client lists exist."""
        modules = [ROUTING_MODULE, SCRIPT_MODULE]
        if not self.scripted:
            modules += [module for name, module in registry.index.items() if self.lists(module, name)]
        return list(dict.fromkeys(modules))

    def instructions(self) -> str:
        """The server instructions an MCP client hands the model."""
        if self.scripted:
            return SCRIPT_INSTRUCTIONS
        parts = [_LISTED_INSTRUCTIONS]
        if self.name == "progressive":
            parts.append(PROGRESSIVE_INSTRUCTIONS)
        elif self._compact:
            parts.append(_SIGNATURE_INSTRUCTIONS)
        if self._compact:
            parts.append(_EXPECTED_PID_INSTRUCTIONS)
        return " ".join(parts)

    def document(self, tool: Any, fn: Callable[..., Any]) -> None:
        """Shorten what a client is sent for one listed tool, as tool_docs asks.

        `tool` is the FastMCP tool and `fn` the function it was made from. A tool listed
        as a signature advertises `{"type": "object"}`; its arguments are still validated
        against the Python signature, strictly. The script profile lists five tools and
        leaves them as they are.
        """
        if self.scripted:
            return
        schema = compact_input_schema(tool.parameters)
        if self._compact and (self.docs == "signature" or tool.name not in DIRECT_TOOLS):
            signature = signature_text(fn)
            self._manuals[tool.name] = {
                "name": tool.name, "description": tool.description, "input_schema": schema, "signature": signature}
            tool.description = f"{summary_line(tool.description)[:200]}\n{signature}"
            schema = {"type": "object"}
            _forbid_unknown_arguments(tool)
        elif self._compact:
            schema = _without_expected_pid(schema)
        tool.parameters = schema

    def add_own_tools(
        self,
        mcp: Any,
        registry: ToolRegistry,
        *,
        before_call: Callable[[], None],
        transport_provider: Callable[[], dict[str, Any] | None],
    ) -> None:
        """List the tools a profile brings itself: the discovery tools, or tool_help."""
        if self.scripted:
            return
        manager = mcp._tool_manager

        def add(fn: Callable[..., Any], *, enveloped: bool = False, hints: dict[str, bool]) -> None:
            from mcp.types import ToolAnnotations

            listed = fn if enveloped else make_structured_tool(
                fn, before_call=before_call, transport_provider=transport_provider)
            mcp.add_tool(listed, annotations=ToolAnnotations(**hints))
            self.document(manager.get_tool(fn.__name__), fn)

        read_only = {"readOnlyHint": True, "idempotentHint": True}
        if self.name == "progressive":
            find_tools, list_toolsets, describe_toolset, call_tool = _discovery_tools(registry, manager)
            for fn in (find_tools, list_toolsets, describe_toolset):
                add(fn, hints=read_only)
            # call_tool returns the called tool's own ToolEnvelope, so it is not wrapped in a second one.
            add(call_tool, enveloped=True, hints={"destructiveHint": True})
        elif self._compact:
            add(self._tool_help(manager), hints=read_only)
        if self._compact:
            self._answer_bad_arguments_with_the_signature(manager)

    def _tool_help(self, manager: Any) -> Callable[..., dict[str, Any]]:
        manuals = self._manuals

        def tool_help(name: str) -> dict[str, Any]:
            """Full manual and JSON input schema of one tool; listings show a summary and signature."""
            name = name.strip()
            if name in manuals:
                return manuals[name]
            tool = manager.get_tool(name)
            if tool is None:
                close = get_close_matches(name, [listed.name for listed in manager.list_tools()], n=3, cutoff=0.6)
                raise ValueError(f"Unknown tool {name!r}." + (f" Did you mean {', '.join(close)}?" if close else ""))
            return {"name": tool.name, "description": tool.description, "input_schema": tool.parameters,
                    "signature": None}

        return tool_help

    def _answer_bad_arguments_with_the_signature(self, manager: Any) -> None:
        """A signature tool has no schema to guide the model, so a refused call repeats the signature."""
        from mcp.server.fastmcp.exceptions import ToolError

        original = manager.call_tool
        manuals = self._manuals
        manual_from = (
            "describe_toolset(toolset, tools=[{name!r}])" if self.name == "progressive" else "tool_help(name={name!r})")

        async def call_tool(name: str, arguments: dict[str, Any], context: Any = None,
                            convert_result: bool = False) -> Any:
            try:
                return await original(name, arguments, context=context, convert_result=convert_result)
            except ToolError as exc:
                if name not in manuals:
                    raise
                raise ToolError(
                    f"{exc}\nSignature: {name}{manuals[name]['signature']}\nPass numbers, booleans, arrays and "
                    f"objects as JSON values, not strings. Full manual and JSON schema: "
                    f"{manual_from.format(name=name)}."
                ) from exc

        manager.call_tool = call_tool


def _discovery_tools(registry: ToolRegistry, manager: Any) -> tuple[Callable[..., Any], ...]:
    """find_tools, list_toolsets, describe_toolset and call_tool over the registry.

    A toolset is a group of the registry: one folder of maxmcp/tools. The tools they reach
    stay out of tools/list, so a client's tool list does not change after discovery.
    """
    described: dict[str, dict[str, Any]] = {}

    def describe(name: str) -> dict[str, Any]:
        """One tool's manual and exact input schema, with expected_pid where the tool takes it."""
        if name not in described:
            from mcp.server.fastmcp.tools import Tool

            fn = registry.function(name)
            listed = Tool.from_function(make_structured_tool(fn, pid_guard=registry.index[name] != ROUTING_MODULE))
            entry = {"name": name, "description": inspect.getdoc(fn) or "",
                     "input_schema": compact_input_schema(listed.parameters)}
            if tool_hints(name):
                entry["annotations"] = tool_hints(name)
            described[name] = entry
        return described[name]

    def find_tools(query: str, limit: int = 3) -> dict[str, Any]:
        """Search the 3ds Max tools not listed directly, by what you need to do.

        Returns the best matches with their exact input schemas, e.g. query="boolean subtract",
        "spline knots", "tyflow birth" or "light intensity". Run one with call_tool.
        """
        ranked = registry.search(query)
        limit = max(1, min(int(limit), 10))
        best = ranked[:limit]
        return {
            "query": query,
            "tools": [{**describe(name), "toolset": registry.index[name].split(".")[0]} for name in best],
            "also_matching": ranked[limit:limit + 10],
            "next": (
                "call_tool(name=<tool>, arguments={...}) with arguments matching input_schema."
                if best else
                "No tool matched. Try other words, browse list_toolsets, or use execute_maxscript."
            ),
            "result_contract": _RESULT_CONTRACT,
        }

    def list_toolsets() -> dict[str, Any]:
        """Browse every capability group and its tool names."""
        return {
            "profile": "progressive",
            "workflow": "find_tools(query) or describe_toolset(toolset, tools=[...]) -> call_tool",
            "tool_count": len(registry.index),
            "toolsets": [
                {"name": group, "description": description, "tools": list(names)}
                for group, (description, names) in registry.groups().items()
            ],
        }

    def describe_toolset(toolset: str, tools: list[str] | None = None) -> dict[str, Any]:
        """Return descriptions and exact input schemas for a toolset's tools.

        Pass tools=[...] with names from list_toolsets to describe only those; a whole
        group can be large.
        """
        groups = registry.groups()
        group = toolset.strip().lower()
        if group not in groups:
            raise ValueError(f"Unknown toolset {toolset!r}. Available toolsets: {list(groups)}")
        description, members = groups[group]
        selected = list(members)
        if tools is not None:
            requested = {name.strip() for name in tools}
            unknown = sorted(requested - set(members))
            if unknown:
                raise ValueError(f"Toolset {group!r} has no tools {unknown}. Its tools: {list(members)}")
            selected = [name for name in members if name in requested]
        return {
            "name": group,
            "description": description,
            "tool_count": len(selected),
            "result_contract": _RESULT_CONTRACT,
            "tools": [describe(name) for name in selected],
        }

    def call_tool(name: str, arguments: dict[str, Any] | None = None) -> ToolEnvelope:
        """Run a 3ds Max tool that is not listed directly, by its exact name.

        arguments must match the input_schema from find_tools or describe_toolset; invalid
        arguments are refused with the tool's signature. Mutating tools change the scene.
        """
        name = name.strip()
        if name not in registry.index:
            if manager.get_tool(name) is not None:  # run_python and the discovery tools themselves
                return _error(  # type: ignore[return-value]
                    f"{name!r} is a top-level tool and cannot be run through call_tool", "BAD_PARAM",
                    {"message": "Call it directly."})
            close = get_close_matches(name, list(registry.index), n=3, cutoff=0.6)
            message = "Find the tool with find_tools(query) and call it by its exact name."
            if close:
                message = f"Did you mean {', '.join(close)}? " + message
            return _error(  # type: ignore[return-value]
                f"Unknown or unavailable tool: {name!r}", "NOT_FOUND",
                {"message": message, "suggested_tools": [*close, "find_tools"]})
        return registry.call(name, arguments)  # type: ignore[return-value]

    return find_tools, list_toolsets, describe_toolset, call_tool
