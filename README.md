# 3dsmax-mcp

<p align="left">
  <img src="images/logo.png" alt="3dsmax-mcp logo" width="200">
</p>

MCP server bridging Claude and other agents to Autodesk 3ds Max via TCP socket.

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

### 3. Build and register skill file

```bash
python scripts/build_skill.py
python scripts/register.py
```
This copies the development skill to `.claude/skills/` and registers the skill file to .claude json.

#### Global skill for all agents (optional)

I recommend creating a symbolic link for the skill file so both Claude, Codex and Gemini can all get it. Use command prompt not powershell for this. 

First install agent-skills if you don't have it. Via powershell  `npm install -g @govcraft/agent-skills`

then

```bash
mklink /D "%USERPROFILE%\.agents\skills\3dsmax-mcp-dev" "C:\path\to\3dsmax-mcp\skills\3dsmax-mcp-dev"
```

Replace `C:\path\to\3dsmax-mcp` with the actual path where you cloned the repo. This lets coding agents load the 3ds Max skill even when you're working outside this project. Requires admin permissions. If you don't have agent-skills you can just install it to `.codex/skills` or `.gemini/skills` etc. Claude might require you to create symlink in `.claude/skills`

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

### 5. Setting up MCP for agents.

In powershell 

```bash
claude mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp
codex mcp add 3dsmax-mcp -- uv run --directory C:\path\to\3dsmax-mcp 3dsmax-mcp
gemini mcp add --scope user 3dsmax-mcp -- uv run --directory "C:\path\to\3dsmax-mcp" 3dsmax-mcp

```

#### Claude Desktop App

Edit `%APPDATA%\Claude\claude_desktop_config.json`

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

#### Add skill to Claude app
Open Claude app go to settings> capabilities section and upload the .MD


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
