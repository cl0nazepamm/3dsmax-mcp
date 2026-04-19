# 3dsmax-mcp

<p align="left">
  <img src="images/logo.png" alt="3dsmax-mcp logo" width="200">
</p>

MCP server that connects AI agents to Autodesk 3ds Max.
Works with Claude Code, Claude Desktop, Codex, Gemini, and any MCP-compatible client.

### What's new in 0.5.0

- **Native C++ Bridge** — 76 handlers running inside 3ds Max as a GUP plugin, 86-130x faster than MAXScript
- **One-step installer** — `uv run python install.py` handles everything
- **Multi-view capture** — pure SDK viewport switching, no MAXScript re-entrancy
- **Controller & wiring tools** — assign controllers, wire parameters, inspect track views
- **PB1 fallback** — legacy primitives (Capsule, Hedra, etc.) now get correct params
- **110 tools** across scene, objects, materials, modifiers, controllers, viewport, introspection.
- **Bundled MAXScript reference** — 10 topic files for agents to write correct MAXScript

## Architecture

```
Agent  <-->  FastMCP (Python/stdio)  <-->  Named Pipe  <-->  C++ GUP Plugin  <-->  3ds Max SDK
                                      |
                                      +--> TCP:8765 fallback --> MAXScript listener
```

The native bridge runs inside 3ds Max as a Global Utility Plugin. It reads the scene graph directly through the C++ SDK and communicates over Windows named pipes. 76 native handlers for scene, objects, materials, modifiers, controllers, viewport, introspection, and more.

## Requirements

- [Python 3.10+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/)
- Autodesk 3ds Max 2026 (2024/2025 supported via MAXScript fallback)

## Installation

```powershell
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
uv sync
uv run python install.py
```

The installer will:
- Detect your 3ds Max installation
- Deploy the native bridge plugin (`.gup`)
- Install the MAXScript listener (TCP fallback)
- Build skill files for your agents
- Register with Claude Code / Codex / Gemini / Claude Desktop

Restart 3ds Max and any running agents after installation.

### Manual registration

If the installer can't find your agent, register manually:

**Claude Code / Codex / Gemini:**
```powershell
claude mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
```

**Claude Desktop** — add to `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\3dsmax-mcp", "3dsmax-mcp"]
    }
  }
}
```

## Updating

```powershell
git pull
uv sync
uv run python install.py
```

## Skill file

The skill file teaches agents how to use the tools, what pitfalls to avoid, and how 3ds Max works. Without it, agents will guess wrong on material workflows, controller paths, and plugin APIs. The installer builds and deploys it automatically.

If you need to rebuild manually:
```powershell
python scripts/build_skill.py
```

## Safe mode

Both the native bridge and the MAXScript listener read from a shared config:

```
%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
```

```ini
[mcp]
safe_mode = true
```

When enabled (default), these commands are blocked:
`DOSCommand`, `ShellLaunch`, `deleteFile`, `python.Execute`, `createFile`

To disable, set `safe_mode = false` and restart 3ds Max.

## Tools

110 tools across scene management, objects, materials, modifiers, controllers, wiring, viewport capture, file access, plugin introspection, tyFlow, Forest Pack, RailClone, Data Channel, and more.

| Category | Tools | Transport |
|----------|-------|-----------|
| Scene reads | `get_scene_info`, `get_selection`, `get_scene_snapshot`, `get_selection_snapshot`, `get_scene_delta`, `get_hierarchy` | C++ |
| Objects | `create_object`, `delete_objects`, `transform_object`, `clone_objects`, `select_objects`, `set_object_property`, `set_visibility`, `set_parent` | C++/Hybrid |
| Inspection | `inspect_object`, `inspect_properties`, `introspect_class`, `introspect_instance`, `walk_references`, `learn_scene_patterns`, `map_class_relationships` | C++ |
| Materials | `assign_material`, `set_material_properties`, `get_material_slots`, `create_texture_map`, `write_osl_shader`, `create_shell_material`, `replace_material` | Hybrid |
| Modifiers | `add_modifier`, `remove_modifier`, `set_modifier_state`, `collapse_modifier_stack`, `batch_modify` | Hybrid |
| Controllers | `assign_controller`, `inspect_controller`, `inspect_track_view`, `set_controller_props`, `add_controller_target` | Hybrid |
| Wiring | `wire_params`, `unwire_params`, `get_wired_params`, `list_wireable_params` | Hybrid |
| Viewport | `capture_viewport`, `capture_multi_view`, `capture_screen`, `render_scene` | C++ |
| Organization | `manage_layers`, `manage_groups`, `manage_selection_sets`, `manage_scene` | C++ |
| File access | `inspect_max_file`, `merge_from_file`, `search_max_files`, `batch_file_info` | C++ |
| Plugins | `discover_plugin_classes`, `introspect_class`, `introspect_instance`, `get_plugin_capabilities` | C++ |
| Scene events | `watch_scene`, `get_scene_delta` | C++ |
| tyFlow | `create_tyflow`, `get_tyflow_info`, `modify_tyflow_operator`, `set_tyflow_shape`, `reset_tyflow_simulation` | MAXScript |
| Forest Pack | `scatter_forest_pack` | MAXScript |
| Data Channel | `add_data_channel`, `inspect_data_channel`, `set_data_channel_operator` | MAXScript |
| Scripting | `execute_maxscript` | Pipe |

## v0.6.0 — Standalone Chat Mode

Run an AI chat entirely inside 3ds Max — no external MCP client required. The native bridge ships with a Win32 chat window, an OpenAI-compatible LLM client, and direct access to the full tool surface.

- **Launch:** Customize UI → MCP category → **MCP Chat** macroscript
- **Config:** `%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini` — `[llm]` section (`api_key`, `base_url`, `model`). Default target is OpenRouter. Env-var fallback: `OPENROUTER_API_KEY`, `LLM_API_KEY`, `OPENAI_API_KEY`.
- **Tools:** all ~88 tools from `src/tools/*.py` are auto-registered (generated at build time by `scripts/gen_tool_registry.py`), plus `execute_maxscript` as a catch-all.
- **Security:** the existing `[mcp] safe_mode` filter applies — `execute_maxscript` calls from the chat hit the same keyword blocklist as every other path.
- **Skill-aware:** the v0.6.0 deploy copies `SKILL.md` to `%LOCALAPPDATA%\3dsmax-mcp\skill\` and the chat loads it into the system prompt.
- **Slash commands:** `/reload`, `/clear`, `/help`.

## v0.5.2 Notice

Deleted the procedural placement tools (kept floor plan — will update that to be actually useful) and removed verified workflows. The native bridge already handles verification and the verified tools were causing crashes.

## Building from source (native bridge)

Only needed if you want to modify the C++ plugin.

**Max 2027+** — Visual Studio 2022 (v143), C++20, CMake 3.20+

```powershell
cd native
cmake -B build -G "Visual Studio 17 2022" -A x64 -DMAX_VERSION=2027
cmake --build build --config Release
```

Then copy `native/build/Release/mcp_bridge.gup` to `C:\Program Files\Autodesk\3ds Max <version>\plugins\`.
