import logging
from functools import lru_cache
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from .max_client import MaxClient

logging.basicConfig(level=logging.INFO, format="%(message)s")

mcp = FastMCP("3dsmax-mcp")
client = MaxClient()

# Import tool modules to trigger @mcp.tool() registration
from .tools import execute, scene, objects, materials, render, viewport, identify, transform, hierarchy, modifiers, selection, clone, scene_manage, visibility, inspect, floor_plan, scene_query, effects, material_ops, material_replace, state_sets, data_channel, wire_params, controllers, scattering, capabilities, snapshots, session_context, bridge, plugins, tyflow, railclone, file_access, organize, learning, chat  # noqa: E402, F401


SKILL_RESOURCE_URI = "resource://3dsmax-mcp/skill"
SKILL_FILE = (
    Path(__file__).resolve().parent.parent / "skills" / "3dsmax-mcp-dev" / "SKILL.md"
)


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
    base_rules = (
        "You are a 3ds Max assistant connected via MCP.\n"
        "Use get_bridge_status if connection health or host state is uncertain.\n"
        "Start with get_scene_snapshot / get_selection_snapshot for fast live context.\n"
        "Use inspect_track_view to browse an object's animation/controller hierarchy before targeting a specific param_path.\n"
        "When working with plugins or unfamiliar classes, start with discover_plugin_surface or get_plugin_manifest.\n"
        "Use inspect_plugin_class before making assumptions about a plugin class surface.\n"
        "Use inspect_plugin_instance for live plugin objects when generic object inspection is too shallow.\n"
        "Plugin resources are available under resource://3dsmax-mcp/plugins/{plugin_name}/manifest, /guide, /recipes, and /gotchas.\n"
        "For tyFlow maintenance, inspect with get_tyflow_info first; enable include_flow_properties/include_event_properties/include_operator_properties for deep readback before edits.\n"
        "For tyFlow creation/mutation, use create_tyflow, modify_tyflow_operator, set_tyflow_shape, set_tyflow_physx, and get_tyflow_particles.\n"
        "For RailClone maintenance, use get_railclone_style_graph to read the exposed style graph (bases/segments/parameters) before edits.\n"
        "Prefer dedicated tools over raw MAXScript when available.\n"
        "Inspect objects/properties before edits.\n"
        "After any meaningful mutation, verify with get_scene_delta or re-inspect.\n"
        "Work in natural language with the user, but keep tool usage structured and explicit.\n"
        "DO NOT render unless the user asks.\n"
        "Use capture_viewport for fast viewport context.\n"
        f"Reference resource: {SKILL_RESOURCE_URI}\n"
    )
    return f"{base_rules}\nFull reference:\n\n{_read_skill_file()}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
