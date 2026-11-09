"""The 3dsmax-mcp MCP server.

MCP clients see five tools: run_python, which runs a script against the mx API, and the
four that choose which 3ds Max the server talks to. Every other tool is a Python
function in maxmcp/tools, declared with @mcp.tool() and called from scripts as
mx.tools.<name>(**arguments).
"""

import logging
import os
import sys
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .max_client import MaxClient
from .registry import ToolRegistry
from .tool_response import make_structured_tool

logging.basicConfig(level=logging.INFO, format="%(message)s")

_INSTRUCTIONS = (
    "Work in 3ds Max by writing run_python scripts against the mx API; it is the only operational tool. "
    "mx.tools.<name>(**arguments) runs any of the ~180 3dsmax-mcp tools and mx.help(name) prints its manual."
)

mcp = FastMCP("3dsmax-mcp", instructions=_INSTRUCTIONS)
client = MaxClient()
# Every tool function by name, for mx. run_python itself is not callable from a script.
registry = ToolRegistry(client, skip=("python_api",))

if __name__ == "__main__" and __spec__ is not None:
    sys.modules.setdefault(__spec__.name, sys.modules[__name__])

# The modules whose tools MCP clients see.
_ADVERTISED_MODULES = ("routing", "python_api")
_READ_ONLY_TOOLS = frozenset({"list_max_instances", "get_selected_max_instance"})


def _install_tool_decorator() -> None:
    """Make @mcp.tool() add a function to the registry, and advertise it only from _ADVERTISED_MODULES."""
    advertise = mcp.tool

    def register(fn: Any, name: str | None = None) -> Any:
        registry.add(fn, name)
        module = fn.__module__.rsplit(".", 1)[-1]
        if module in _ADVERTISED_MODULES:
            wrapped = make_structured_tool(
                fn,
                before_call=client.clear_last_response,
                transport_provider=client.get_last_transport,
                # The routing tools pick the target, so they take no expected_pid.
                pid_guard=module != "routing",
            )
            read_only = (name or fn.__name__) in _READ_ONLY_TOOLS
            advertise(name=name, annotations=ToolAnnotations(readOnlyHint=True) if read_only else None)(wrapped)
        return fn

    def tool(*args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return register(args[0])
        return lambda fn: register(fn, kwargs.get("name"))

    mcp.tool = tool  # type: ignore[method-assign]


_install_tool_decorator()
for _module in _ADVERTISED_MODULES:
    import_module(f".tools.{_module}", package=__package__)
# Its @mcp.resource() entries (plugin manifests and guides) exist once it is imported.
import_module(".tools.plugins", package=__package__)


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
    return (
        "You are a 3ds Max assistant connected via MCP.\n"
        f"{_INSTRUCTIONS}\n"
        "The tool names in the rules below are not top-level MCP tools: call each one inside a run_python "
        "script as mx.tools.<name>(**arguments).\n"
        "Do not inspect repository source files, run Python imports, or run repository tests for live scene requests unless the user explicitly asks for repo/debug/test work or run_python is unavailable.\n"
        "Do not call get_bridge_status or get_session_context as a session preamble or before every task.\n"
        "Use get_bridge_status only when a call fails with a connection/transport error and you need to diagnose the bridge.\n"
        "Use query_scene(action=overview|filter|class|property|selection|delta) for scene reads.\n"
        "Use get_session_context only when the user explicitly wants bridge + capabilities + scene + selection in one call.\n"
        "Use inspect_track_view to browse an object's animation/controller hierarchy before targeting a specific param_path.\n"
        "When working with plugins or unfamiliar classes, use discover_plugin_surface or get_plugin_manifest.\n"
        "Use inspect_plugin_class before making assumptions about a plugin class surface.\n"
        "Use inspect_plugin_instance for live plugin objects when generic object inspection is too shallow.\n"
        "Plugin resources are available under resource://3dsmax-mcp/plugins/{plugin_name}/manifest, /guide, /recipes, and /gotchas.\n"
        "For tyFlow maintenance, inspect with get_tyflow_info first; enable include_flow_properties/include_event_properties/include_operator_properties for deep readback before edits.\n"
        "For tyFlow creation/mutation, use create_tyflow, modify_tyflow_operator, set_tyflow_shape, set_tyflow_physx, and get_tyflow_particles.\n"
        "For RailClone 7.3.5+, read get_railclone_style, replace XML with set_railclone_style using expected_style, then verify get_railclone_output.\n"
        "For Max Creation Graph work, use mcg_search_operators for typed ports, mcg_create_graph for a temp template fork, and mcg_apply_patch for the normal checkpoint-patch-compile-verify-rollback loop.\n"
        "MCG mutations use opaque graph_id values and expected hashes; never ask the user to copy, compile, run, or install a generated graph manually. Keep retries bounded to eight iterations and do not call mcg_reload_operators in the normal per-graph loop.\n"
        "Prefer dedicated tools over raw MAXScript when available.\n"
        "Inspect objects/properties before edits when you do not already have the needed data.\n"
        "After any meaningful mutation, verify with query_scene(action=delta) or re-inspect.\n"
        "Work in natural language with the user, but keep scripts explicit about what they read and change.\n"
        "DO NOT render unless the user asks.\n"
        "Use capture_viewport for fast viewport context.\n"
        "run_python replies are structured objects: `{ok, result}` on success, `{ok, error}` on failure. A tool call that fails inside a script raises mx.MaxToolError (.code, .message, .hint) and ends the script unless the script catches it. Set MCP_TRIPBACK_MODE=full for elapsed_ms and full transport metadata.\n"
        "If ok is false, read error.message and error.details (script line, tool, code, hint) before retrying or choosing a fallback.\n"
        f"Reference resource: {SKILL_RESOURCE_URI}\n"
        "Load the reference resource only when you need detailed project rules or MAXScript examples.\n"
    )


def main():
    from .parent_watchdog import start_parent_watchdog

    start_parent_watchdog()
    mcp.run(transport="stdio")
    # Client closed stdin: exit even if non-daemon threads linger, but never
    # while a diagnostics capture holds a Max thread suspended.
    try:
        from .suspend_guard import release_all

        release_all()
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:
                pass
    finally:
        os._exit(0)


if __name__ == "__main__":
    main()
