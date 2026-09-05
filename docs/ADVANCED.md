# Advanced configuration

Technical reference for 3dsmax-mcp: architecture, build, profiles, and security.

## Architecture

```
AI agent  <-->  FastMCP (Python)  <-->  Native bridge (C++ GUP inside 3ds Max)
                                      |
                                      +--> MAXScript listener fallback
```

The native bridge is a Global Utility Plugin. It reads the scene through the 3ds Max SDK and exposes most high-frequency operations without round-tripping through MAXScript parsing. A MAXScript listener remains as a fallback when the native path is unavailable.

## Requirements

- Python 3.12+
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

The installer prompts for the external MCP tool profile and defaults to `full` for maximum
client compatibility. Context-limited local or smaller models can use the progressive surface:

```powershell
uv run python install.py --tool-profile progressive
```

After install, restart 3ds Max. The installer:

1. Removes any **legacy** files copied into the Max install directory (`plugins\mcp_bridge.gup`, `scripts\mcp\`, `scripts\startup\mcp_autostart.ms`) from older installs.
2. Deploys an **ApplicationPlugins bundle** to `%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\` (native GUPs in `Contents\bin\`, MAXScript in `Contents\scripts\`).
3. Writes user config under `%LOCALAPPDATA%\3dsmax-mcp\`, including the selected `[mcp] tool_profile`, builds the agent skill, and registers MCP entries where it can (Claude Desktop, Cursor, Gemini, CLI agents).

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

The installer defaults to **full**, which advertises every operational tool directly for maximum MCP client compatibility. For context-limited local or smaller models, **progressive** advertises only `list_toolsets`, `describe_toolset`, and `call_tool`, then lazy-loads exact operational schemas on demand. **Core** remains the smaller eager profile. A server launched without an installed setting or environment override also falls back to **full**.

```powershell
$env:MCP_TOOL_PROFILE = "progressive"
uv run 3dsmax-mcp
```

The installer persists the choice as `[mcp] tool_profile` in
`%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini`. `MCP_TOOL_PROFILE` (or
`THREEDSMAX_MCP_TOOL_PROFILE`) overrides that setting for a specific launch.

| Profile | Advertised surface |
|---------|--------------------|
| **progressive** (context-efficient) | Three discovery/dispatch meta-tools; operational modules and schemas load only when described or called; useful for local or smaller models |
| **core** | Eager scene, object, material, modifier, controller, viewport, file, plugin, organization, and learning tools |
| **full** (installer default) | Eager core plus tyFlow, MCG, Forest Pack, RailClone, Data Channel, effects, floor plan, state sets, wire params, render, render automations |

Progressive workflow:

1. Call `list_toolsets` to choose a capability group.
2. Call `describe_toolset` for that group’s exact tool names and input schemas.
3. Call `call_tool` with the selected name and argument object.

Operational tools stay in a private registry, so `tools/list` remains at three entries after discovery. `call_tool` accepts only tools declared by the core/full module allowlist, rejects meta-tool and recursive dispatch, validates arguments with the original FastMCP schema, and returns the same `ToolEnvelope` as eager profiles. It does not make mutating tools safer: bridge `safe_mode`, tool-specific dry-run controls, and normal mutation precautions still apply.

To use the smaller eager surface instead:

```powershell
$env:MCP_TOOL_PROFILE = "core"
uv run 3dsmax-mcp
```

Specialty modules in full profile: `data_channel`, `effects`, `floor_plan`, `mcg`, `railclone`, `render`, `render_automations`, `scattering`, `state_sets`, `tyflow`, `tyflow_graph`, `tyflow_patch`, `tyflow_manifest`, `tyflow_census`, `wire_params`.

## Native identity, transactions, and scene QA

`resolve_node_refs` converts a name, native handle, or absolute JSON-Pointer hierarchy path such as `/Rig/Camera` into a canonical `{handle, name, path, class, layer}` identity plus `sceneSeq`, the current persistent-mutation revision. It also reports `activitySeq` for interaction diagnostics. Selection and sub-object selection advance only `activitySeq`, so normal viewport clicking does not stale a guarded write. Supplying more than one selector cross-checks identity instead of silently retargeting a stale handle. Handles are scene/session-local; refresh after loading or resetting a scene.

`scene_patch` accepts up to 256 `rename`, relative `transform`, `set_flags`, and `set_parent` operations. It resolves and validates the entire plan first, optionally rejects a stale mutation-only `expected_scene_seq`, supports `dry_run`, and applies the plan inside exactly one native undo hold. Selection-only interaction remains observable but never blocks the patch. A failed apply cancels the whole hold. Mutating native calls must still be issued sequentially.

`scene_qa` is intentionally non-mesh. It checks naming collisions, invalid or degenerate transforms, hierarchy cycles, group metadata, and timeline sanity; optional checks cover transform risks and distance from origin. It never inspects topology, UVs, normals, skinning, weight painting, or visual quality. Automatic repair is restricted to explicit deterministic name-collision and empty-name fixes, with dry-run and stale-sequence guards.

## Config file

Shared by the native bridge, MAXScript listener, and external MCP server:

```
%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
```

Example:

```ini
[mcp]
safe_mode = true
tcp_idle_poll_interval_ms = 1500
```

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
| `maxmcp/server.py` | FastMCP entry, tool registration |
| `maxmcp/tools/` | MCP tool implementations |
| `native/` | C++ GUP bridge |
| `maxscript/` | Listener + autostart |
| `skills/3dsmax-mcp-dev/` | Agent skill source |
| `scripts/build_skill.py` | Skill archive builder and installer |
| `scripts/gen_tool_registry.py` | Native diagnostic tool registry |
