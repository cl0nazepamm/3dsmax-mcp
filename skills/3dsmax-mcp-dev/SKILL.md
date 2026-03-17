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

## TOP PRIORITY: Deep SDK Introspection

When you encounter an unfamiliar class, plugin, or object — **use the C++ SDK introspection tools first**. These return the complete API surface directly from the DLL class registry. They are faster, more complete, and more reliable than MAXScript reflection (`showClass`, `getPropNames`).

### Tool hierarchy (use in this order):
1. **`introspect_class`** — Get the full API of any class by name. Returns all ParamBlock2 parameters (names, types, defaults, ranges, animatable flags) and all FPInterface functions/properties. Works on ANY class — built-in or third-party plugin.
2. **`introspect_instance`** — Same but on a live scene object with ACTUAL current values. Also includes modifier stack params, material params. Add `include_subanims:true` for the full animation tree.
3. **`discover_plugin_classes`** — Enumerate ALL registered classes in Max's DLL directory. Filter by superclass (`geometry`, `modifier`, `material`, etc.) or name pattern (`*Vray*`, `Forest*`).

### When to use these vs MAXScript reflection:
- **Always prefer `introspect_class`** over `inspect_plugin_class` — it returns parameter defaults, ranges, and function signatures that MAXScript cannot see.
- **Always prefer `introspect_instance`** over generic `inspect_properties` for plugin objects — it reads ParamBlock2 values directly from the SDK, catching parameters that `getPropNames` misses.
- **Always prefer `discover_plugin_classes`** over `list_plugin_classes` for broad class enumeration — it scans every loaded DLL, not just MAXScript-visible classes.

### Example workflow for unknown plugin:
```
1. discover_plugin_classes pattern:"*Forest*"     → find all Forest Pack classes
2. introspect_class class_name:"Forest_Pro"        → get full parameter API
3. introspect_instance name:"ForestPack001"        → read live values
4. Now you know every parameter, its type, range, and current value — proceed with edits
```

These tools require the native C++ bridge plugin (`mcp_bridge.gup`). They fall back gracefully if not installed.

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
- Scene state: `manage_scene` (hold/fetch/reset/save/info)

### Viewport capture
- Single viewport: `capture_viewport`, `capture_model`
- **Multi-view grid: `capture_multi_view`** — captures front/right/back/top (configurable) and stitches into a labeled 2x2 grid image. Use this for spatial awareness instead of 4 separate captures. Saves tokens. Image saved to `%TEMP%\3dsmax_multiview.png`.
- Fullscreen: `capture_screen` (requires `enabled=True`)

### External .max file access
- Inspect without opening: `inspect_max_file` — reads OLE metadata (size, dates, author) without loading. Add `list_objects=True` for object names via MERGE_LIST_NAMES.
- Import objects: `merge_from_file` — selective merge by object name, with duplicate handling (rename/skip/merge/delete_old).
- Batch scan: `batch_file_info` — metadata from multiple files in parallel (std::async threads for OLE, main thread for object listing).
- **Search across files: `search_max_files`** — scans a folder recursively, lists all objects from every .max file, filters by wildcard pattern. Use for "where is the fridge?" / "which file has the character?" queries.

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
- **Native C++ GUP plugin** (`native/bin/mcp_bridge.gup`) loaded at 3ds Max startup
- Transport: **named pipe** (`\\.\pipe\3dsmax-mcp`) — no TCP, no MAXScript listener
- All 38+ native handlers use direct SDK API calls (IParamBlock2, IDerivedObject, IInstanceMgr, etc.)
- Non-native tools still send MAXScript through the pipe's `HandleMaxScript` path (ExecuteMAXScriptScript in C++)
- `client.native_available` flag routes each tool to native or MAXScript path
- Transport forced to `"pipe"` in `max_client.py` — TCP disabled entirely

Handler files in `native/src/handlers/`:
- `scene_handlers.cpp` — scene_info, selection, snapshots, find_class_instances, hierarchy
- `object_handlers.cpp` — create/delete/transform/select/clone/visibility/properties
- `modifier_handlers.cpp` — add/remove/state/collapse/unique/batch_modify
- `inspect_handlers.cpp` — inspect_object, inspect_properties, materials, instances, dependencies, material_slots, write_osl_shader
- `scene_manage_handlers.cpp` — set_parent, batch_rename, manage_scene
- `file_handlers.cpp` — inspect_max_file, merge_from_file, batch_file_info (OLE + MERGE_LIST_NAMES)
- `viewport_handlers.cpp` — capture_multi_view (GDI+ stitching)

When editing the bridge:
- C++ changes: rebuild (`cmake --build native/build --config Release`), deploy (`native/deploy.bat`), restart 3ds Max
- Python-side transport changes need MCP server restart
- New handler: add to handler .cpp, declare in `native_handlers.h`, route in `command_dispatcher.cpp`, add to `CMakeLists.txt`

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

### MAXScript execution and formatting
- In `execute_maxscript`, wrap diagnostic/error-prone snippets with `try(...) catch (ex) (ex)` and return explicit values, because `format`/`print` output is often swallowed and failures otherwise appear as generic `MAXScript execution failed`.

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

### OSL shader writing (Max 2026) — CRITICAL RULES
- Use `write_osl_shader` tool — it handles file I/O, compilation, and global storage
- The **shader function name MUST match `shader_name`** exactly (case-sensitive)
- Use **unique shader names** — reusing a name hits stale cache and silently fails
- OSLMap **lowercases all param names** — always use lowercase keys in properties dict
- The handler verifies compilation — if `compiledAs` returns "Example", the shader failed
- `color * float` IS valid: `EdgeColor * Boost` works fine
- Standard OSL globals: `N`, `I`, `P`, `u`, `v`, `time`, `dPdu`, `dPdv`
- Common functions: `mix()`, `pow()`, `abs()`, `dot()`, `normalize()`, `noise()`, `clamp()`, `smoothstep()`
- After creation, wire via: `set_material_property(name="Obj", property="base_color_shader", value="ShaderGlobalVar")`

Working one-shot template:
```
write_osl_shader(
    shader_name="MyShader",
    osl_code="""shader MyShader(
        color BaseColor = color(0.8, 0.2, 0.1),
        float Roughness = 0.5,
        output color result = 0
    )
    {
        result = BaseColor * Roughness;
    }""",
    properties={"basecolor": "color(1,0,0)", "roughness": "0.3"}
)
```

Internal details (for developers):
- OSLMap ONLY compiles from inline `OSLCode` string — file-read approaches silently fail
- Correct order: `OSLCode` first, then `OSLAutoUpdate`, then `OSLPath`
- File-read via `readLine`/`readDelimitedString` produces strings OSLMap rejects

### Plugin class registration gotcha
- Some plugins (Arnold, scripted materials) do NOT register classes in DllDir under their MAXScript name
- `FindClassDescByName("ai_standard_surface")` returns nullptr even when Arnold is loaded
- Fix: try SDK ClassDesc first, fall back to `RunMAXScript("className()")` for creation
- This affects `assign_material`, `create_object`, `add_modifier` for scripted/deferred plugin classes
- DllDir shows Arnold as "Map to Material" (ArnoldMapToMtl) but not individual shader classes

### Native C++ SDK pitfalls
- `is_array()` macro collision: MAXScript SDK defines `is_array` macro — use `.type() == json::value_t::array` instead of `.is_array()`
- `Matrix3(1)` deprecated in Max 2026 — use `Matrix3()` default constructor (identity by default)
- `Modifier::GetName()` takes `bool localized` param — use `mod->GetName(false).data()`
- `EnableModInViews()`/`EnableModInRender()` take NO arguments — separate Enable/Disable methods
- `QuatToEuler` signature: `(const Quat&, float*, int, bool)` not `(Quat, float*, float*, float*)`
- `ClassDesc::ClassName()` returns `const MCHAR*` directly, not a string class
- `CreateObjectNode()` takes 1 argument (Object*), not 2 — set name separately via `node->SetName()`

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
- Native C++ builds that include MaxScript headers should use MSVC `/EHa`; `/EHsc` triggers warning C4535 in SDK macros.
- For `.gup` targets with a `.def` file, omit `LIBRARY ...` to avoid LNK4070 `/OUT:...dll` mismatch warnings.

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
- MAXScript renaming: if a generic prefix/array loop fails, do separate matchPattern + substituteString passes per name prefix for reliable object renames in execute_maxscript.
