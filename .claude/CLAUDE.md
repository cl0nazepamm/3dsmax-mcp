# 3dsmax-mcp

MCP server for AI agents to control 3ds Max. This file is the single source of truth — `AGENTS.md` (Codex) is auto-generated from it via `scripts/build_skill.py`.

## learn-from-mistakes

When you encounter a bug, unexpected behavior, or discover a MAXScript/3ds Max/MCP pitfall:
1. Fix the issue
2. Append the lesson to the relevant section in `skills/3dsmax-mcp-dev/SKILL.md`
3. One line per lesson — include the pattern or fix
4. Check for duplicates before adding

## Project Structure
- `src/server.py` — FastMCP server entry point
- `src/max_client.py` — TCP socket client (connects to 127.0.0.1:8765)
- `src/tools/` — MCP tool implementations (one file per category)
- `maxscript/mcp_server.ms` — MAXScript listener (runs inside 3ds Max)
- `maxscript/startup/mcp_autostart.ms` — auto-start loader for 3ds Max
- `native/` — C++ GUP bridge plugin (named pipe, 53 native handlers)

## Skills & Build
- `skills/3dsmax-mcp-dev/SKILL.md` — source of truth (grows via learn-from-mistakes)
- `scripts/build_skill.py` — builds `.skill` archive, copies to local + global `.claude/skills/`, generates `AGENTS.md`
- Both `.claude/skills/` and `AGENTS.md` are gitignored — never edit them directly

## Key Patterns
- Tools registered via `@mcp.tool()` in `src/tools/*.py`
- External MCP defaults to compact `MCP_TOOL_PROFILE=core`, including controller tools; set `MCP_TOOL_PROFILE=full` to register specialty modules (`data_channel`, `effects`, `floor_plan`, `railclone`, `render`, `scattering`, `state_sets`, `tyflow`, `wire_params`, `chat`).
- All tools send MAXScript strings to 3ds Max via `client.send_command()`
- MAXScript results returned as JSON strings via manual concatenation
- Prefer OpenPBR for neutral PBR material creation/conversion; use PhysicalMaterial only as fallback or when explicitly requested.
- Viewport capture: `gw.getViewportDib()` → save to temp → `Read` tool to view
- Do not RENDER unless user explicitly asks — but `capture_multi_view` (quad view) is encouraged after scene changes
- Standalone chat (v0.7.0): `MCP Chat` macroscript opens a Win32 window; config in `%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini` `[llm]`, tool registry auto-generated from Python by `scripts/gen_tool_registry.py`, dispatches through the same `CommandDispatcher` so `safe_mode` applies. Token defaults are compact: `prompt_mode=compact`, `tool_profile=core`; use `prompt_mode=full` or `tool_profile=full` only when needed.
