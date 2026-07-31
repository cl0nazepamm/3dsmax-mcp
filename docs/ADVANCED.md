# Advanced configuration

Technical reference for 3dsmax-mcp: architecture, build, profiles, security, and in-Max chat (WIP).

## Architecture

```
AI agent  <-->  FastMCP (Python)  <-->  Native bridge (C++ GUP inside 3ds Max)
                                      |
                                      +--> MAXScript listener fallback
```

The native bridge is a Global Utility Plugin. It reads the scene through the 3ds Max SDK and exposes most high-frequency operations without round-tripping through MAXScript parsing. A MAXScript listener remains as a fallback when the native path is unavailable.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Autodesk 3ds Max 2023–2027

## Installation details

```powershell
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
uv sync
uv run python install.py
```

Skip skill deployment:

```powershell
uv run python install.py --skip-skill
```

After install, restart 3ds Max. The installer:

1. Removes any **legacy** files copied into the Max install directory (`plugins\mcp_bridge.gup`, `scripts\mcp\`, `scripts\startup\mcp_autostart.ms`) from older installs.
2. Deploys an **ApplicationPlugins bundle** to `%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\` (native GUPs in `Contents\bin\`, MAXScript in `Contents\scripts\`).
3. Writes user config under `%LOCALAPPDATA%\3dsmax-mcp\`, builds the agent skill, and registers MCP entries where it can (Claude Desktop, Cursor, Gemini, CLI agents).

### Application package layout

```
%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\
  PackageContents.xml
  Contents\
    bin\mcp_bridge_2023.gup … mcp_bridge_2027.gup
    scripts\mcp_server.ms
```

Manifest template: [`bundle/PackageContents.xml.in`](../bundle/PackageContents.xml.in). Max loads the GUP matching its version (`plugins parts`) and the shared TCP fallback script (`post-start-up scripts parts`).

### Dev testing without install

Build native binaries, stage the bundle, and point Max at it:

```powershell
native\build.bat all
uv run python scripts/stage_bundle.py --dest bundle
set ADSK_APPLICATION_PLUGINS=C:\path\to\3dsmax-mcp\bundle
```

Then launch 3ds Max. For a full install, run `uv run python install.py` (also migrates away legacy install-dir copies automatically).

## MCP client registration

Default server entry:

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "C:/path/to/3dsmax-mcp", "3dsmax-mcp"]
    }
  }
}
```

Manual CLI examples:

```powershell
claude mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
codex mcp add 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
```

Config file locations:

| Client | Path |
|--------|------|
| Claude Desktop (standalone) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Desktop (Microsoft Store) | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json` |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` |
| Gemini | `%USERPROFILE%\.gemini\settings.json` |

`install.py` registers both Claude Desktop locations when the Store package folder is present.

## Tool profiles

The external MCP server defaults to the **full** profile (114 tools). Set **core** for a smaller everyday surface (77 tools) without specialty plugin modules.

```powershell
$env:MCP_TOOL_PROFILE = "core"
uv run 3dsmax-mcp
```

| Profile | Modules |
|---------|---------|
| **core** | Scene, objects, materials, modifiers, controllers, viewport, file access, plugins, organization, learning |
| **full** | Core plus tyFlow, Forest Pack, RailClone, Data Channel, effects, floor plan, state sets, wire params, render, in-Max chat drivers (WIP) |

Specialty modules in full profile: `chat`, `data_channel`, `effects`, `floor_plan`, `railclone`, `render`, `scattering`, `state_sets`, `tyflow`, `wire_params`.

## Config file

Shared by the native bridge, MAXScript listener, and standalone chat:

```
%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
```

Example:

```ini
[mcp]
safe_mode = true
tcp_idle_poll_interval_ms = 1500

[llm]
base_url = https://openrouter.ai/api/v1
model = anthropic/claude-sonnet-4.6
max_tokens = 4096
temperature = 0.2
prompt_mode = compact
tool_profile = full
include_scene_snapshot = false
max_scene_roots = 25
max_prompt_chars = 12000
max_tool_result_chars = 12000
max_history_tool_chars = 1800
max_tool_summary_chars = 600
max_display_tool_chars = 600
max_tool_loops = 4
```

API keys for standalone chat live in `%LOCALAPPDATA%\3dsmax-mcp\.env` (see `.env.example`). Real environment variables override the file.

## Safe mode

When `safe_mode = true` (default), dangerous MAXScript shapes are blocked in `execute_maxscript`, including substring matches for:

- `DOSCommand`, `ShellLaunch`, `deleteFile`, `python.Execute`, `createFile`

Disable only if you accept the risk:

```ini
[mcp] 
safe_mode = false
```

Restart 3ds Max after changing config.

### Scope — read this

Safe mode is an **accident preventer**, not a sandbox. It is a case-insensitive substring blocklist, so determined authors can bypass it with concatenation or indirect calls.

What it does **not** cover:

- Native handlers run unfiltered: `delete_objects`, `manage_scene`, `render_scene`, `merge_from_file`, `write_osl_shader`, viewport capture (disk writes), etc.
- The named pipe uses default ACLs — any process running as your user can connect on a typical dev machine.

Treat standalone chat like any local agent that can edit your scene. Keep API keys out of shared drives.

## Multi-instance Max

Each 3ds Max window registers its own native pipe. With one Max running, clients connect automatically. With several open, run **MCP Claim This Max** in the target window so clients route to that instance until another is claimed.

TCP fallback is opt-in via the **MCP Start** macroscript. `tcp_idle_poll_interval_ms` controls idle polling frequency for the fallback listener (default is sparse to reduce viewport stutter).

## Agent skill

The skill teaches agents tool choice, material workflows, controller paths, and MAXScript pitfalls. The installer builds and deploys it automatically.

Manual rebuild:

```powershell
python scripts/build_skill.py
python scripts/build_skill.py --target global   # user-level .claude/skills and .agents/skills
```

Bundled MAXScript reference lives under `skills/3dsmax-mcp-dev/` (10 topic files). MCP resource: `resource://3dsmax-mcp/skill`.

Anthropic models sometimes prefer raw MAXScript over dedicated tools; Codex tends to use native tools more reliably. The skill reduces that gap.

## Standalone chat (in-Max)

> **Work in progress.** The in-Max chat window and its helper tools are under active development. APIs, config keys, and behavior may change between releases. For day-to-day work, prefer an **external MCP client** (Cursor, Claude Desktop, Codex, etc.) — that path is stable.

Run an LLM inside 3ds Max without an external MCP client — same tool surface as external MCP when it works.

Open **MCP Chat** from Customize UI → MCP, or search the macro globally.

- **API key:** `%LOCALAPPDATA%\3dsmax-mcp\.env` (see `.env.example`; `OPENROUTER_API_KEY`, `LLM_API_KEY`, or `OPENAI_API_KEY`)
- **Settings:** `[llm]` section in `mcp_config.ini`
- **Tools:** Auto-generated registry from `src/tools/*.py` (`scripts/gen_tool_registry.py` at build time)
- **Security:** Same `safe_mode` filter as external MCP for `execute_maxscript`
- **Slash commands:** `/reload`, `/clear`, `/help`
- **Skill:** Deployed to `%LOCALAPPDATA%\3dsmax-mcp\skill\SKILL.md`; `prompt_mode=full` injects the full skill into the system prompt

External helper tools (for automation outside Max): `send_to_chat`, `chat_status`, `chat_reload`, `chat_clear`.

## Building the native bridge

Only needed when modifying C++ handlers.

Install matching 3ds Max SDKs. Builds land in `native/bin/` and are staged into `bundle/Contents/bin/`. Run `uv run python install.py` to deploy the application package to `%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\`.

```powershell
cd native
.\build.bat all
.\build.bat 2025          # single version
.\build.bat all deploy    # build + copy into Max plugin folders
```

Windows batch note: quote CMake `-D` paths when the repo or SDK path contains spaces.

## Tripback and debugging

Tool responses default to `{ok, result}` or `{ok, error}`. Set `MCP_TRIPBACK_MODE=full` for timing and extended metadata.

Inside Max:

- **MCP Smoke** macro or `run_tool_smoke` MCP tool
- `invoke_tool` for single-tool probes
- `get_bridge_status` when connections fail (not as a session preamble)

Regenerate smoke catalog:

```powershell
python scripts/gen_tool_smoke.py
python scripts/run_live_tool_smoke.py --tier read
```

## Project layout

| Path | Purpose |
|------|---------|
| `src/server.py` | FastMCP entry, tool registration |
| `src/tools/` | MCP tool implementations |
| `native/` | C++ GUP bridge |
| `maxscript/` | Listener + autostart |
| `skills/3dsmax-mcp-dev/` | Agent skill source |
| `docs/MATERIAL_NETWORK_TOOLS.md` | `inspect_material_network` + `replicate_material` spec |
| `scripts/build_skill.py` | Skill + AGENTS.md generator |
| `scripts/gen_tool_registry.py` | In-Max chat tool registry |
