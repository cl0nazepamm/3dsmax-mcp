# C++ Native Bridge for 3dsmax-mcp (Option 2 Hybrid)

## Context

The current architecture uses a Python MCP server that sends MAXScript strings over TCP to a MAXScript listener inside 3ds Max. Every tool call pays: TCP connection overhead + MAXScript parse/eval + manual JSON string building in MAXScript. The goal is to replace this with a C++ GUP plugin that runs inside Max, communicates via Windows named pipes, and calls the 3ds Max SDK directly — while keeping the Python MCP layer and all 37 tool files unchanged.

```
Before: Claude <-> FastMCP Python <-> TCP:8765 <-> MAXScript execute() <-> Max
After:  Claude <-> FastMCP Python <-> Named Pipe <-> C++ GUP <-> Max SDK
```

---

## Project Structure

```
native/
  CMakeLists.txt                    # Build config targeting Max SDK
  CMakePresets.json                 # Debug/Release presets per Max version
  include/mcp_bridge/
    bridge_gup.h                    # GUP plugin class
    pipe_server.h                   # Named pipe server (background thread)
    command_dispatcher.h            # JSON command routing
    main_thread_executor.h          # Main thread marshaling via WM_USER
    native_handlers.h               # Phase 2: direct SDK handlers
    json_helpers.h                  # JSON wrapper (nlohmann)
    logging.h                       # Logging to Max Listener + file
  src/
    dllmain.cpp                     # DLL exports (LibDescription, etc.)
    bridge_gup.cpp                  # GUP Start()/Stop(), ClassDesc2
    pipe_server.cpp                 # Overlapped named pipe I/O
    command_dispatcher.cpp          # Parse JSON, route to handlers
    main_thread_executor.cpp        # Hidden window + WM_USER dispatch
    maxscript_handler.cpp           # ExecuteMAXScriptScript() wrapper
    native_handlers.cpp             # Phase 2: SDK scene queries
    json_helpers.cpp
    logging.cpp
    mcp_bridge.def                  # Module definition (ordinal exports)
  third_party/nlohmann/json.hpp     # Header-only JSON library
  deploy/
    install.bat                     # Copy .gup to Max plugin dir
    uninstall.bat
```

Modified existing files:
- `src/max_client.py` — add named pipe transport (ctypes, zero new deps)
- `.gitignore` — add `native/build/`, `*.gup`

---

## Phase 1: Transport Replacement (all tools work unchanged)

### 1. DLL Entry Point (`dllmain.cpp`)
- Export 6 required functions: `LibDescription`, `LibNumberClasses`, `LibClassDesc`, `LibVersion`, `LibInitialize`, `LibShutdown`
- Single `ClassDesc2` registering the GUP

### 2. GUP Plugin (`bridge_gup.cpp`)
- `Start()` — creates `PipeServer` + `MainThreadExecutor`, returns `GUPRESULT_KEEP`
- `Stop()` — tears down pipe server and executor
- Auto-loads when Max starts (GUPs are auto-discovered from plugin dirs)

### 3. Named Pipe Server (`pipe_server.cpp`)
- Pipe name: `\\.\pipe\3dsmax-mcp`
- Background thread with overlapped I/O
- Single pipe instance (matches current 1-request-at-a-time model)
- `WaitForMultipleObjects` on connect event + shutdown event for clean teardown
- Same protocol: newline-delimited JSON, same request/response fields
- Flow: `ConnectNamedPipe` → `ReadRequest` → `Dispatch` → `WriteResponse` → `DisconnectNamedPipe` → loop

### 4. Main Thread Marshaling (`main_thread_executor.cpp`)
- Creates a hidden Win32 window during `GUP::Start()` (runs on main thread)
- Background pipe thread posts `WM_USER+0x4D43` with a work item pointer
- Window proc executes the work item on main thread, signals condition variable
- Background thread blocks on `cv.wait_for()` with 120s timeout
- This is the standard approach for Max plugins — reliable across all Max versions

### 5. MAXScript Handler (`maxscript_handler.cpp`)
- Receives MAXScript string from dispatcher
- Calls `ExecuteMAXScriptScript(wideCmd, ScriptSource::NotSpecified, &result)` on main thread
- Converts returned `Value*` to UTF-8 string via `to_string()`
- Identical behavior to current MAXScript `execute()` but through SDK

### 6. Command Dispatcher (`command_dispatcher.cpp`)
- Parses JSON request with nlohmann/json
- Routes by `type` field: `"maxscript"` → MaxScriptHandler, `"ping"` → PingHandler
- Builds JSON response with same fields: `success`, `requestId`, `result`, `error`, `meta`
- Adds `"transport": "namedpipe"` to meta (backward compatible)

### 7. Python Client (`src/max_client.py`)
- Add `_send_via_pipe()` method using `ctypes.windll.kernel32` (CreateFileW, WriteFile, ReadFile, CloseHandle)
- Add `_resolve_transport()`: auto mode tries pipe first, falls back to TCP
- Constructor accepts `transport="auto"|"pipe"|"tcp"` parameter
- Zero new dependencies — ctypes is stdlib

### 8. Build System (`CMakeLists.txt`)
- CMake 3.20+, C++17, MSVC x64
- Links: `core.lib`, `maxutil.lib`, `geom.lib`, `gup.lib`, `maxscrpt.lib`, `paramblk2.lib`
- Output: `mcp_bridge.gup`
- `MAXSDK_PATH` configurable (default: `C:/Program Files/Autodesk/3ds Max 2025 SDK/maxsdk`)

### 9. Deployment
- `deploy/install.bat` copies `.gup` to `%LOCALAPPDATA%\Autodesk\3dsMax\2025 - 64bit\ENU\plugins\`
- User-writable, no admin needed
- Old MAXScript server can run simultaneously (different transport) — zero conflict

---

## Phase 2: Native SDK Handlers (after Phase 1 is stable)

Add `native:*` command types for hot-path operations:

| Handler | Replaces | SDK Calls |
|---------|----------|-----------|
| `native:get_scene_info` | 50-line MAXScript | `INode` tree traversal |
| `native:get_object_properties` | MAXScript property access | `INode::GetNodeTM()`, `ParamBlock2` |
| `native:transform_object` | MAXScript pos/rot/scale | `INode::SetNodeTM()` |
| `native:create_object` | MAXScript `Box()` etc. | `CreateInstance()` + `AddNewNode()` |
| `native:get_selection` | MAXScript selection query | `GetSelNodeCount/GetSelNode` |
| `native:capture_viewport` | MAXScript `gw.getViewportDib()` | `ViewExp::GetDIB()` |

Python tools migrate incrementally — change the body from MAXScript string to structured JSON command.

---

## Phase 3: Full Migration (ongoing)

- Migrate remaining tools to native handlers one at a time
- Add structured command schema validation
- Benchmark native vs MAXScript paths
- Eventually phase out MAXScript server for users with the GUP

---

## Coexistence Strategy

- Phase 1: both servers run simultaneously, Python auto-detects pipe → TCP fallback
- Users without the GUP keep working via MAXScript server unchanged
- No breaking changes to any existing tool or protocol

---

## Key Decisions

1. **Named pipe** over shared memory — natural request/response model, OS-buffered, simpler
2. **WM_USER + hidden window** for main thread — works across all Max versions, battle-tested in plugins
3. **nlohmann/json** — header-only, zero build complexity
4. **ctypes** for Python pipe client — zero new dependencies
5. **Single pipe instance** — Max is single-threaded anyway, concurrent instances would just queue

---

## Verification

1. Build the GUP: `cmake --preset max2025-release && cmake --build build/max2025-release --config Release`
2. Run `deploy/install.bat` to copy to Max plugin dir
3. Start 3ds Max — check Listener for "MCP Bridge: Started on \\.\pipe\3dsmax-mcp"
4. Run existing Python test suite — all tools should pass unchanged
5. Benchmark: compare round-trip latency of `ping` via pipe vs TCP
6. Stress test: rapid sequential commands from Python
7. Verify fallback: remove GUP, confirm Python falls back to TCP MAXScript server

---

## Files to Modify

- `src/max_client.py` — add pipe transport
- `.gitignore` — add native build artifacts

## Files to Create

- All files under `native/` (~14 files)
