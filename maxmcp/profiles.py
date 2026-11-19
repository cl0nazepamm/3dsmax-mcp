"""Tool profiles: which tools an MCP client lists, and how they are documented.

Every tool is in the ToolRegistry whatever the profile, so a run_python script reaches all
of them as mx.tools.<name>(**arguments). The profile only decides what a client lists:

    code   run_python and the instance routing tools (the default)
    full   every tool

MCP_TOOL_PROFILE, else `[mcp] tool_profile` in the user's mcp_config.ini, selects one.
Clients send every listed tool to the model with each request, so in full `tool_docs`
(MCP_TOOL_DOCS) decides how much of each tool is sent:

    hybrid     DIRECT_TOOLS keep their manual and JSON Schema; every other tool is its
               manual's first line and a Python-style signature (the default)
    signature  every tool is a first line and a signature
    complete   every tool keeps its manual and JSON Schema
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any, Callable

from .core.tool_response import make_structured_tool
from .registry import ToolRegistry, signature_text, summary_line
from .user_config import TOOL_DOCS, TOOL_PROFILES, setting

ROUTING_MODULE = "system.routing"
SCRIPT_MODULE = "system.python_api"

# The tools most tasks use: hybrid docs keep their full manual. Keep the list small: each
# entry costs its schema on every model request.
DIRECT_TOOLS = frozenset({
    "query_scene", "inspect_object", "create_object", "transform_object", "set_object_property",
    "delete_objects", "clone_objects", "add_modifier", "set_modifier_property", "assign_material",
    "set_material_property", "execute_maxscript", "capture_viewport", "undo_last", "run_python",
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
        return self.name == "code"

    @property
    def _compact(self) -> bool:
        """Whether listed tools are shortened, with expected_pid named once in the instructions."""
        return not self.scripted and self.docs != "complete"

    def lists(self, module: str) -> bool:
        """Whether an MCP client is shown the tools that maxmcp/tools/<module>.py declares."""
        return module in (ROUTING_MODULE, SCRIPT_MODULE) or not self.scripted

    def startup_modules(self, registry: ToolRegistry) -> list[str]:
        """The modules of maxmcp/tools to import at startup, so that the tools a client lists exist."""
        modules = [ROUTING_MODULE, SCRIPT_MODULE]
        if not self.scripted:
            modules += registry.index.values()
        return list(dict.fromkeys(modules))

    def instructions(self) -> str:
        """The server instructions an MCP client hands the model."""
        if self.scripted:
            return SCRIPT_INSTRUCTIONS
        parts = [_LISTED_INSTRUCTIONS]
        if self._compact:
            parts += [_SIGNATURE_INSTRUCTIONS, _EXPECTED_PID_INSTRUCTIONS]
        return " ".join(parts)

    def document(self, tool: Any, fn: Callable[..., Any]) -> None:
        """Shorten what a client is sent for one listed tool, as tool_docs asks.

        `tool` is the FastMCP tool and `fn` the function it was made from. A tool listed
        as a signature advertises `{"type": "object"}`; its arguments are still validated
        against the Python signature, strictly. The code profile lists five tools and
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
        *,
        before_call: Callable[[], None],
        transport_provider: Callable[[], dict[str, Any] | None],
    ) -> None:
        """List tool_help, which a profile that shortens its listings brings itself."""
        if not self._compact:
            return
        from mcp.types import ToolAnnotations

        manager = mcp._tool_manager
        tool_help = self._tool_help(manager)
        mcp.add_tool(make_structured_tool(tool_help, before_call=before_call, transport_provider=transport_provider),
                     annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
        self.document(manager.get_tool(tool_help.__name__), tool_help)
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

        async def call_tool(name: str, arguments: dict[str, Any], context: Any = None,
                            convert_result: bool = False) -> Any:
            try:
                return await original(name, arguments, context=context, convert_result=convert_result)
            except ToolError as exc:
                if name not in manuals:
                    raise
                raise ToolError(
                    f"{exc}\nSignature: {name}{manuals[name]['signature']}\nPass numbers, booleans, arrays and "
                    f"objects as JSON values, not strings. Full manual and JSON schema: tool_help(name={name!r})."
                ) from exc

        manager.call_tool = call_tool
