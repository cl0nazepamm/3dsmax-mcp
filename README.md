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


## How to update

in powershell
```
git pull
python scripts/build_skill.py

```


## How it works

1. The MAXScript listener runs inside 3ds Max on TCP port 8765
2. The MCP server (Python) sends MAXScript commands via TCP socket
3. 3ds Max executes commands and returns JSON responses
4. Claude sends commands through the MCP server and gets results back

## Current list of tools

- `build_structure` - Procedurally builds larger structures (house, tower, castle, etc.)
- `clone_objects` - Clones objects as copy/instance/reference with optional offset.
- `add_data_channel` - Creates a Data Channel modifier graph.
- `inspect_data_channel` - Reads the full operator graph of a Data Channel modifier.
- `set_data_channel_operator` - Edits parameters of one Data Channel operator.
- `add_dc_script_operator` - Adds a MAXScript-based Data Channel script operator.
- `list_dc_presets` - Lists available Data Channel presets.
- `load_dc_preset` - Applies a Data Channel preset to an object.
- `get_effects` - Lists atmospheric/render effects in the scene.
- `toggle_effect` - Enables/disables an effect by index.
- `delete_effect` - Deletes an atmospheric/render effect by index.
- `execute_maxscript` - Runs arbitrary MAXScript and returns the result.
- `build_floor_plan` - Builds a floor plan from room/cell definitions.
- `place_on_grid` - Places one object at a grid index.
- `place_grid_array` - Fills a grid volume with repeated objects.
- `place_circle` - Places objects evenly around a circle.
- `set_parent` - Parents/unparents objects.
- `get_hierarchy` - Returns recursive child hierarchy for an object.
- `isolate_and_capture_selected` - Isolates selected objects and captures viewport images.
- `batch_rename_objects` - Renames many objects in one operation.
- `inspect_object` - High-level deep inspection of one object.
- `inspect_properties` - Deep property dump for object/base/modifier/material.
- `inspect_modifier_properties` - Deep property dump for one modifier.
- `assign_material` - Creates and assigns a material to objects.
- `set_material_property` - Sets one property on object material/sub-material.
- `set_material_properties` - Sets multiple material properties at once.
- `get_material_slots` - Runtime slot inspector with low-token scopes (`map`/`summary`/`all`) plus bitmap/normal helper class hints.
- `create_texture_map` - Creates a texture map and stores it as a global variable.
- `set_texture_map_properties` - Sets properties on a stored texture map.
- `set_sub_material` - Creates/assigns sub-material slots in Multi/Sub material.
- `write_osl_shader` - Writes OSL to disk and creates an OSLMap from it.
- `create_material_from_textures` - Auto-builds PBR material from a texture folder.
- `get_materials` - Lists assigned materials and their object usage.
- `add_modifier` - Adds a modifier to an object.
- `remove_modifier` - Removes a modifier from an object.
- `set_modifier_state` - Toggles modifier enabled/view/render states.
- `collapse_modifier_stack` - Collapses modifier stack to baked geometry.
- `make_modifier_unique` - De-instances a shared modifier.
- `batch_modify` - Scene-wide property edits for all modifiers of a class.
- `get_object_properties` - Detailed object properties (transform/material/modifiers).
- `set_object_property` - Sets one object property via MAXScript expression.
- `create_object` - Creates a primitive/object in scene.
- `delete_objects` - Deletes objects by name.
- `render_scene` - Runs an actual render (optionally saves file).
- `manage_scene` - Scene state actions (hold/fetch/reset/save/info).
- `find_class_instances` - Finds class instances scene-wide (`getclassinstances` style).
- `get_instances` - Gets all object instances sharing same base object.
- `get_dependencies` - Traces dependency graph for an object.
- `find_objects_by_property` - Finds objects by property/value match.
- `get_scene_info` - Lists scene objects with filters.
- `get_selection` - Returns current selection info.
- `select_objects` - Selects objects by names/pattern/class/all.
- `get_state_sets` - Reads State Sets with camera/range metadata.
- `get_camera_sequence` - Access to camera sequencer.
- `transform_object` - Moves/rotates/scales an object.
- `capture_viewport` - Fast active-viewport screenshot (safe default).
- `capture_screen` - Fullscreen capture, disabled by default; requires `enabled=true`.
- `set_visibility` - Hide/show/toggle/freeze/unfreeze objects.
