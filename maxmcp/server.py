import configparser
import logging
import os
import sys
from importlib import import_module
from functools import lru_cache
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from .max_client import MaxClient
from .tool_discovery import register_progressive_tools
from .tool_response import make_structured_tool

logging.basicConfig(level=logging.INFO, format="%(message)s")

mcp = FastMCP("3dsmax-mcp")
# Operational tools loaded by the progressive profile live here.  This server
# is never run, so its tools remain callable without being advertised by the
# public MCP list_tools surface.
_progressive_mcp = FastMCP("3dsmax-mcp-progressive-hidden")
client = MaxClient()

if __name__ == "__main__" and __spec__ is not None:
    sys.modules.setdefault(__spec__.name, sys.modules[__name__])


_READ_ONLY_TOOLS = {
    "get_bridge_status",
    "get_plugin_capabilities",
    "query_scene",
    "resolve_node_refs",
    "get_object_properties",
    "analyze_node_orientation",
    "inspect_mesh",
    "geometry_qa",
    "pick_component",
    "inspect_curve",
    "get_hierarchy",
    "get_instances",
    "get_dependencies",
    "get_materials",
    "get_material_library",
    "get_material_slots",
    "inspect_material_network",
    "inspect_object",
    "inspect_properties",
    "discover_plugin_surface",
    "get_plugin_manifest",
    "inspect_plugin_class",
    "inspect_plugin_constructor",
    "inspect_plugin_instance",
    "get_session_context",
    "capture_viewport",
    "capture_multi_view",
    "capture_screen",
    "get_effects",
    "get_state_sets",
    "get_camera_sequence",
    "inspect_track_view",
    "inspect_controller",
    "list_wireable_params",
    "get_wired_params",
    "inspect_max_file",
    "search_max_files",
    "batch_file_info",
    "watch_scene",
    "walk_references",
    "map_class_relationships",
    "learn_scene_patterns",
    "mcg_get_context",
    "mcg_list_graphs",
    "mcg_inspect_graph",
    "mcg_search_operators",
}

_DESTRUCTIVE_TOOLS = {
    "mesh_edit",
    "scene_patch",
    "scene_qa",
    "delete_objects",
    "collapse_modifier_stack",
    "batch_replace_materials",
    "replace_material",
    "remove_modifier",
    "batch_rename_objects",
    "manage_layers",
    "manage_groups",
    "manage_selection_sets",
    "merge_from_file",
    "render_cancel",
    "delete_effect",
    "undo_last",
    "mcg_cleanup_workspace",
    "mcg_reload_operators",
}

_IDEMPOTENT_TOOLS = {
    "get_bridge_status",
    "get_plugin_capabilities",
    "query_scene",
    "resolve_node_refs",
    "get_object_properties",
    "analyze_node_orientation",
    "get_hierarchy",
    "get_instances",
    "get_dependencies",
    "get_materials",
    "get_material_library",
    "get_material_slots",
    "inspect_material_network",
    "inspect_object",
    "inspect_properties",
    "get_session_context",
    "capture_viewport",
    "capture_multi_view",
    "capture_screen",
    "select_objects",
    "set_visibility",
    "mcg_get_context",
    "mcg_list_graphs",
    "mcg_inspect_graph",
    "mcg_search_operators",
}


def _tool_annotations(name: str) -> dict[str, bool]:
    annotations: dict[str, bool] = {}
    if name in _READ_ONLY_TOOLS or name.startswith(("get_", "inspect_", "discover_", "list_")):
        annotations["readOnlyHint"] = True
    if name in _DESTRUCTIVE_TOOLS:
        annotations["destructiveHint"] = True
    if name in _IDEMPOTENT_TOOLS:
        annotations["idempotentHint"] = True
    return annotations


def _register_raw_tool(raw_tool, wrapped, annotations: dict[str, bool]) -> None:
    if annotations:
        try:
            raw_tool(wrapped, annotations=annotations)
            return
        except TypeError:
            pass
    raw_tool(wrapped)


def _raw_tool_decorator(raw_tool, decorator_args, decorator_kwargs, annotations: dict[str, bool]):
    if annotations and "annotations" not in decorator_kwargs:
        annotated_kwargs = dict(decorator_kwargs)
        annotated_kwargs["annotations"] = annotations
        try:
            return raw_tool(*decorator_args, **annotated_kwargs)
        except TypeError:
            pass
    return raw_tool(*decorator_args, **decorator_kwargs)


def _install_structured_tool_results() -> None:
    """Register MCP tools with stable JSON envelopes while keeping raw callables."""
    raw_tool = mcp.tool
    progressive_raw_tool = _progressive_mcp.tool

    def registration_tool():
        return progressive_raw_tool if _tool_profile() == "progressive" else raw_tool

    def structured_tool(*decorator_args, **decorator_kwargs):
        if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1 and not decorator_kwargs:
            fn = decorator_args[0]
            annotations = _tool_annotations(getattr(fn, "__name__", "") or "")
            wrapped = make_structured_tool(
                fn,
                before_call=client.clear_last_response,
                transport_provider=client.get_last_transport,
            )
            _register_raw_tool(registration_tool(), wrapped, annotations)
            return fn

        def decorate(fn):
            annotations = _tool_annotations(getattr(fn, "__name__", "") or "")
            raw_decorator = _raw_tool_decorator(
                registration_tool(),
                decorator_args,
                decorator_kwargs,
                annotations,
            )
            wrapped = make_structured_tool(
                fn,
                before_call=client.clear_last_response,
                transport_provider=client.get_last_transport,
            )
            raw_decorator(wrapped)
            return fn

        return decorate

    mcp.tool = structured_tool  # type: ignore[method-assign]


_install_structured_tool_results()

CORE_TOOL_MODULES = (
    "execute",
    "bridge",
    "capabilities",
    "session_context",
    "query_scene",
    "scene_query",
    "scene_manage",
    "scene_patch",
    "scene_qa",
    "objects",
    "transform",
    "orientation",
    "hierarchy",
    "selection",
    "visibility",
    "clone",
    "modifiers",
    "booleans",
    "splines",
    "poly_edit",
    "mesh_ops",
    "geometry_qa",
    "component_pick",
    "loft",
    "curve_model",
    "curve_edit",
    "materials",
    "material_ops",
    "material_network",
    "palette_laydown",
    "smart_import",
    "material_replace",
    "inspect",
    "plugins",
    "organize",
    "viewport",
    "identify",
    "file_access",
    "learning",
    "controllers",
    "keyframes",
    "tool_test",
    "mainthread",
)

SPECIALTY_TOOL_MODULES = (
    "data_channel",
    "effects",
    "mcg",
    "railclone",
    "render",
    "render_automations",
    "scattering",
    "state_sets",
    "tyflow",
    "tyflow_graph",
    "tyflow_patch",
    "tyflow_manifest",
    "tyflow_census",
    "wire_params",
)


def _tool_profile() -> str:
    value = os.environ.get("MCP_TOOL_PROFILE") or os.environ.get("THREEDSMAX_MCP_TOOL_PROFILE")
    if value is not None:
        normalized = value.strip().lower()
        return normalized if normalized in {"core", "full", "progressive"} else "full"

    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        config_path = Path(local_appdata) / "3dsmax-mcp" / "mcp_config.ini"
        parser = configparser.ConfigParser()
        try:
            parser.read(config_path, encoding="utf-8")
            normalized = parser.get("mcp", "tool_profile", fallback="").strip().lower()
            if normalized in {"core", "full", "progressive"}:
                return normalized
        except (OSError, configparser.Error):
            pass
    return "full"


def _register_tool_modules() -> None:
    if _tool_profile() == "progressive":
        register_progressive_tools(
            public_mcp=mcp,
            hidden_mcp=_progressive_mcp,
            package=__package__,
            tools_dir=Path(__file__).resolve().parent / "tools",
            allowed_modules=CORE_TOOL_MODULES + SPECIALTY_TOOL_MODULES,
            before_call=client.clear_last_response,
            transport_provider=client.get_last_transport,
        )
        return

    modules = list(CORE_TOOL_MODULES)
    if _tool_profile() == "full":
        modules.extend(SPECIALTY_TOOL_MODULES)
    for name in modules:
        import_module(f".tools.{name}", package=__package__)


# Import tool modules to trigger @mcp.tool() registration. Default is full;
# set MCP_TOOL_PROFILE=core for everyday tools or progressive for the three
# lazy discovery/dispatch meta-tools.
_register_tool_modules()


SKILL_RESOURCE_URI = "resource://3dsmax-mcp/skill"


def _find_skill_file() -> Path:
    """Locate SKILL.md: bundled inside the package in a wheel, repo root in a checkout."""
    pkg = Path(__file__).resolve().parent
    relative = Path("skills") / "3dsmax-mcp-dev" / "SKILL.md"
    for base in (pkg, pkg.parent):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return pkg.parent / relative


SKILL_FILE = _find_skill_file()


@lru_cache(maxsize=1)
def _read_skill_file() -> str:
    """Read the local skill guide once and cache it for prompt/resource calls."""
    try:
        return SKILL_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        logging.warning("Skill file not found: %s", SKILL_FILE)
        return "Skill file not found."
    except OSError as exc:
        logging.warning("Could not read skill file %s: %s", SKILL_FILE, exc)
        return "Skill file could not be loaded."


@mcp.resource(SKILL_RESOURCE_URI)
def get_skill() -> str:
    """3ds Max MCP development guide exposed as an MCP resource."""
    return _read_skill_file()


@mcp.prompt()
def max_assistant() -> str:
    """Default assistant instructions for MCP clients like Claude Desktop."""
    scene_call_rule = (
        "For user requests about the live 3ds Max scene, call the MCP tool that matches the task directly.\n"
    )
    if _tool_profile() == "progressive":
        scene_call_rule = (
            "This server uses the progressive tool profile. Only list_toolsets, "
            "describe_toolset, and call_tool are advertised.\n"
            "Use list_toolsets to choose a capability group, describe_toolset to load its exact "
            "schemas, then invoke the selected operational tool through call_tool. Never call a "
            "hidden operational name as a top-level MCP tool.\n"
        )
    base_rules = (
        "You are a 3ds Max assistant connected via MCP.\n"
        f"{scene_call_rule}"
        "Do not inspect repository source files, run Python imports, or run repository tests for live scene requests unless the user explicitly asks for repo/debug/test work or direct MCP tools are unavailable.\n"
        "Do not call get_bridge_status or get_session_context as a session preamble or before every task.\n"
        "Use get_bridge_status only when a tool fails with a connection/transport error and you need to diagnose the bridge.\n"
        "Use query_scene(action=overview|filter|class|property|selection|delta) for scene reads.\n"
        "Use get_session_context only when the user explicitly wants bridge + capabilities + scene + selection in one call.\n"
        "Use inspect_track_view to browse an object's animation/controller hierarchy before targeting a specific param_path.\n"
        "When working with plugins or unfamiliar classes, use discover_plugin_surface or get_plugin_manifest.\n"
        "Use inspect_plugin_class before making assumptions about a plugin class surface.\n"
        "Use inspect_plugin_instance for live plugin objects when generic object inspection is too shallow.\n"
        "Plugin resources are available under resource://3dsmax-mcp/plugins/{plugin_name}/manifest, /guide, /recipes, and /gotchas.\n"
        "For tyFlow maintenance, inspect with get_tyflow_info first; enable include_flow_properties/include_event_properties/include_operator_properties for deep readback before edits.\n"
        "For tyFlow creation/mutation, use create_tyflow, modify_tyflow_operator, set_tyflow_shape, set_tyflow_physx, and get_tyflow_particles.\n"
        "For RailClone maintenance, use get_railclone_style_graph to read the exposed style graph (bases/segments/parameters) before edits.\n"
        "For Max Creation Graph work, use mcg_search_operators for typed ports, mcg_create_graph for a temp template fork, and mcg_apply_patch for the normal checkpoint-patch-compile-verify-rollback loop.\n"
        "MCG mutations use opaque graph_id values and expected hashes; never ask the user to copy, compile, run, or install a generated graph manually. Keep retries bounded to eight iterations and do not call mcg_reload_operators in the normal per-graph loop.\n"
        "Prefer dedicated tools over raw MAXScript when available.\n"
        "Inspect objects/properties before edits when you do not already have the needed data.\n"
        "After any meaningful mutation, verify with query_scene(action=delta) or re-inspect.\n"
        "Work in natural language with the user, but keep tool usage structured and explicit.\n"
        "DO NOT render unless the user asks.\n"
        "Use capture_viewport for fast viewport context.\n"
        "MCP tool replies are structured objects: `{ok, result}` on success, `{ok, error}` on failure, optional top-level `hint` (`message`, `suggested_tools`, `next`). Transport only when present on errors. Set MCP_TRIPBACK_MODE=full for elapsed_ms and full transport metadata.\n"
        "If ok is false, read error.message and any hint.suggested_tools before retrying or choosing a fallback.\n"
        f"Reference resource: {SKILL_RESOURCE_URI}\n"
        "Load the reference resource only when you need detailed project rules or MAXScript examples.\n"
    )
    return base_rules


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
