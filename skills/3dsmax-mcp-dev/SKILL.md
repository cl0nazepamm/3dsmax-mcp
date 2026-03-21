---
name: 3dsmax-mcp-dev
description: Rules, tool choices, and workflow patterns for AI agents working with 3ds Max via MCP. Covers the native C++ bridge, plugin introspection, scene organization, material workflows, and MAXScript pitfalls.
---

# 3dsmax-mcp Skill Guide

Principles:
- Prefer dedicated tools over raw MAXScript
- Prefer SDK introspection over MAXScript reflection
- Prefer verified workflows over optimistic success strings
- Do NOT render unless asked — but `capture_multi_view` (quad view) is encouraged after building or modifying scenes so the user can see the result

## 1. Deep SDK Introspection (Use First)

When encountering an unfamiliar class, plugin, or object — **use C++ SDK introspection first**. These read the DLL class registry directly. Faster and more complete than MAXScript's `showClass`/`getPropNames`.

**Tool hierarchy:**
1. **`introspect_class`** — Full API of any class: ParamBlock2 params (names, types, defaults, ranges), FPInterface functions/properties. Works on any class.
2. **`introspect_instance`** — Same but on a live object with current values + modifier stack + material params. Add `include_subanims:true` for animation tree.
3. **`discover_plugin_classes`** — Enumerate ALL classes from DLL directory. Filter by superclass or name pattern.

**Always prefer these over MAXScript reflection:**
- `introspect_class` > `inspect_plugin_class` (gets defaults, ranges, function signatures)
- `introspect_instance` > `inspect_properties` for plugin objects (catches params `getPropNames` misses)
- `discover_plugin_classes` > `list_plugin_classes` (scans every loaded DLL)

**Unknown plugin workflow:**
```
1. discover_plugin_classes pattern:"*Forest*"     → find classes
2. introspect_class class_name:"Forest_Pro"        → get full API
3. introspect_instance name:"ForestPack001"        → read live values
4. Proceed with edits — you now know every param, type, range, value
```

**Material/shader introspection:**
- `introspect_instance` reads the entire material tree in one call — every param, every texmap slot, all sub-materials with current values
- Use for renderer conversion workflows: read source material tree → map params → write to new material

**Deep SDK learning tools:**

These tools let you understand how 3ds Max works at the deepest level — class relationships, real-world usage patterns, reference graphs, and live events.

1. **`learn_scene_patterns`** — Analyze the current scene in one call. Returns frequency-sorted data on:
   - Which geometry/material/modifier/texmap classes are used and how often
   - Common modifier stacks (e.g. "TurboSmooth | Skin | Skin Wrap" = character deform pipeline)
   - Material-to-geometry associations (e.g. "Shell Material → PolyMeshObject" = export pipeline)
   - Texture-to-material connections (e.g. "Bitmap → Physical Material")
   - **Use first** when opening an unfamiliar scene — instantly understand the entire production setup

2. **`walk_references`** — Walk the SDK reference graph from any object. Shows how materials, modifiers, controllers, and textures connect through Max's reference system.
   - Use to understand shader networks: "this Shell Material references Standard Surface + Physical Material"
   - Use to debug why changing one object affects another
   - `max_depth` controls detail (default 4, max 8)

3. **`map_class_relationships`** — Scan DLL directory to find which classes accept which reference types via ParamBlock2 params.
   - Shows "Physical Material accepts texturemaps in these slots: base_color_map, bump_map, ..."
   - Shows "Forest_Pro accepts nodes + texturemaps"
   - Filter by superclass or name pattern
   - **Use before wiring** — know which slots exist without guessing

4. **`watch_scene`** — Live event streaming from 3ds Max. Registers native SDK callbacks for:
   - node created/deleted, selection changes, modifier added
   - material assigned, file open, undo/redo, render start/end
   - Actions: `start`, `stop`, `get` (poll events), `clear`, `status`
   - Use `since=<timestamp>` for incremental polling
   - **Use during iterative work** — track what the user does between your calls

**Learning workflow for new scenes:**
```
1. learn_scene_patterns                           → understand the whole scene
2. walk_references name:"MainCharacter"           → map one object's dependencies
3. introspect_instance name:"MainCharacter"       → get live param values
4. map_class_relationships superclass:"material"  → learn what plugs into what
5. Now you understand the scene deeply — proceed with edits
```

## 2. Plugin & Tool Development (SDK Learning)

When the user is developing a tool, plugin, or automating a workflow and you need to understand SDK classes, parameters, or how things connect — **use native introspection, not documentation or guesswork.**

**Learning an unknown class or API:**
```
1. discover_plugin_classes pattern:"*ClassName*"   → find it in the DLL registry
2. introspect_class class_name:"ClassName"          → get ALL params, types, defaults, ranges, functions
3. map_class_relationships pattern:"ClassName"      → see what it accepts (nodes, materials, texmaps)
```

**Understanding how a live object works:**
```
1. introspect_instance name:"ObjectName"            → every param with current value
2. walk_references name:"ObjectName"                → full dependency graph (materials → textures → controllers)
3. introspect_instance name:"ObjectName" include_subanims:true → animation/controller tree
```

**Testing changes and verifying results:**
```
1. get_scene_delta capture:true                     → capture baseline
2. (make changes — create objects, assign materials, add modifiers)
3. get_scene_delta                                  → see exactly what changed (added/removed/modified with before/after values)
```

**Reverse-engineering a production scene:**
```
1. learn_scene_patterns                             → modifier stacks, material combos, class frequencies
2. walk_references name:"KeyObject"                 → map its dependency tree
3. map_class_relationships superclass:"material"    → learn all material slot wiring possibilities
```

**Watching user actions in real-time:**
```
1. watch_scene action:"start"                       → enable event tracking
2. (user works in Max — creates, selects, modifies)
3. watch_scene action:"get"                         → see every action with full detail
```

**Rules:**
- NEVER guess parameter names — use `introspect_class` to get the exact names, types, and ranges
- NEVER assume slot connections — use `map_class_relationships` to see what plugs into what
- NEVER skip verification — use `get_scene_delta` after mutations to confirm what actually changed
- When writing MAXScript that targets a specific class, introspect it first to get correct property names
- When building a C++ native handler, use `introspect_class` to understand the ParamBlock2 layout before writing SetValue calls

## 3. Default Workflow

1. **Context** — `get_bridge_status`, `get_session_context`, `inspect_active_target`
2. **Inspect** — `introspect_instance` (preferred) or `inspect_object` + `get_material_slots`
3. **Mutate** — use a dedicated tool (never `execute_maxscript` if a tool exists)
4. **Verify** — `get_scene_delta`, verified tool, or re-inspect

**Verified workflow tools** (action + readback in one call):
- `create_object_verified`, `assign_material_verified`, `set_material_verified`
- `add_modifier_verified`, `transform_object_verified`, `set_modifier_state_verified`
- `set_object_property_verified`

## 4. Scene Organization (Pure C++ SDK)

**Layers** — `manage_layers`:
- Actions: `list`, `create`, `delete`, `set_current`, `set_properties`, `add_objects`, `select_objects`
- Properties: hidden, frozen, renderable, color, boxMode, castShadows, rcvShadows, xRayMtl, backCull, rename, parent

**Groups** — `manage_groups`:
- Actions: `list`, `create`, `ungroup`, `open`, `close`, `attach`, `detach`

**Named Selection Sets** — `manage_selection_sets`:
- Actions: `list`, `create`, `delete`, `select`, `replace`

## 5. Tool Reference

### Scene reads
`get_scene_info` `get_selection` `get_scene_snapshot` `get_selection_snapshot` `get_scene_delta` `get_hierarchy`

### Objects
`get_object_properties` `set_object_property` `create_object` `delete_objects` `transform_object` `select_objects` `set_visibility` `clone_objects` `set_parent` `batch_rename_objects`

### Modifiers
`add_modifier` `remove_modifier` `set_modifier_state` `collapse_modifier_stack` `make_modifier_unique` `batch_modify`

### Materials
- Create + assign: `assign_material`
- Edit: `set_material_property`, `set_material_properties`
- Inspect: `get_material_slots`, `get_materials`
- Multi/Sub: `set_sub_material`
- Textures: `create_texture_map`, `set_texture_map_properties`, `create_material_from_textures`
- Shell + ORM: `create_shell_material`, `replace_material`, `batch_replace_materials`
- OSL: `write_osl_shader`

### Known Issues — Material Pipeline
- `create_material_from_textures` has no ORM packed texture support (OcclusionRoughnessMetallic)
- No UberBitmap (OSLMap) awareness — uses Bitmaptexture/ai_image instead of OSL UberBitmap2.osl
- No MultiOutputChannelTexmapToTexmap knowledge — cannot split R/G/B channels from a single map
- No Shell Material support — cannot wrap glTF + Arnold in dual-pipeline structure
- Arnold wiring uses ai_image instead of UberBitmap — misses channel splitting for packed maps
- AO compositing uses ai_layer_rgba instead of ai_multiply — inconsistent with standard Arnold workflows
- No concept of render vs export material slots (Shell originalMaterial / bakedMaterial)

### Viewport
- Fast: `capture_viewport`, `capture_model`
- Multi-angle grid: `capture_multi_view` (front/right/back/top stitched into one image)
- Fullscreen: `capture_screen` (requires `enabled=True`)

### External .max files (no scene load)
- `inspect_max_file` — OLE metadata + optional object names + class directory
- `search_max_files` — scan folder for objects matching pattern (batched, token-optimized)
- `merge_from_file` — selective merge with duplicate handling
- `batch_file_info` — parallel metadata from multiple files

### Plugin discovery
- `discover_plugin_surface`, `get_plugin_manifest`, `refresh_plugin_manifest`
- `inspect_plugin_class`, `inspect_plugin_constructor`, `inspect_plugin_instance`
- MCP resources: `resource://3dsmax-mcp/plugins/{name}/manifest|guide|recipes|gotchas`

### tyFlow
- Create: `create_tyflow`, `create_tyflow_basic_verified`
- Inspect: `get_tyflow_info` (enable `include_operator_properties` for deep readback)
- Edit: `modify_tyflow_operator`, `set_tyflow_shape`, `set_tyflow_physx`, `add_tyflow_collision`
- Simulate: `reset_tyflow_simulation`, `get_tyflow_particle_count`, `get_tyflow_particles`

### Controllers & wiring
- `assign_controller`, `inspect_controller`, `inspect_track_view`
- `list_wireable_params`, `wire_params`, `get_wired_params`, `unwire_params`

### Data Channel
- `add_data_channel`, `inspect_data_channel`, `set_data_channel_operator`, `add_dc_script_operator`

### Scene management
- `manage_scene` (hold/fetch/reset/save/info)
- `get_state_sets`, `get_camera_sequence`

## 6. When to Use `execute_maxscript`

Only when no dedicated tool exists:
- Quick experiments, animation keyframing, render/environment settings
- Custom scripted operations, unsupported host features

Never as default when a proper tool exists.

## 7. MCP Tool Pitfalls

- Fast/small models send `"foo"` instead of `["foo"]` for list params — all tool signatures use coerced types (`StrList`, `FloatList`, `IntList`, `DictList` from `src/coerce.py`) that auto-wrap single values into one-element lists. Any new tool with a `list[T]` param **must** use these types instead of bare `list[]`.

## 8. MAXScript Pitfalls

- **No parens with keyword args**: `Box width:10` not `Box() width:10`
- **Case-insensitive** but avoid ambiguous short names
- **Wrap in try/catch**: `try (...) catch (ex) (ex)` — errors otherwise appear as generic failures
- **Escape strings**: use `src.helpers.maxscript.safe_string`, use `MCP_Server.escapeJsonString` in MAXScript
- **`Noise` vs `Noisemodifier`**: texture map vs modifier
- **`(getDir #temp)`** is Max temp, not OS temp
- **.NET strings**: convert to MAXScript strings before using string methods
- `assign_controller`/`wire_params` track paths may fail with display-style tokens like `[#Transform][#Position][#Z Position]`; normalize to lowercase underscore form like `[#transform][#position][#z_position]`.

### UberBitmap + Shell Material Workflow
- `create_shell_material` builds a Shell Material wrapping Arnold (render) + glTF (export)
- Arnold render slot uses UberBitmap2.osl (OSLMap) for all texture loading — NOT ai_image or Bitmaptexture
- Packed ORM textures are split via `MultiOutputChannelTexmapToTexmap`:
  - Output 1 = Col (RGB), 2 = R, 3 = G, 4 = B, 5 = A, 6 = Luminance, 7 = Average
- Standard ORM wiring: BaseColor×AO(R) via `ai_multiply` → base_color, G → specular_roughness, B → metalness
- Shell Material slots: `originalMaterial` (slot 0, render) = Arnold, `bakedMaterial` (slot 1, export) = glTF
- `renderMtlIndex = 0` (Arnold for rendering), `viewportMtlIndex = 1` (glTF for viewport/export)
- When ORM texture detected in `_DEFAULT_CHANNEL_PATTERNS`, prefer packed split over separate roughness/metallic files
- `replace_material` / `batch_replace_materials` for swapping materials across objects

### OSL Shader Rules
- Use `write_osl_shader` — handles file I/O, compilation, global storage
- Shader function name MUST match `shader_name` exactly
- Use unique shader names — reusing hits stale cache
- OSLMap lowercases all param names — use lowercase keys
- After creation, wire via `set_material_property`

## 9. C++ SDK Pitfalls

- `is_array()` macro collision with MAXScript headers — use `.type() == json::value_t::array`
- `Matrix3(1)` deprecated in Max 2026 — use `Matrix3()` default
- `Modifier::GetName(bool localized)` — use `mod->GetName(false).data()`
- `ClassDesc::ClassName()` returns `const MCHAR*`, not a string class
- Arnold/scripted plugins don't register in DllDir under MAXScript names — fall back to `RunMAXScript` for creation
- `WStr::operator bool` deleted in Max 2026 — use `.data() && .data()[0]` checks
- Native scene-delta baselines must be scoped per pipe client and cleared on scene reset/fetch; one process-global snapshot leaks across simultaneous agents and unrelated scenes.

## 10. Architecture

```
Agent <-> FastMCP (Python/stdio) <-> Named Pipe <-> C++ GUP Plugin <-> 3ds Max SDK
                                  |
                                  +-> TCP:8765 fallback -> MAXScript listener
```

- 53 native C++ handlers via named pipe (pure SDK, 86-130x faster)
- Multi-instance pipe — multiple agents connect simultaneously
- Safe mode on pipe — blocks DOSCommand, ShellLaunch, deleteFile, python.Execute, createFile
- ScriptSource::NonEmbedded — .NET calls work through the pipe
- `client.native_available` routes tools to native or MAXScript path
- Remaining tools use MAXScript through `ExecuteMAXScriptScript()` in the C++ bridge

### Adding a native handler
1. Add handler function to relevant `.cpp` in `native/src/handlers/`
2. Declare in `native_handlers.h`
3. Route in `command_dispatcher.cpp`
4. Add source to `CMakeLists.txt`
5. Update Python tool with `if client.native_available:` + MAXScript fallback
6. Build → deploy → restart Max
