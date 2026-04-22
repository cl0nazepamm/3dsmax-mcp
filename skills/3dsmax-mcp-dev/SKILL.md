---
name: 3dsmax-mcp-dev
description: Rules, tool choices, and workflow patterns for AI agents working with 3ds Max via MCP. Covers SDK introspection, scene organization, material workflows, and MAXScript pitfalls.
---

# 3dsmax-mcp Skill Guide

Principles:
- Prefer dedicated tools over raw MAXScript
- Prefer SDK introspection over MAXScript reflection
- Do NOT render unless asked — but `capture_multi_view` (quad view) is encouraged after building or modifying scenes so the user can see the result

## 1. Deep SDK Introspection (Use First)

When encountering an unfamiliar class, plugin, or object — **use SDK introspection tools first**. These read the DLL class registry directly. Faster and more complete than MAXScript's `showClass`/`getPropNames`.

**Tool hierarchy:**
1. **`introspect_class`** — Full API of any class: ParamBlock2 params (names, types, defaults, ranges), FPInterface functions/properties. Works on any class. **Blocked for OSLMap** — use `introspect_osl` instead.
2. **`introspect_instance`** — Same but on a live object with current values + modifier stack + material params. Add `include_subanims:true` for animation tree.
3. **`introspect_osl`** — Lightweight reflection for OSLMap and any material/texturemap class. Creates a temp instance, dumps properties with types, interfaces, and output channels. For OSLMap, use `osl_file` param to load a shader (e.g. `osl_file:"UberBitmap2"`). Short names resolve to `(getDir #maxRoot)/OSL/<name>.osl`.
4. **`discover_plugin_classes`** — Enumerate ALL classes from DLL directory. Filter by superclass or name pattern.

**Always prefer these over MAXScript reflection:**
- `introspect_class` > `inspect_plugin_class` (gets defaults, ranges, function signatures)
- `introspect_osl` for OSLMap and scripted material/map classes (bounded output, handles dynamic params)
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
NOTE: Arnold materials (ai_standard_surface, etc.) are scripted plugins — `discover_plugin_classes` and `introspect_class` won't find them. Create via MAXScript: `ai_standard_surface()`. Use `inspect_plugin_class` or `introspect_osl` for reflection instead.

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

## 3. Default Workflow

1. **Context** — `get_bridge_status`, `get_scene_snapshot`
2. **Inspect** — `introspect_instance` (preferred) or `inspect_object` + `get_material_slots`
3. **Mutate** — use a dedicated tool (never `execute_maxscript` if a tool exists)
4. **Verify** — `get_scene_delta` or re-inspect after mutation

## 4. Scene Organization

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
- Fast: `capture_viewport`
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
- Create: `create_tyflow`, `create_tyflow_preset`
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

**Almost never.** Only when there is genuinely no dedicated tool:
- Animation keyframing, render/environment settings, custom scripted operations

**DO NOT use execute_maxscript for:**
- Anything a dedicated tool already does — even if it feels faster to write a script
- Batch operations — call the dedicated tool in a loop, do not write MAXScript `for` loops
- Setting properties — use `set_object_property`, not `execute_maxscript("$obj.prop = val")`
- Creating objects — use `create_object`, not `execute_maxscript("Box()")`
- Assigning materials — use `assign_material`, not MAXScript
- Selecting objects — use `select_objects`, not `execute_maxscript("select $obj")`
- Inspecting — use `inspect_object`/`introspect_instance`/`introspect_osl`, not `showProperties`

If you catch yourself writing MAXScript that a tool already handles, stop and use the tool.

## 7. MCP Tool Pitfalls

- List params accept a single value or a list — both `"foo"` and `["foo"]` work.
- `get_material_slots` with `slot_scope:"all"` + `include_values:true` returns 40+ params on complex materials (Physical, Arnold). Prefer `slot_scope:"map"` (default) unless you need every param.
- `assign_controller` / `set_controller_props` `params` dict values accept both strings and numbers — both `{"seed": 42}` and `{"seed": "42"}` are valid.
- In standalone chat mode, always specify primitive sizes explicitly when calling `create_object` — don't rely on defaults filling in for omitted dimensions.
- `list_wireable_params` returns paths with `[#Parameters]` grouping level (e.g. `[#Object (Box)][#Parameters][#height]`). Pass them through to `wire_params`/`assign_controller`/`unwire_params` as-is — the bracket levels are normalized for you.
- `get_wired_params` returns paths with `[#name]` format. Pass directly to `unwire_params` — both `[name]` and `[#name]` formats are accepted.
- `add_controller_target` only works on script, expression, and constraint controllers. Noise/Bezier/other controllers will return a clear error message. Use `assign_controller` with `controller_type:"float_script"` if you need node references.

## 8. MAXScript Pitfalls

- **No parens with keyword args**: `Box width:10` not `Box() width:10`
- **Case-insensitive** but avoid ambiguous short names
- **Wrap in try/catch**: `try (...) catch (ex) (ex)` — errors otherwise appear as generic failures
- **Escape strings**: use `MCP_Server.escapeJsonString` when building JSON output in MAXScript
- **`Noise` vs `Noisemodifier`**: texture map vs modifier
- **`(getDir #temp)`** is Max temp, not OS temp
- **.NET strings**: convert to MAXScript strings before using string methods
- `assign_controller`/`wire_params` track paths may fail with display-style tokens like `[#Transform][#Position][#Z Position]`; normalize to lowercase underscore form like `[#transform][#position][#z_position]`.

### UberBitmap + Shell Material Workflow
- `create_shell_material` builds a Shell Material wrapping Arnold (render) + glTF (export)
- Arnold render slot uses UberBitmap2.osl (OSLMap) for all texture loading — NOT ai_image or Bitmaptexture
- UberBitmap2.osl path: `(getDir #maxroot) + "OSL\\UberBitmap2.osl"` — do NOT search for it
- All built-in OSL shaders live in `<maxroot>\OSL\`
- Packed ORM textures are split via `MultiOutputChannelTexmapToTexmap`:
  - Output 1 = Col (RGB), 2 = R, 3 = G, 4 = B, 5 = A, 6 = Luminance, 7 = Average
- Standard ORM wiring: BaseColor×AO(R) via `ai_multiply` → base_color, G → specular_roughness, B → metalness
- Shell Material slots: `originalMaterial` (slot 0, render) = Arnold, `bakedMaterial` (slot 1, export) = glTF
- `renderMtlIndex = 0` (Arnold for rendering), `viewportMtlIndex = 1` (glTF for viewport/export)
- When ORM texture detected in `_DEFAULT_CHANNEL_PATTERNS`, prefer packed split over separate roughness/metallic files
- `replace_material` / `batch_replace_materials` for swapping materials across objects

### OSL Shader Rules
- Use `write_osl_shader` — handles file I/O, compilation, global storage
- Use `introspect_osl` to inspect any OSL shader's properties and output channels before wiring
- Shader function name MUST match `shader_name` exactly
- Use unique shader names — reusing hits stale cache
- OSLMap lowercases all param names — use lowercase keys
- `introspect_class` is blocked for OSLMap (663K+ output) — always use `introspect_osl` instead
- After creation, wire via `set_material_property`

## 9. MAXScript Reference Files

This skill includes bundled MAXScript reference files for writing correct MAXScript. Read the relevant file BEFORE writing MAXScript code for unfamiliar areas.

| File | Covers |
|------|--------|
| `maxscript-core-syntax.md` | Variables, scope, types, operators, control flow, collections, strings |
| `maxscript-common-patterns.md` | Undo blocks, animate blocks, callbacks, file I/O, performance |
| `maxscript-3dsmax-objects.md` | Node creation, transforms, hierarchy, properties, superclasses |
| `maxscript-mesh-poly-ops.md` | Mesh/poly sub-object ops, vertex/edge/face manipulation |
| `maxscript-materials-textures.md` | Material creation, texmap wiring, Standard/Physical/Arnold |
| `maxscript-animation-controllers.md` | Controllers, constraints, expressions, wire params |
| `maxscript-rendering-cameras.md` | Render settings, cameras, environment, render elements |
| `maxscript-splines-shapes.md` | Spline creation, knots, interpolation, shape booleans |
| `maxscript-scripted-plugins.md` | Custom scripted geometry, modifiers, materials, utilities |
| `maxscript-ui-rollouts.md` | Rollout UIs, dialogs, controls, event handlers |

**IMPORTANT:** Before writing any MAXScript, READ the relevant file. Do not guess syntax.

**Location:** `skills/3dsmax-mcp-dev/` in the project root. Example:
```
Read: skills/3dsmax-mcp-dev/maxscript-materials-textures.md
```

## 10. Tool & Action Discovery

### Unwrap UVW Editor
- The macroscript `OpenUnwrapUI` does NOT open the UV editor window
- To open the editor: `modifierInstance.edit()` on the Unwrap_UVW modifier (e.g. `$Box001.modifiers[#Unwrap_UVW].edit()`)
- Action table "Unwrap UVW" has 228 actions including "Edit UVW's" (id 40005)
- Use `list_macroscripts` and `list_action_tables` to discover available commands — don't guess names

### System Discovery
- `list_macroscripts` — 4000+ macros, filter by category/pattern
- `list_action_tables` — 100+ tables with all menu/shortcut actions
- `introspect_interface` — full FPInterface dump (functions, properties, enums with live values)
- `invoke_interface` — call FPInterface functions + set properties directly, no MAXScript parsing
- `run_macroscript` — execute macroscripts by category + name
- Use these to discover any plugin's API surface before guessing MAXScript commands

## Standalone Chat Mode

When this file is loaded as the system prompt by the in-Max chat window (Customize UI → MCP → MCP Chat), you are running **inside** 3ds Max — not as an external MCP client.

- All MCP tools are available and callable.
- `safe_mode` still guards `execute_maxscript`. If a script is rejected you'll get `{"error": "Blocked by safe mode: ..."}` — surface that to the user rather than retrying with obfuscation.
- Don't reference external docs (Linear, Slack, web URLs) from the chat — you can't fetch them. Stick to tools, the scene, and what's in this skill file.
- The scene snapshot is re-injected into the system prompt each turn, so you have fresh state; you still need to call `get_selection_snapshot` / `inspect_object` / `get_scene_delta` for deep reads or after mutations.
- Slash commands handled client-side: `/reload` (reread config), `/clear` (drop conversation), `/help`. Don't tell the user to use tool calls for these.
