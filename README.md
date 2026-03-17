# 3dsmax-mcp

<p align="left">
  <img src="images/logo.png" alt="3dsmax-mcp logo" width="200">
</p>

MCP server that connects AI agents to Autodesk 3ds Max.
Works with Claude Code, Claude Desktop, Codex, Gemini, and any MCP-compatible client.

## Features

- Inspect and manipulate scenes — objects, materials, modifiers, hierarchies
- Create objects, assign materials, build PBR setups from texture folders
- Transform, clone, parent, hide/freeze objects in bulk
- Write OSL shaders
- Advanced viewport captures
- Self-describing plugin runtime and deep SDK level introspection.
- Access controllers, wire parameters, data channels
- Write MAXScript in a loop — agents iterate until it works
- Python scripting (requires safe mode off)
- Read .max files without opening them, batch inspect assets (Native plugin only)

## Architecture

```
Agent  <-->  FastMCP (Python/stdio)  <-->  Named Pipe <-->  3ds Max SDK
                                      |
                                      +--> TCP:8765 fallback --> MAXScript listener
```

The C++ plugin (`mcp_bridge.gup`) runs inside 3ds Max as a Global Utility Plugin. It reads the scene graph directly through the SDK and communicates with the Python MCP server over Windows named pipes. 

If plugin is not installed it will fallback to TCP.

## Prerequisites

- [Python 3.10+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/)
- Autodesk 3ds Max 2026

## Setup

### 1. Clone and install

```bash
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
uv sync
python scripts/build_skill.py
```

### 2. Install the native bridge (Max 2026)

Copy `mcp_bridge.gup` to your 3ds Max plugins folder:

```
C:\Program Files\Autodesk\3ds Max 2026\plugins\
```

Restart 3ds Max. The bridge starts automatically — no UI, no configuration.

### 3. Install the MAXScript listener

Only needed if you're not using the C++ plugin (e.g. Max 2024/2025). The native bridge handles everything including MAXScript execution internally.

1. Copy `maxscript/mcp_server.ms` to:
   ```
   [3ds Max Install Dir]/scripts/mcp/mcp_server.ms
   ```

2. Copy `maxscript/startup/mcp_autostart.ms` to:
   ```
   [3ds Max Install Dir]/scripts/startup/mcp_autostart.ms
   ```

3. Restart 3ds Max. You should see `MCP: Auto-start complete` in the MAXScript Listener.

### 4. Register with your agent

**Claude Code / Codex / Gemini** (PowerShell):

```bash
claude mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
codex mcp add 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
gemini mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
```

**Claude Desktop** — edit `%APPDATA%\Claude\claude_desktop_config.json`:

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

Replace paths with your actual clone location.

### 5. Skill file

The skill file teaches agents 3ds Max conventions and pitfalls. It grows automatically via the `learn-from-mistakes` flag.

```bash
python scripts/build_skill.py
python scripts/register.py
```

For global access across agents, symlink the skill folder:

```cmd
mklink /D "%USERPROFILE%\.claude\skills\3dsmax-mcp-dev" "C:\path\to\3dsmax-mcp\skills\3dsmax-mcp-dev"
mklink /D "%USERPROFILE%\.agents\skills\3dsmax-mcp-dev" "C:\path\to\3dsmax-mcp\skills\3dsmax-mcp-dev"
```

## Safe mode

The MAXScript listener runs with safe mode ON by default. Blocked operations:

- `DOSCommand` — shell execution
- `ShellLaunch` — launch external apps
- `deleteFile` — delete files from disk
- `python.Execute` — Python inside Max
- `createFile` — write files to disk

Everything else is allowed: scene operations, file reads, renders, viewport captures, saves.

To disable, set `safeMode = false` in `maxscript/mcp_server.ms`.

## Project structure

```
src/
  server.py              FastMCP server entry point
  max_client.py          Named pipe + TCP client
  tools/                 113 MCP tool implementations
maxscript/
  mcp_server.ms          MAXScript TCP listener (runs inside Max)
  startup/               Auto-start loader
native/
  bin/mcp_bridge.gup     Pre-built C++ plugin (Max 2026)
  src/                   C++ source (GUP, pipe server, native handlers)
  include/               Headers
  CMakeLists.txt         Build config (VS 2022, Max 2026 SDK)
skills/
  3dsmax-mcp-dev/        Skill file (conventions, pitfalls, patterns)
```

## Building the native plugin from source

Only needed if you want to modify the C++ code. Requires Visual Studio 2022 (v143 toolset) and the 3ds Max 2026 SDK.

```bash
cd native
cmake -B build -G "Visual Studio 17 2022" -A x64 .
cmake --build build --config Release
```

Output: `native/build/Release/mcp_bridge.gup`

## Updating

```bash
git pull
uv sync
python scripts/build_skill.py
```

Copy the updated `mcp_bridge.gup` to your Max plugins folder if changed.
