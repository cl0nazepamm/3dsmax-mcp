# 3dsmax-mcp

MCP server bridging Claude to Autodesk 3ds Max via file-based JSON polling.

## Prerequisites

- [Python 3.10+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Autodesk 3ds Max 2026

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YourUsername/3dsmax-mcp.git
cd 3dsmax-mcp
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Set up 3ds Max (MAXScript listener)

Copy the MAXScript files into your 3ds Max installation:

1. Copy `maxscript/mcp_server.ms` to:
   ```
   [3ds Max Install Dir]/scripts/mcp/mcp_server.ms
   ```

2. Copy `maxscript/startup/mcp_autostart.ms` to:
   ```
   [3ds Max Install Dir]/scripts/startup/mcp_autostart.ms
   ```

3. Restart 3ds Max. You should see `MCP: Auto-start complete` in the MAXScript Listener.

### 4. Connect to Claude

#### Claude Code (CLI)

Add to your `.claude/settings.json` or project settings:

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/3dsmax-mcp",
        "3dsmax-mcp"
      ]
    }
  }
}
```

#### Claude Desktop App

Edit `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/3dsmax-mcp",
        "3dsmax-mcp"
      ]
    }
  }
}
```

Replace `/absolute/path/to/3dsmax-mcp` with the actual path where you cloned the repo. Restart the Claude Desktop app after editing.

## How it works

1. The MCP server (Python) sends MAXScript commands as JSON to a temp directory
2. The MAXScript listener (running inside 3ds Max) polls for requests, executes them, and writes responses
3. Claude sends commands through the MCP server and gets results back

## Available tools

- `execute_maxscript` - Run arbitrary MAXScript code
- `get_scene_info` - List all objects in the scene
- `get_selection` - Get info about selected objects
- `get_object_properties` - Get detailed properties of an object
- `set_object_property` - Set a property on an object
- `create_object` - Create a new object
- `delete_objects` - Delete objects by name
- `get_materials` - List all materials in the scene
- `render_scene` - Render the current viewport
