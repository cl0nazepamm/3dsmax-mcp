# 3dsmax-mcp

<p align="left">
  <img src="logo.png" alt="3dsmax-mcp logo" width="200">
</p>

MCP server bridging Claude to Autodesk 3ds Max via TCP socket.

## Prerequisites

- [Python 3.10+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Autodesk 3ds Max 2025+ (only 2026 tested!)

## Ideas you can try

- Write MaxScript/Python directly. Claude will read and debug code, fix issues, and keep the agent running on a loop until success.
- Write OSL shaders
- Read and manipulate scene data.
- Organize objects.
- Set up project folders and organize them.
- Get feedback on your renders. (Claude can see outside 3dsmax window)
- Will learn from mistakes and save it in SKILL.md
- Basic 3dsmax skill file is included. Contributions welcome.
- You can also rename objects using AI.(Only works on Claude Code). Ask Claude to rename objects using haiku. Claude will run haiku subagent and analyze selected objects in the scene. Be aware that this burns tokens CRAZILY. Only do this if you're rich.
- Try using plugins like Forest Pack and tyFlow.
- Convert scenes between renderers

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Build skill file

```bash
python scripts/build_skill.py
```

This copies the development skill to `.claude/skills/` so Claude Code auto-discovers it in-project.

#### Global skill (optional)

To make the skill available in all your Claude Code projects, create a symlink to your user-level skills directory:

```bash
mklink /D "%USERPROFILE%\.claude\skills\3dsmax-mcp-dev" "C:\path\to\3dsmax-mcp\skills\3dsmax-mcp-dev"
```

Replace `C:\path\to\3dsmax-mcp` with the actual path where you cloned the repo. This lets Claude Code load the 3ds Max skill even when you're working outside this project.

To activate the skill in a conversation, tell Claude:

> activate skill 3dsmax-mcp-dev

### 4. Set up 3ds Max (MAXScript listener)

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

### 5. Connect to Claude

#### Claude Code (CLI)

Run this command 
```bash
claude mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
```

#### Gemini CLI

Run these commands (replacing the path with your absolute local path):

1. **Add MCP Server:**
   ```bash
   gemini mcp add --scope user 3dsmax-mcp uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
   ```
2. **Install Dev Skill:**
   ```bash
   gemini skills install "C:\path\to\3dsmax-mcp\skills\3dsmax-mcp-dev"
   ```

#### Claude Desktop App

Edit `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```bash
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\path\\to\\3dsmax-mcp",
        "3dsmax-mcp"
      ]
    }
  }
}
```

Replace `C:\\path\\to\\3dsmax-mcp` with the actual path where you cloned the repo. Restart the Claude Desktop app after editing.

## How it works

1. The MAXScript listener runs inside 3ds Max on TCP port 8765
2. The MCP server (Python) sends MAXScript commands via TCP socket
3. 3ds Max executes commands and returns JSON responses
4. Claude sends commands through the MCP server and gets results back

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
