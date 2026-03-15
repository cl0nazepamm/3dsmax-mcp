---
name: 3dsmax-mcp-dev
description: Practical rules, tool choices, workflow patterns, and bridge failure modes for developing 3dsmax-mcp (Python MCP + MAXScript TCP listener). Use when adding or debugging tools, writing MAXScript bridge code, improving AI-to-3ds-Max interaction, or validating live transport/JSON behavior.
---

# 3dsmax-mcp Development Guide

This is the working guide for extending and debugging `3dsmax-mcp`.

Keep it lean:
- prefer strong primitives over UI-surface mirroring
- prefer low-token readback before deep inspection
- prefer dedicated tools over raw MAXScript
- prefer action + verification loops over optimistic success strings

## Core Rules

### Materials
- Never use `execute_maxscript` for normal material work.
- Use:
  - `assign_material`
  - `set_material_property`
  - `set_material_properties`
  - `get_material_slots`
  - `set_sub_material`
- Use raw MAXScript only for material-adjacent gaps such as file I/O, unusual texture-map creation, or unsupported shader wiring.

### Rendering and capture
- Do not render unless the user explicitly asks.
- Avoid screenshots by default.
- Prefer:
  - `capture_viewport`
  - `capture_model`
- Use `capture_screen enabled:true` only for full UI/fullscreen needs.

### Verification
- Do not trust mutation success strings on their own.
- After meaningful changes, verify with:
  - `get_scene_delta`
  - `inspect_object`
  - `get_selection_snapshot`
  - `get_material_slots`
  - specific verify tools such as `verify_scatter_output`

## Default Interaction Pattern

Use this order unless there is a clear reason not to:

1. Check bridge/session context
   - `get_bridge_status`
   - `get_session_context`
   - `inspect_active_target`
   - for plugin systems: `discover_plugin_surface`, `get_plugin_manifest`
2. Inspect the target
   - `inspect_object`
   - `inspect_properties`
   - `inspect_modifier_properties`
   - `get_material_slots`
   - plugin classes/instances: `inspect_plugin_class`, `inspect_plugin_instance`
3. Mutate with a dedicated tool
4. Verify with delta + readback

When possible, prefer the verified orchestration tools in `src/tools/workflows.py`:
- `create_object_verified`
- `assign_material_verified`
- `set_material_verified`
- `add_modifier_verified`
- `transform_object_verified`
- `set_modifier_state_verified`
- `set_object_property_verified`

These wrappers are the right place for common action+verify flows. Do not create one file per wrapper; keep orchestration consolidated.

## Tool Selection

### Live context
- Bridge health: `get_bridge_status`
- One-shot live context: `get_session_context`
- Context-aware current target: `inspect_active_target`
- Cheap scene summary: `get_scene_snapshot`
- Cheap selection summary: `get_selection_snapshot`
- Change tracking: `get_scene_delta`

### Plugin discovery
- Plugin family discovery: `discover_plugin_surface`
- Runtime class scan: `list_plugin_classes`
- Class-level reflection: `inspect_plugin_class`
- Constructor guidance: `inspect_plugin_constructor`
- Live plugin object inspection: `inspect_plugin_instance`
- Structured plugin manifest: `get_plugin_manifest`, `refresh_plugin_manifest`
- MCP resources:
  - `resource://3dsmax-mcp/plugins/index`
  - `resource://3dsmax-mcp/plugins/{plugin_name}/manifest`
  - `resource://3dsmax-mcp/plugins/{plugin_name}/guide`
  - `resource://3dsmax-mcp/plugins/{plugin_name}/recipes`
  - `resource://3dsmax-mcp/plugins/{plugin_name}/gotchas`
- tyFlow direct tools:
  - `list_tyflow_operator_types`
  - `create_tyflow`
  - `get_tyflow_info`
    - For maintenance readback, turn on `include_flow_properties`, `include_event_properties`, and `include_operator_properties` with sensible caps.
  - `add_tyflow_event`
  - `modify_tyflow_operator`
  - `set_tyflow_shape`
  - `set_tyflow_physx`
  - `add_tyflow_collision`
  - `connect_tyflow_events`
  - `remove_tyflow_element`
  - `get_tyflow_particle_count`
  - `get_tyflow_particles`
  - `reset_tyflow_simulation`
  - `create_tyflow_preset`
- RailClone direct tools:
  - `get_railclone_style_graph`
    - Uses exposed arrays/interfaces to reconstruct style graph data (bases/segments/parameters); not full UI-parity export.
- Current recipe layer:
  - `create_tyflow_basic_verified`
  - `create_tyflow_scatter_from_objects_verified`

### Object inspection
- Rich overview: `inspect_object`
- Typed property dump: `inspect_properties`
- Modifier-specific dump: `inspect_modifier_properties`
- Compact object details: `get_object_properties`

### Scene queries
- Filter/list objects: `get_scene_info`
- Find by property: `find_objects_by_property`
- Enumerate instances/classes: `get_instances`, `find_class_instances`
- Dependency tracing: `get_dependencies`
- Effects: `get_effects`
- State Sets / cameras: `get_state_sets`, `get_camera_sequence`

### Materials and texture maps
- Create + assign: `assign_material`
- Update one property: `set_material_property`
- Update many properties: `set_material_properties`
- Inspect practical slots: `get_material_slots`
- Multi/Sub: `set_sub_material`
- Texture maps: `create_texture_map`, `set_texture_map_properties`, `write_osl_shader`
- Folder-driven material creation: `create_material_from_textures`

### Object and modifier edits
- Create/delete objects: `create_object`, `delete_objects`
- Direct property set: `set_object_property`
- Transform: `transform_object`
- Modifiers: `add_modifier`, `remove_modifier`, `set_modifier_state`
- Collapse stack: `collapse_modifier_stack`
- Batch modifier edits: `batch_modify`

### Organization
- Select: `select_objects`
- Parenting: `set_parent`
- Visibility/freeze: `set_visibility`
- Clone/instance: `clone_objects`
- Batch rename: `batch_rename_objects`

### Procedural / specialty systems
- Data Channel: `add_data_channel`, `inspect_data_channel`, `set_data_channel_operator`, `add_dc_script_operator`
- Wires: `list_wireable_params`, `wire_params`, `get_wired_params`, `unwire_params`
- Controllers: `assign_controller`, `inspect_controller`, `inspect_track_view`, `add_controller_target`, `set_controller_props`
- Scatter: prefer dedicated tools and verification; do not improvise large manual placement loops first

## When `execute_maxscript` Is Appropriate

Use `execute_maxscript` only when no dedicated tool exists or when probing the host interactively:
- quick experiments
- unsupported host features
- custom scripted operations
- animation keyframing gaps
- render/environment settings not yet wrapped

Do not use it as the default workflow surface when a proper tool exists.

## Bridge and Protocol

Current architecture:
- Python MCP server: `FastMCP`
- 3ds Max host side: MAXScript TCP listener in `maxscript/mcp_server.ms`
- Address: `127.0.0.1:8765`
- Transport: JSON + newline delimiter, one request/response per connection
- Listener model: `.NET TcpListener` + timer polling

Current live pattern:
- protocol v2 supports `requestId` and response `meta`
- `get_bridge_status` falls back to legacy protocol behavior when needed

When editing the bridge:
- Python-side transport changes need MCP server restart
- MAXScript listener changes need reloading `mcp_server.ms` inside 3ds Max
- keep compatibility in mind if the Python side may speak to an older listener

## Adding or Updating Tools

For a new primitive tool:
1. Put it in the relevant domain file under `src/tools/`
2. Import `mcp` and `client` from `..server`
3. Build a MAXScript payload carefully
4. Use `client.send_command(...)`
5. Return `response.get("result", "")` or structured JSON
6. Add tests if the logic is non-trivial

For a repeated action+verify flow:
1. Keep the primitive tools where they belong
2. Add the composed wrapper to `src/tools/workflows.py`
3. Return both action result and verification payload

Do not create a new file for every `_verified` wrapper unless the orchestration file becomes genuinely unwieldy.

## MAXScript Pitfalls

### Constructors
- Do not use parentheses after class names with keyword params.
- Correct: `ai_standard_surface name:"Mat1" metalness:1.0`
- Wrong: `ai_standard_surface() name:"Mat1" metalness:1.0`

### Scope and execution
- `execute()` runs in global scope.
- Do not rely on local variables inside dynamically constructed `execute(...)` strings unless the string explicitly addresses the node/property path.

### Case-insensitivity
- MAXScript is case-insensitive.
- Avoid ambiguous short variable names.

### String / JSON escaping
- Escape user-provided strings before embedding in MAXScript using shared helpers from `src.helpers.maxscript`.
- For JSON emitted by MAXScript, use `MCP_Server.escapeJsonString`.
- Do not hand-roll escaping in multiple incompatible ways.

### Python string building
- In Python f-strings, double braces for literal MAXScript braces.
- Raw triple-quoted strings are usually safer for larger MAXScript blocks.

### .NET strings
- Convert .NET strings to MAXScript strings before using string methods.

### Misc host quirks
- No built-in `stringJoin`; concatenate manually.
- `Noise` is the texture map; modifier class is `Noisemodifier`.
- `(getDir #temp)` is Max temp, not OS temp.
- `#view_persp_user` is the correct perspective view enum.

## Domain Notes

### Materials
- `get_material_slots` is the practical low-token slot inspector.
- For material verification, compare slot values before/after. Scene delta only tracks material assignment names, not internal shader parameter edits.

### Modifiers
- Scene delta is useful for modifier count changes, not for every modifier state/property change.
- For modifier-state verification, inspect the actual modifier before/after.

### Data Channel
- Object must be Editable Mesh/Poly first.
- `operator_order` is 0-based.
- Operators not in the order list do not execute.
- Composite pipelines need a final `vertex_output`.
- `TransformElements.transformType` actual values are sequential: `0,1,2,3`.

### Wire parameters
- Rotation expressions use radians, not degrees.
- Paths from `list_wireable_params` starting with `[` need no dot separator.

### Controllers
- Inspect before assigning.
- Use `layer=True` to preserve existing controllers.
- Expression controllers require update after expression changes; wrapped tools handle this.

### Scattering
- Prefer procedural systems over manual placement for “scatter” tasks.
- Prefer stable adapters and verification over broad UI emulation.
- Forest-related work may be intentionally deferred; do not assume it is the current priority.

## Live Debug / Test Loop

Use this loop when validating changes:

1. Run Python tests
2. Reload MCP server if Python modules changed
3. Reload `mcp_server.ms` in 3ds Max if listener code changed
4. Check `get_bridge_status`
5. Run a small live smoke action
6. Verify via `get_scene_delta` and inspection

Useful signals:
- `ConnectionRefusedError`: listener is not running
- `Empty command` / `Unknown command type`: protocol mismatch
- syntax errors around JSON literals: escaping/building issue in MAXScript
- mutation success with no readback change: verification gap, not success
- Codex `shell_command` can be polluted by user PowerShell profile output/errors; use `login:false` for clean command output during repo diagnostics.

## Practical Endgame

Do not try to wrap every parameter in 3ds Max.

The target architecture is:
- cheap context tools
- strong inspection tools
- narrow typed mutation tools
- a small verified workflow layer for common production tasks
- raw MAXScript only for the long tail

If a task is common and failure is costly, add a verified workflow.
If a task is rare or too broad, keep it in the inspect + primitive-tool layer.
