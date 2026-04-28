[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/cl0nazepamm-3dsmax-mcp-badge.png)](https://mseep.ai/app/cl0nazepamm-3dsmax-mcp)

# 3dsmax-mcp

<p align="left">
  <img src="images/logo.png" alt="3dsmax-mcp logo" width="200">
</p>

A production oriented MCP server that connects AI agents to Autodesk 3ds Max.
Works with any MCP-compatible client.

### Features

- **Native C++ Bridge** — 76 handlers running inside 3ds Max as a GUP plugin, 86-130x faster than MAXScript
- **One-step installer** — `uv run python install.py` handles everything
- **Quad-view capture** — Screenshotting is fast and supports multi views.
- **Compact default tool surface** — core MCP profile exposes common scene/object/material/inspection tools; full profile keeps specialty tools available
- **116 tools in full profile** across scene, objects, materials, modifiers, controllers, viewport, introspection.
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

## Updating

```powershell
git pull
uv sync
uv run python install.py
```

To install without copying or building the agent skill files:
```powershell
uv run python install.py --skip-skill
```

## MCP Tool Profile

The external MCP server defaults to a compact core profile to reduce tool-list
tokens in clients. Controller tools are included in core. Use the full profile
only when you need specialty modules such as Data Channel, effects, floor-plan
generation, tyFlow, RailClone, wire params, or standalone-chat driver tools.

```powershell
$env:MCP_TOOL_PROFILE = "full"
python -m src.server
```

## Skill

The skill file teaches agents how to use the tools, what pitfalls to avoid, and how 3ds Max works. Without it, agents will guess wrong on material workflows, controller paths, and plugin APIs. The installer builds and deploys it automatically. 

However Anthropic models seem to REALLY like using maxscript instead of using the native tooling unlike Codex which uses the right tool most of the time.

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

### Scope — read this

`safe_mode` is an **accident preventer**, not a sandbox. It's a case-insensitive substring blocklist, so a determined author (or a sufficiently clever LLM) can bypass it with string concatenation, DotNet reflection, etc. It catches the obvious shapes — LLM hallucinates `deleteFile` → rejected — not an adversarial MaxScript author.

What it **doesn't** cover:
- Native C++ handlers run unfiltered: `delete_objects`, `manage_scene` (reset/new/open), `render_scene`, `merge_from_file`, `write_osl_shader`, `capture_*` (disk writes). If the LLM hallucinates them they run.
- The `\\.\pipe\3dsmax-mcp` named pipe uses the default ACL — any process running as your user can open it and send commands. Fine on a single-user dev machine; if you need multi-user isolation, gate on `GetNamedPipeClientProcessId`.

The v0.7.0 chat window runs your configured LLM with direct scene-editing tools. Treat it like you'd treat any local agent that can edit your scene: don't point it at scenes you wouldn't double-click, and keep your API key in `.env` not a shared drive.

## Tools

Default core profile: 79 tools with concise descriptions. Full profile: 116 tools across scene management, objects, materials, modifiers, controllers, wiring, viewport capture, file access, plugin introspection, tyFlow, Forest Pack, RailClone, Data Channel, and more.

| Category | Tools | Transport |
|----------|-------|-----------|
| Scene reads | `get_scene_info`, `get_selection`, `get_scene_snapshot`, `get_selection_snapshot`, `get_scene_delta`, `get_hierarchy` | C++ |
| Objects | `create_object`, `delete_objects`, `transform_object`, `clone_objects`, `select_objects`, `set_object_property`, `set_visibility`, `set_parent` | C++/Hybrid |
| Inspection | `inspect_object`, `inspect_properties`, `introspect_class`, `introspect_instance`, `walk_references`, `learn_scene_patterns`, `map_class_relationships` | C++ |
| Materials | `assign_material`, `set_material_properties`, `get_material_slots`, `create_texture_map`, `palette_laydown`, `write_osl_shader`, `create_shell_material`, `replace_material` | Hybrid |
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

## v0.7.0 — Standalone Chat Mode

Run an AI chat entirely inside 3ds Max — no external MCP client required. The native bridge ships with a Win32 chat window, an OpenAI-compatible LLM client, and direct access to scene tools.

- **Launch:** You can find chat window in usermacros or search it directly by global search.
- **API key:** `%LOCALAPPDATA%\3dsmax-mcp\.env` — `OPENROUTER_API_KEY=...` (also accepts `LLM_API_KEY` / `OPENAI_API_KEY`). Real env vars override the file. `deploy.bat` seeds `.env.example` on first install.
- **Settings:** `%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini` `[llm]` — non-secret knobs only (`base_url`, `model`, `max_tokens`, `temperature`, plus token controls below). Default target is OpenRouter + `anthropic/claude-sonnet-4.6`.
- **Token controls:** external MCP defaults to `MCP_TOOL_PROFILE=core` plus concise tool descriptions. Standalone chat defaults to `prompt_mode=compact`, `tool_profile=core`, `include_scene_snapshot=true`, `max_scene_roots=25`, `max_prompt_chars=12000`, `max_tool_result_chars=12000`, `max_history_tool_chars=1800`, `max_tool_summary_chars=600`, `max_display_tool_chars=600`, `max_tool_loops=4`. Use `MCP_TOOL_PROFILE=full`, `prompt_mode=full`, or `tool_profile=full` only when you need the full specialty surface.
- **Tools:** native tools from `src/tools/*.py` are auto-registered (generated at build time by `scripts/gen_tool_registry.py`), plus `execute_maxscript` as a catch-all. The default chat schema exposes the compact core profile; the full registry remains available with `tool_profile=full`.
- **Security:** the existing `[mcp] safe_mode` filter applies — `execute_maxscript` calls from the chat hit the same keyword blocklist as every other path.
- **Skill-aware:** the v0.7.0 deploy copies `SKILL.md` to `%LOCALAPPDATA%\3dsmax-mcp\skill\`. The chat uses a compact rule summary by default; set `prompt_mode=full` to inject the deployed `SKILL.md`.
- **Slash commands:** `/reload`, `/clear`, `/help`.

## Building from source (native bridge)

Only needed if you want to modify the C++ plugin.

**Max 2027+** — Visual Studio 2022 (v143), C++20, CMake 3.20+

```powershell
cd native
cmake -B build -G "Visual Studio 17 2022" -A x64 -DMAX_VERSION=2027
cmake --build build --config Release
```

Then copy `native/build/Release/mcp_bridge.gup` to `C:\Program Files\Autodesk\3ds Max <version>\plugins\`.
