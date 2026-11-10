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
uv run python install.py
```

Skip skill deployment:

```powershell
uv run python install.py --skip-skill
```

After install, restart 3ds Max. The installer:

1. Removes any **legacy** files copied into the Max install directory (`plugins\mcp_bridge.gup`, `scripts\mcp\`, `scripts\startup\mcp_autostart.ms`) from older installs.
2. Deploys an **ApplicationPlugins bundle** to `%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\` (native GUPs in `Contents\bin\`, MAXScript in `Contents\scripts\`).
3. Writes user config under `%LOCALAPPDATA%\3dsmax-mcp\` (an existing `mcp_config.ini` is kept), builds the agent skill, and registers MCP entries where it can (Claude Desktop, Cursor, Gemini, CLI agents).

### Application package layout

```
%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\
  PackageContents.xml
  Contents\
    bin\mcp_bridge_2023.gup … mcp_bridge_2027.gup
    scripts\mcp_server.ms
```

Manifest template: [`maxmcp/assets/PackageContents.xml.in`](../maxmcp/assets/PackageContents.xml.in). Max loads the GUP matching its version (`plugins parts`) and the shared TCP fallback script (`post-start-up scripts parts`).

### Dev testing without install

Build native binaries, stage the bundle, and point Max at it:

```powershell
native\build.bat all
uv run python scripts/release/stage_bundle.py --dest bundle
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

## How agents call tools

MCP clients send every advertised tool definition with each model request. The server advertises five tools, about 2k Claude tokens: `run_python`, and `list_max_instances`, `select_max_instance`, `get_selected_max_instance` and `release_max_instance`, which choose the 3ds Max it talks to. The agent works by writing Python scripts against the `mx` API. Every other tool is a Python function in `maxmcp/tools`, called from a script as `mx.tools.<name>(**arguments)`. `mx.help()` lists the tools by group, `mx.help("<group>")` lists one group with signatures, `mx.find_tools("boolean subtract")` returns the manuals of the best matches, and `mx.help("<name>")` prints one tool's signature and manual.

The `full`, `core` and `progressive` tool profiles of 1.x are gone, together with `MCP_TOOL_PROFILE`, `[mcp] tool_profile` and the `call_tool`, `list_toolsets` and `describe_toolset` tools. A setting left in `mcp_config.ini` or in a client's environment is ignored. To expose tools of your own, write your own MCP server around `maxmcp.mx`, which works from any Python process that can import `maxmcp`.

### Python scripts (`run_python` and `mx`)

`run_python(code)` runs a script with `mx` (`maxmcp.mx`) pre-imported. The reply's `ran_in` says where it ran:

- **`max`**: inside 3ds Max's own Python, when the bridge allows Python (`safe_mode` off), exports the in-process
  entry point `mcp_bridge_call`, and Max's Python matches the server's (3ds Max 2027 and the server both run 3.13).
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

- Native handlers run unfiltered: `delete_objects`, `manage_scene`, `render_setup`, `merge_from_file`, `write_osl_shader`, viewport capture (disk writes), etc.
- The named pipe uses default ACLs — any process running as your user can connect on a typical dev machine.

### Quiet mode and the save prompt

Tool scripts run in Max's quiet mode, so prompts take their default answer. For the "save changes?" prompt of a reset, open, fetch or quit, that default discards unsaved work. A script that mentions `resetMaxFile`, `loadMaxFile`, `fetchMaxFile`, `quitMax`, `checkForSave`, `max reset file`, `max file new`, `max file open`, `max fetch` or the Interface's `FileReset`, `FileFetch` or `LoadFromFile` therefore runs with quiet mode off, and the prompt reaches the agent as `BLOCKED_BY_DIALOG`. The match is plain text (comments and strings included); a false positive only leaves a prompt visible. `execute_maxscript(quiet=True)` on such a script is sent as `quiet=False` unless `allow_discard=True` is passed too. Replies say when quiet mode was off for this reason (`transport.quiet_override` and a warning). A command reached indirectly (`fileIn`, `macros.run`) is not seen: pass `quiet=False` for such scripts. The bridge applies the same rule to scripts sent without `quiet`, and `execute_maxscript` sends `quiet=False` for them explicitly, so an older bridge shows the prompt too. `manage_scene` fetch and reset always keep their prompt. `loadMaxFile` never asks to save, with prompts on or off, so `execute_maxscript` and `execute_python` also check the scene first: while it has unsaved changes, such a script is refused with `UNSAVED_CHANGES` before anything runs, unless `allow_discard=True`. If a dialog holds that read-only check, the call returns `BLOCKED_BY_DIALOG` with `retryable: true`: the script was not sent, so repeat it once the dialog is answered. The check reads `getSaveRequired()`, the state Max's own prompt uses. Edits made with undo off, as plain execute_maxscript edits are by default, don't mark the scene modified, in Max too. Tool edits (create, transform, material, clone, delete) and `undo on` scripts do. A script that calls `checkForSave()` is refused too, since the name may sit in a comment or the answer may be ignored. With `allow_discard=True` it still shows its prompt.

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

The skill teaches agents tool choice, material workflows, controller paths, and MAXScript pitfalls. The installer builds and deploys it automatically.

Manual rebuild:

```powershell
python scripts/release/build_skill.py
python scripts/release/build_skill.py --target global   # user-level .claude/skills and .agents/skills
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
.\build.bat all deploy    # build, then print the install.py deploy step
```

Windows batch note: quote CMake `-D` paths when the repo or SDK path contains spaces.

## Hot-reloadable native core (development)

For native handler work the bridge can run as a permanent shell plus a core DLL that a running 3ds Max swaps in place, so a rebuilt handler loads without a restart.

| Layout | `MCP_HOT_CORE` | Output | Reload |
|--------|----------------|--------|--------|
| Static (default, shipped) | `OFF` | `mcp_bridge.gup` with the core linked in | `CORE_STATIC` |
| Hot (development only) | `ON` | shell `mcp_bridge.gup` plus `core\mcp_core.dll` | `reload_native_core` |

The **shell** holds everything 3ds Max keeps a pointer into: plugin registration, the named pipe, the main-thread executor, the dialog monitor and its Qt reader, the scene journal and `scene_delta` cursors, GDI+, the agent viewport, `render_start`'s armed job and render notifications, and undo records. It answers `ping`, `health`, `native:max_dialogs` and `native:core` itself, so health checks, dialog recovery and core control keep working while the core is missing or being replaced. The **core** is command dispatch, the handlers and the generated native tool registry. The two sides meet only through versioned C tables (`native/include/mcp_bridge/core_api.h`, `host_api.h`). Each side's sources have their own folder, `native/src/shell` and `native/src/core`, and `native/CMakeLists.txt` lists them. Stateless shared code (`native/src/shared`) is linked into both, so a reload updates only the core's copy.

### Building the hot layout

`scripts/dev/hot_core.py` keeps the hot tree apart from the release trees and never installs anything, writes into Max or ProgramData folders, or stops Max:

```powershell
python scripts/dev/hot_core.py configure   # native/build-hot-2027, same Visual Studio instance as native/build-2027
python scripts/dev/hot_core.py build       # mcp_core target only, Release
python scripts/dev/hot_core.py path        # built DLL plus the mcp_core.path line and MCP_CORE_DLL value
python scripts/dev/hot_core.py status --gup-dir "<folder of the installed .gup>"
```

`--max-version` picks another Max (default 2027) and `--build-dir` another tree. A full `cmake --build native\build-hot-2027 --config Release` also builds the hot shell (`Release\mcp_bridge.gup`). Install that shell in place of the static GUP and restart Max once; from then on, core rebuilds reload in place. A core loads only into a shell built with the same compiler, Max SDK, configuration and boundary versions, so build both from the same tree.

### How the shell finds the core

The first source that is set wins, even when its file does not exist, so a typo fails instead of loading another core. Relative paths resolve against the `.gup`'s folder.

1. The `path` argument of a reload.
2. `MCP_CORE_DLL` in 3ds Max's environment.
3. The first non-blank line of `mcp_core.path` next to the `.gup` (UTF-8 with or without BOM, or UTF-16LE with BOM; surrounding quotes ignored).
4. `<folder of the .gup>\core\mcp_core.dll`.

The shell copies the DLL, and its `.pdb` when present, to `%TEMP%\3dsmax-mcp\core\mcp_core-<pid>-<tick>.dll` and maps the copy, so a build can overwrite the original while Max runs; copies no process maps any more are swept on later loads. Before binding a core it compares the core API version, host API version, Max SDK version, MSVC version and iterator debug level with its own. If loading fails at startup, `ping`, `health`, `max_dialogs` and `native:core` still answer, status reports `last_error`, every other request fails with "the native core is not loaded", and a later reload can still load a core.

### Control protocol

Pipe request type `native:core`, whose `command` is a JSON object; an empty command means status. Python sends it on the control channel (`MaxClient.core_control`, tool `reload_native_core`), never over TCP, and retries a reload's `CORE_BUSY` up to three attempts only when `wait_ms` is at most 20000; a longer wait gets a single attempt.

| Request or error | Behavior |
|------------------|----------|
| `{"action": "status"}` | Changes nothing. Returns `layout` (`static`/`hot`), `loaded`, `build`, `bridge_version`, `core_api_version`, `host_api_version`, `source`, `loaded_from`, `loaded_at_ms`, `reloads`, `in_flight`, `last_error`. |
| `{"action": "reload", "wait_ms": 5000, "path": "..."}` | Loads and checks the new core while the old one serves, closes the gate (other core calls get `CORE_BUSY`) and waits up to `wait_ms` for calls in flight; then, on Max's main thread, shuts the old core down, initializes and binds the new one, and frees the old copy. Returns the status plus `reloaded`, `previous_build` and `drain_ms`. `path` is optional; `wait_ms` is 0-600000 (default 5000). |
| `BAD_PARAM` | Malformed command. |
| `CORE_STATIC` | Static layout; nothing to reload. |
| `CORE_BUSY` (retryable) | Calls did not drain in time, the main thread did not run the swap, the old core declined to shut down while a `watch_scene` callback ran, or another reload was in progress. The old core keeps serving; `details.wait_ms` is the wait used. |
| `CORE_LOAD_FAILED` | Missing file, failed copy or `LoadLibrary`, no `mcp_core_api` export, incomplete table, or a new core that would not initialize. The old core keeps serving unless the message says no core is loaded. |
| `CORE_VERSION_MISMATCH` | The new core's stamps differ from the shell's; the message lists each pair. Nothing changes. |

Every error reports `details.in_flight`. A reload stops `watch_scene` (its notifications and overlay are unregistered and its events dropped), keeps `scene_delta` positions while the scene journal runs, and makes the core re-read `safe_mode` from `mcp_config.ini`. Changes to shell sources or to `core_api.h`/`host_api.h` still need a rebuilt shell and a Max restart.

### Release rule

Ship the static layout only. `MCP_HOT_CORE` defaults to `OFF`; with it `ON` CMake defines no install rule, and `build.bat` stages only `mcp_bridge.gup` from `native\build-<ver>`, so a hot shell would ship without its core. Keep the release trees static and do hot work in `native\build-hot-<ver>`.

## Tripback and debugging

Tool responses default to `{ok, result}` or `{ok, error}`. Set `MCP_TRIPBACK_MODE=full` for timing and extended metadata.

Inside Max:

- **MCP Smoke** macro or `run_tool_smoke` MCP tool
- `invoke_tool` for single-tool probes
- `get_bridge_status` when connections fail (not as a session preamble)

Regenerate smoke catalog:

```powershell
python scripts/codegen/gen_tool_smoke.py
python scripts/live/run_live_tool_smoke.py --tier read
```

## Project layout

| Path | Purpose |
|------|---------|
| `maxmcp/server.py` | FastMCP entry; advertises `run_python` and the instance routing tools |
| `maxmcp/registry.py` | Finds a tool by name, checks its arguments, and backs `mx.help` and `mx.find_tools` |
| `maxmcp/mx/`, `maxmcp/inmax/` | The script API, and its host inside 3ds Max |
| `maxmcp/tools/<group>/` | One folder per subject. Modules without a leading underscore declare tools; `_*.py` are their helpers |
| `maxmcp/core/` | Named-pipe client, response envelopes, MAXScript escaping, hung-Max diagnostics |
| `maxmcp/assets/` | What ships that is not Python: config template, bundle manifest, MAXScript, tyFlow script checker, MCG samples |
| `native/src/shell/` | The part of the C++ bridge 3ds Max keeps loaded: plugin, named pipe, main-thread executor, dialogs |
| `native/src/core/` | Command dispatch and the SDK handlers (`handlers/`); the part a hot reload swaps |
| `native/src/shared/` | Stateless code both sides link |
| `native/mx/` | `_mx`, the SDK-bound Python module scripts get as `sdk` |
| `native/bin/` | Prebuilt bridges for 3ds Max 2023-2027 |
| `skills/3dsmax-mcp-dev/` | Agent skill source |
| `install.py`, `uninstall.py` | Source installer and uninstaller |
| `scripts/dev/` | `mcpcall.py` runs one tool from current source in a fresh process; `hot_core.py` builds the hot-reloadable core |
| `scripts/codegen/` | Generators for the native tool registry, smoke cases and sdk reference; `vendor_qt.py` |
| `scripts/release/` | `build_skill.py` (skill archive and install), `stage_bundle.py` |
| `scripts/live/` | Checks that run against a live 3ds Max |
