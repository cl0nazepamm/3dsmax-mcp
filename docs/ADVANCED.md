# Advanced configuration

Technical reference for 3dsmax-mcp: architecture, build, tool calling, and security.

## Architecture

```
AI agent  <-->  FastMCP (Python)  <-->  Native bridge (C++ GUP inside 3ds Max)
                                      |
                                      +--> MAXScript listener fallback
```

The native bridge is a Global Utility Plugin. It reads the scene through the 3ds Max SDK and exposes most high-frequency operations without round-tripping through MAXScript parsing. A MAXScript listener remains as a fallback when the native path is unavailable.

Release builds link the whole bridge into that one GUP. Development builds can split it into a permanent shell GUP and a core DLL that reloads while Max runs; see [Hot-reloadable native core](#hot-reloadable-native-core-development).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Autodesk 3ds Max 2023–2027

## Installation details

```powershell
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
uv sync
# build the bridge for your 3ds Max first: see "Building the native bridge"
uv run python install.py
```

The repository carries no built bridges: a checkout builds its own, and a release (the Windows
setup, the PyPI package) carries them for 3ds Max 2023 to 2027.

Skip skill deployment:

```powershell
uv run python install.py --skip-skill
```

The installer asks which [tool profile](#tool-profiles) MCP clients get and defaults to `code`.
To choose without the prompt:

```powershell
uv run python install.py --tool-profile full
```

After install, restart 3ds Max. The installer:

1. Writes user config under `%LOCALAPPDATA%\3dsmax-mcp\` (an existing `mcp_config.ini` is kept, apart from the selected `[mcp] tool_profile`).
2. Deploys an **ApplicationPlugins bundle** to `%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\` (native GUPs in `Contents\bin\`, MAXScript in `Contents\scripts\`). 3ds Max must be closed.
3. Copies the agent skill to `~/.claude/skills` and `~/.agents/skills`.
4. Registers MCP entries where it can (Claude Desktop, Cursor, Gemini, CLI agents).

A copy of the bridge that a very old install left in the 3ds Max folder (`plugins\mcp_bridge.gup`) loads beside the bundle; `uninstall.py` removes it.

### Application package layout

```
%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\
  PackageContents.xml
  Contents\
    bin\mcp_bridge_2023.gup … mcp_bridge_2027.gup
    bin\mx_2023\ … mx_2027\          _mx, the sdk module of each bridge
    scripts\mcp_server.ms
```

Manifest template: [`maxmcp/assets/PackageContents.xml.in`](../maxmcp/assets/PackageContents.xml.in). Max loads the GUP matching its version (`plugins parts`) and the shared TCP fallback script (`post-start-up scripts parts`).

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

## How agents call tools

MCP clients send every advertised tool definition with each model request. By default the server advertises five tools, about 2k Claude tokens: `run_python`, and `list_max_instances`, `select_max_instance`, `get_selected_max_instance` and `release_max_instance`, which choose the 3ds Max it talks to. The agent works by writing Python scripts against the `mx` API. Every other tool is a Python function in `maxmcp/tools`, called from a script as `mx.tools.<name>(**arguments)`. `mx.help()` lists the tools by group, `mx.help("<group>")` lists one group with signatures, `mx.find_tools("boolean subtract")` returns the manuals of the best matches, and `mx.help("<name>")` prints one tool's signature and manual.

To expose tools of your own, write your own MCP server around `maxmcp.mx`, which works from any Python process that can import `maxmcp`.

### Tool profiles

The default above is the **code** profile. The other profiles list tools directly, for clients and models that work better that way. A profile decides only what a client lists, and so how much of the context window the server takes before any work starts; `run_python` scripts reach every tool in every profile.

| Profile | Advertised surface | Tool definitions per request (Claude tokens) |
|---------|--------------------|----------------------------------------------|
| **code** (default) | `run_python` and the 4 instance routing tools | ~2k |
| **progressive** | The routing tools, the 14 most common tools and `run_python` (`DIRECT_TOOLS` in `maxmcp/profiles.py`), and `find_tools`, `list_toolsets`, `describe_toolset`, `call_tool` | ~7k |
| **core** | Scene, object, modeling, material, controller, viewport, file, plugin, lighting and render setup tools | ~19k |
| **full** | Every tool: core plus tyFlow, MCG, Forest Pack, RailClone, Data Channel, Pulze, effects, state sets, wire params, render automations, and the development-only native core reload | ~28k |

Figures are approximate, with the default `hybrid` tool docs. Other models' tokenizers count differently.

The installer persists the choice as `[mcp] tool_profile` in `%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini`. The Windows setup shows the installed profile selected and changes the setting only when another one is picked; it accepts `/TOOLPROFILE=code|full|progressive|core` for unattended installs. `MCP_TOOL_PROFILE` (or `THREEDSMAX_MCP_TOOL_PROFILE`) overrides the setting for one launch:

```powershell
$env:MCP_TOOL_PROFILE = "progressive"
uv run 3dsmax-mcp
```

A server with no installed setting and no override uses **code**, and so does one given a name that is not a profile.

Progressive workflow:

1. Use a directly listed tool when one fits.
2. Otherwise call `find_tools(query)` with what you need, for example `"boolean subtract"` or `"spline knots"`. It returns the best matches with their exact input schemas. `list_toolsets` and `describe_toolset(toolset, tools=[...])` browse the groups instead; a toolset is one folder of `maxmcp/tools`, the same groups `mx.help()` prints.
3. Call `call_tool(name, arguments)`. A wrong or unknown argument name is refused with the tool's signature, and an unknown tool name suggests close matches.

The tools `call_tool` reaches are not registered with the client, so `tools/list` does not change after discovery. `call_tool` runs a tool through the same registry as `mx.tools`, with the same argument check and the same `ToolEnvelope`. It does not make mutating tools safer: bridge `safe_mode`, tool-specific dry-run controls, and normal mutation precautions still apply.

### Tool docs

In `full`, `core` and `progressive`, `[mcp] tool_docs` in `mcp_config.ini`, or `MCP_TOOL_DOCS` (`THREEDSMAX_MCP_TOOL_DOCS`) for one launch, decides how the advertised tools are documented. `code` advertises five tools and always documents them in full.

| Mode | Advertised tools | Full profile |
|------|------------------|--------------|
| **hybrid** (default) | The common tools (`DIRECT_TOOLS`) keep their full description and JSON Schema; every other tool is its docstring's first line plus a Python-style signature | ~28k |
| **signature** | Every tool is a summary line plus signature | ~24k |
| **complete** | Every tool keeps its full description and JSON Schema | ~75k |

A signature lists each argument with its type and default, enums inline, and nested models on the following lines:

```
Move, rotate, and/or scale an object by world/local offsets.
(name:str='', handle:int=0, move:list[float]=None, rotate:list[float]=None, scale:list[float]=None, coordinate_system:str='world')
```

A signature tool advertises `{"type": "object"}` as its input schema, so its arguments are validated against the Python signature, strictly: an argument name the tool does not take is refused instead of dropped, and the error repeats the signature. `tool_help(name)`, which `full` and `core` list in these modes, returns any listed tool's full manual and JSON Schema. Models often pass numbers and arrays to signature tools as strings; numeric strings and JSON-encoded lists, objects and models are converted, but a `str` parameter keeps what it was given. In hybrid and signature mode `expected_pid` is accepted by every tool but advertised once, in the server instructions, instead of in each schema. What `find_tools` and `describe_toolset` return is always a tool's full manual and schema.

In every mode, advertised input schemas omit Pydantic titles and spell optional `X | None = None` parameters as plain `X`; arguments are still validated against the Python signature, so an explicit `null` is accepted.

### Python scripts (`run_python` and `mx`)

`run_python(code)` runs a script with `mx` (`maxmcp.mx`) pre-imported. The reply's `ran_in` says where it ran:

- **`max`**: inside 3ds Max's own Python, when the bridge allows Python (`safe_mode` off) and exports the in-process
  entry point `mcp_bridge_call`. 3ds Max 2027 runs the server's Python (3.13) and loads the server's packages; 2023
  to 2026 run Python 3.9 to 3.11 and load the copy of pydantic and Pillow built for it (`maxmcp/inmax/site`), and
  each bridge ships the `_mx` built for its 3ds Max and Python (`mx_<year>` beside the plugin).
  `mx` reaches the native handlers in-process instead of over the named pipe, and `sdk`, `rt` (`pymxs.runtime`) and
  `pymxs` are in scope. Everything the script changes (tool calls, `sdk` and `rt` edits) is one undo step, rolled
  back entirely if the script raises. A script never joins an operation the user has open: it is refused with
  `USER_BUSY`.
- **`server`**: otherwise, in the MCP server process, sandboxed, each tool call its own undo step; `note` says why
  it could not run in Max. `where="max"` or `"server"` forces one.

A tool call checks its arguments against the function's signature before anything is sent to 3ds Max. An argument
name the tool does not take, or a value of the wrong type, raises `mx.MaxToolError` with code `BAD_PARAM` and the
signature. `expected_pid`, on `run_python` or on one `mx.tools` call, refuses the call unless it reaches the 3ds Max
with that PID.

Inside Max the same API is available to any script: `import maxmcp.inmax as inmax; inmax.activate()` routes `mx`
through the bridge in-process, and `inmax.run_script(code)` runs a script as one undo step.

```python
blk = mx.box("Block", width=100, length=60, height=40, pos=(900, 0, 0))
mx.boolean(blk, operation="subtract", cutters=[{"name": "Hole", "shape": "cylinder", "size": [30, 30, 60], "pos": [900, 0, 20]}])
mx.render_setup(lights=[{"name": "Key", "kind": "rectangle", "width": 50, "height": 50, "position": (900, -80, 150),
                         "aim_at": blk.center, "intensity": 1000, "unit": "cd"}])
result = {"bbox": blk.bbox, "faces": mx.qa(blk)["counts"]["triangles"]}
```

Helpers live in `maxmcp/mx/objects.py`, `modeling.py`, `shading.py` and `view.py`. A script that runs in the server
may import only `mx` and the standard math/json/re/itertools family; every script stops at `timeout_s`.

## Config file

Shared by the native bridge, MAXScript listener, and external MCP server:

```
%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
```

Example:

```ini
[mcp]
safe_mode = false
tool_profile = code
tcp_idle_poll_interval_ms = 1500
```

## Safe mode

A fresh install has `safe_mode = false`. That is what lets `run_python` scripts run inside 3ds Max, with `sdk`, `rt` and one undo step per script.

With `safe_mode = true`:

- `run_python` scripts run in the MCP server's sandbox instead, each tool call its own undo step, without `sdk` or `rt`.
- `execute_python` is refused.
- `execute_maxscript` blocks scripts that contain `DOSCommand`, `HiddenDOSCommand`, `ShellLaunch`, `deleteFile`, `createFile` or `python.Execute`.
- Script and expression operators of tyFlow, Data Channel and MCG are refused.

```ini
[mcp]
safe_mode = true
```

Restart 3ds Max after changing the setting. An update keeps the `mcp_config.ini` that is installed, so a config written by 1.x still says `safe_mode = true` until you change it. With no setting at all (a missing file or line) the bridge treats safe mode as on.

### Scope — read this

Safe mode is an **accident preventer**, not a sandbox. It is a case-insensitive substring blocklist, so determined authors can bypass it with concatenation or indirect calls.

What it does **not** cover:

- Native handlers run unfiltered: `delete_objects`, `manage_scene`, `render_setup`, `merge_from_file`, `write_osl_shader`, viewport capture (disk writes), etc.
- The named pipe uses default ACLs — any process running as your user can connect on a typical dev machine.

### Quiet mode and the save prompt

Tool scripts run in Max's quiet mode, so prompts take their default answer. For the "save changes?" prompt of a reset, open, fetch or quit, that default discards unsaved work, so a script that mentions one of those commands runs with prompts shown and the prompt reaches the agent as `BLOCKED_BY_DIALOG`. While the scene has unsaved changes, `execute_maxscript` and `execute_python` refuse such a script with `UNSAVED_CHANGES` unless `allow_discard=True` is passed. `manage_scene` fetch and reset always keep their prompt. The `execute_maxscript` manual lists the commands and the exact rules.
## Multi-instance Max

Each 3ds Max window registers its own native pipe. With one Max running, clients connect automatically. With several open, run **MCP Claim This Max** in the target window so clients route to that instance until another is claimed.

The selected target belongs to the MCP server process, not to a chat session. When a client app routes several sessions to one server process, they share it: one session's `select_max_instance` or `release_max_instance` moves the others. Every tool except the four routing tools takes an optional `expected_pid`; a call whose target is another Max fails with `TARGET_MISMATCH` and nothing is sent. `selection_generation` in every reply's transport changes on each select, release or re-resolve.

A target the server picked by itself (the default or a claimed Max) follows its Max's restart, and the reply warns: every tool moves to the one Max that started after the old one stopped answering. A Max that was already open belongs to another session and is never taken over; until the restart is up, calls fail with `TARGET_UNAVAILABLE` (retryable) and nothing is sent. Several new instances give `AMBIGUOUS` (select one), and a claimed instance wins. A target selected explicitly or pinned with `MCP_MAX_PIPE`/`MCP_MAX_PID` never moves and keeps failing with `TARGET_UNAVAILABLE` until you select or release.

TCP fallback is opt-in via the **MCP Start** macroscript. `tcp_idle_poll_interval_ms` controls idle polling frequency for the fallback listener (default is sparse to reduce viewport stutter).

## Busy or hung 3ds Max

Over the named pipe, the client tells a busy Max from a hung one instead of waiting out a bare timeout:

- One request at a time goes to each Max. A call that can't take the pipe within 10 s fails with `MAX_BUSY`, `request_sent: false`, retryable: nothing reached Max.
- A sent request waits for its reply until the reply deadline: about 130 s, or a long tool's own timeout plus 10 s. A Max that exits meanwhile ends the call at once. At the deadline the client checks Max's main window and CPU. A main thread that is blocked, and still blocked 20 s later, gives `MAX_NOT_RESPONDING` with window and CPU evidence; until it recovers, further calls to that Max fail fast with the same code, nothing sent. Only windows on Max's main-window thread count, so a modeless window stuck on another thread does not make Max look hung.
- A Max that is still working at the deadline (busy, or responding) gives `MAX_BUSY`, so a runaway script never holds the server forever. `MAX_BUSY` therefore has two meanings. Before sending: nothing ran, retry later. After the reply deadline: the call was sent, keeps running in Max and is not retryable; `max_dialogs(action="inspect")` reports its result in `blocked_calls`. `request_sent` and `retryable` tell them apart.
- A request whose reply was lost after it was sent (the pipe closed, a mismatched reply) fails with `REQUEST_OUTCOME_UNKNOWN`. The client never replays a request that may have run.
- A call held by a modal dialog returns `BLOCKED_BY_DIALOG` while Max's main thread keeps pumping; once it stops, the call is diagnosed like any other.

`get_bridge_status` uses the bridge's `health` command, which a pipe thread answers without touching Max's main thread, so it works while Max is hung. It reports whether the main thread is idle, busy with this server's request, another MCP client's, or work outside the bridge, plus queued work, connected clients and open dialogs. With an older bridge it falls back to `ping`.

`capture_hang_diagnostics` reads the native thread stacks of a hung Max from the OS, without sending anything to it, and names what blocks the main thread. Each thread is suspended for a few milliseconds while its stack is read. Captures are saved under `%LOCALAPPDATA%\3dsmax-mcp\diagnostics\` (the newest 20 are kept).

The stdio server exits when its client process exits, and always after its stdin closes, so a crashed client doesn't leave a server holding a connection to Max. `MAXMCP_PARENT_WATCHDOG=0` turns off the client-process check.

## Agent skill

The skill holds what the tools' own manuals do not say: rules of conduct, cross-tool workflows and MAXScript pitfalls. It is `SKILL.md` and its references (MAXScript, tyFlow, RailClone, procedural graphs, curves), the `.md` files of `skills/3dsmax-mcp-dev/`. `install.py` and the Windows setup copy them to `~/.claude/skills/3dsmax-mcp-dev` and `~/.agents/skills/3dsmax-mcp-dev`; copying the folder by hand does the same. MCP resource: `resource://3dsmax-mcp/skill`.

## Building the native bridge

Needed to install from a checkout, and after modifying C++ handlers.

Install matching 3ds Max SDKs, Visual Studio 2022 and CMake. Each bridge builds with its `_mx` (`sdk` in scripts) against the Python its 3ds Max embeds, which CMake takes from uv (`uv python install 3.9 3.10 3.11` for 2023 to 2026). Build one bridge per 3ds Max, here 2027, then stage it where `install.py` looks for it:

```powershell
cmake -S native -B native/build-2027 -G "Visual Studio 17 2022" -A x64 -DMAX_VERSION=2027
cmake --build native/build-2027 --config Release

New-Item -ItemType Directory -Force native\bin\mx_2027 | Out-Null
Copy-Item native\build-2027\Release\mcp_bridge.gup native\bin\mcp_bridge_2027.gup
Copy-Item native\build-2027\Release\python\_mx.*.pyd native\bin\mx_2027\
```

CMake looks for the SDK in `C:\Program Files\Autodesk\3ds Max <year> SDK\maxsdk`; pass `-DMAXSDK_PATH=...` (quoted, when the path has spaces) for another location. `native/bin/` is not tracked. Then run `uv run python install.py` to deploy the application package to `%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\`.

## Hot-reloadable native core (development)

For native handler work the bridge can run as a permanent shell plus a core DLL that a running 3ds Max swaps in place, so a rebuilt handler loads without a restart. Releases ship the static layout only.

| Layout | `MCP_HOT_CORE` | Output | Reload |
|--------|----------------|--------|--------|
| Static (default, shipped) | `OFF` | `mcp_bridge.gup` with the core linked in | `CORE_STATIC` |
| Hot (development only) | `ON` | shell `mcp_bridge.gup` plus `core\mcp_core.dll` | `reload_native_core` |

The **shell** holds everything 3ds Max keeps a pointer into: plugin registration, the named pipe, the main-thread executor, the dialog monitor, the scene journal, the agent viewport, render notifications and undo records. It answers `ping`, `health`, `native:max_dialogs` and `native:core` itself, so health checks, dialog recovery and core control keep working while the core is missing or being replaced. The **core** is command dispatch, the handlers and the native tool registry. The two meet only through versioned C tables (`native/include/mcp_bridge/core_api.h`, `host_api.h`), and a core loads only into a shell built with the same compiler, Max SDK, configuration and boundary versions.

The shell looks for the core in this order, and the first source that is set wins even when its file does not exist:

1. The `path` argument of a reload.
2. `MCP_CORE_DLL` in 3ds Max's environment.
3. The first non-blank line of `mcp_core.path` next to the `.gup`.
4. `<folder of the .gup>\core\mcp_core.dll`.

It maps a copy of the DLL, so a build can overwrite the original while Max runs. `reload_native_core` reports the layout and the loaded build and swaps in a rebuilt core; its manual lists the errors. Changes to shell sources or to the two boundary headers still need a rebuilt shell and a Max restart.
## Tripback and debugging

Tool responses default to `{ok, result}` or `{ok, error}`. Set `MCP_TRIPBACK_MODE=full` for timing and extended metadata.

Inside Max:

- **MCP Smoke** macro or `run_tool_smoke` MCP tool
- `invoke_tool` for single-tool probes
- `get_bridge_status` when connections fail (not as a session preamble)

Regenerate the smoke catalog with `python scripts/codegen/gen_tool_smoke.py`.

## Project layout

| Path | Purpose |
|------|---------|
| `maxmcp/server.py` | FastMCP entry; advertises the tools the profile lists, by default `run_python` and the instance routing tools |
| `maxmcp/registry.py` | Finds a tool by name, checks its arguments, and backs `mx.help` and `mx.find_tools` |
| `maxmcp/profiles.py`, `maxmcp/user_config.py` | The `code`, `full`, `core` and `progressive` tool profiles and their `tool_docs` modes; the `mcp_config.ini` settings that select them |
| `maxmcp/mx/`, `maxmcp/inmax/` | The script API, and its host inside 3ds Max |
| `maxmcp/tools/<group>/` | One folder per subject. Modules without a leading underscore declare tools; `_*.py` are their helpers |
| `maxmcp/core/` | Named-pipe client, response envelopes, MAXScript escaping, hung-Max diagnostics |
| `maxmcp/assets/` | What ships that is not Python: config template, bundle manifest, MAXScript, tyFlow script checker, MCG samples |
| `native/src/shell/` | The part of the C++ bridge 3ds Max keeps loaded: plugin, named pipe, main-thread executor, dialogs |
| `native/src/core/` | Command dispatch and the SDK handlers (`handlers/`); the part a hot reload swaps |
| `native/src/shared/` | Stateless code both sides link |
| `native/mx/` | `_mx`, the SDK-bound Python module scripts get as `sdk` |
| `native/bin/` | Where the built bridges and their `_mx` are staged for `install.py` and the wheel; not tracked |
| `skills/3dsmax-mcp-dev/` | Agent skill source |
| `install.py`, `uninstall.py` | Source installer and uninstaller |
| `scripts/codegen/` | Generators the native build runs: the native tool registry and the smoke cases |
| `scripts/release/` | `vendor_inmax.py`, which fetches the per-Python packages for in-Max scripts |
