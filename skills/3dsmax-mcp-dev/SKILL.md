---
name: 3dsmax-mcp-dev
description: Rules, tool choices, and workflow patterns for AI agents working with 3ds Max via MCP. Covers SDK introspection, scene organization, material workflows, and MAXScript pitfalls.
---

# 3dsmax-mcp Skill Guide

Principles:
- Match the user's request. Do not run setup, discovery, scene analysis, or source-code inspection by habit.
- Prefer a dedicated MCP tool over raw MAXScript when a tool clearly matches the task.
- Use repo/source inspection only for code, build, packaging, or debugging requests, or when the MCP tool path is unavailable.
- Do not render unless the user explicitly asks. Viewport capture is fine when visual proof is useful.

## Tool Choice

Scene reads:
- `get_session_context`: compact bridge, capability, scene, and selection summary.
- `get_scene_snapshot`: scene counts, layers, roots, materials, modifiers.
- `get_selection_snapshot`: compact selected-object details.
- `learn_scene_patterns`: broader production-scene patterns when the user asks for analysis.

Object/material/plugin inspection:
- `inspect_object`, `inspect_properties`, `get_material_slots`, and `get_materials` cover normal live scene inspection.
- `introspect_class`, `introspect_instance`, `introspect_osl`, `discover_plugin_classes`, and `map_class_relationships` are for unfamiliar plugin APIs, exact parameter names, slot wiring, or SDK-level automation work.
- Arnold scripted materials such as `ai_standard_surface` may not appear in native class discovery. Create with MAXScript class names and inspect with `inspect_plugin_class` or `introspect_osl`.

Mutation:
- Use object, modifier, material, controller, organization, and viewport tools directly when they match.
- Verify after meaningful edits with `get_scene_delta`, re-inspection, or viewport capture when useful.

Debugging:
- `walk_references` helps trace dependencies from a live object.
- `watch_scene` can track user actions during an interactive session.
- `execute_maxscript` is a fallback for custom scripted operations, animation keyframing, render/environment settings, or temporary probes when no dedicated tool exists.

## Scene Organization

**Layers** — `manage_layers`:
- Actions: `list`, `create`, `delete`, `set_current`, `set_properties`, `add_objects`, `select_objects`
- Properties: hidden, frozen, renderable, color, boxMode, castShadows, rcvShadows, xRayMtl, backCull, rename, parent

**Groups** — `manage_groups`:
- Actions: `list`, `create`, `ungroup`, `open`, `close`, `attach`, `detach`

**Named Selection Sets** — `manage_selection_sets`:
- Actions: `list`, `create`, `delete`, `select`, `replace`

## Tool Reference

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
