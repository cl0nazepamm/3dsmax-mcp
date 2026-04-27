---
name: 3dsmax-mcp-dev
description: Rules, tool choices, and workflow patterns for agents working with 3ds Max via MCP.
---

# 3dsmax-mcp Skill Guide

## Rules

- Prefer dedicated tools over raw MAXScript.
- Prefer SDK/native introspection over MAXScript reflection.
- Do not render unless the user asks. Use `capture_viewport` or `capture_multi_view` for visual proof.
- External MCP defaults to `MCP_TOOL_PROFILE=core`; controller tools stay in core. Use `MCP_TOOL_PROFILE=full` only for specialty modules.
- Prefer OpenPBR for neutral PBR material creation/conversion. Use PhysicalMaterial only as fallback or when explicitly requested.
- After meaningful mutations, verify with `get_scene_delta`, re-inspection, or viewport capture.

## Default Workflow

1. Context: `get_bridge_status`, then `get_scene_snapshot` or `get_selection_snapshot`.
2. Inspect: `introspect_instance`, `get_material_slots`, `inspect_object`, or plugin discovery tools.
3. Mutate: use the dedicated MCP tool, not `execute_maxscript`, when one exists.
4. Verify: `get_scene_delta`, re-inspect, and capture viewport when useful.

## Introspection First

Use these before guessing class names, params, slots, controller paths, or plugin APIs:

- `introspect_class`: full ParamBlock2 and FPInterface data. Do not use for OSLMap.
- `introspect_instance`: live object/material/modifier/controller values; add `include_subanims:true` for animation trees.
- `introspect_osl`: OSLMap and scripted material/map reflection; for built-in shaders use `osl_file:"UberBitmap2"` style short names.
- `discover_plugin_classes`: enumerate classes from loaded DLLs.
- `learn_scene_patterns`: quick production-scene summary.
- `walk_references`: dependency graph for materials, modifiers, controllers, and textures.
- `map_class_relationships`: learn accepted node/material/texmap reference slots.
- `watch_scene`: event stream for user actions between agent calls.

Unknown plugin flow:

```text
discover_plugin_classes pattern:"*Name*"
introspect_class class_name:"DiscoveredClass"
introspect_instance name:"LiveObject"
map_class_relationships pattern:"DiscoveredClass"
```

Arnold scripted materials such as `ai_standard_surface` may not appear in native class discovery. Create with MAXScript class names and inspect with `inspect_plugin_class` or `introspect_osl`.

## Core Tool Surface

- Scene reads: `get_scene_info`, `get_scene_snapshot`, `get_selection_snapshot`, `get_scene_delta`, `get_hierarchy`.
- Objects: `create_object`, `delete_objects`, `transform_object`, `select_objects`, `set_object_property`, `clone_objects`, `set_parent`, `batch_rename_objects`.
- Modifiers: `add_modifier`, `remove_modifier`, `set_modifier_state`, `collapse_modifier_stack`, `make_modifier_unique`, `batch_modify`.
- Materials: `assign_material`, `set_material_property`, `set_material_properties`, `get_material_slots`, `get_materials`, `set_sub_material`, `create_texture_map`, `create_material_from_textures`, `palette_laydown`, `create_shell_material`, `replace_material`.
- Organization: `manage_layers`, `manage_groups`, `manage_selection_sets`.
- Controllers: `assign_controller`, `inspect_controller`, `inspect_track_view`, `add_controller_target`.
- Viewport: `capture_viewport`, `capture_multi_view`, `capture_screen`.
- External files: `inspect_max_file`, `search_max_files`, `merge_from_file`, `batch_file_info`.

Full profile adds specialty modules such as Data Channel, effects, floor-plan, RailClone, render, scattering, state sets, tyFlow, wire params, and standalone chat driver tools.

## Materials

- For new neutral PBR, prefer OpenPBR. Discover exact class/slot names when targeting a specific Max build.
- `create_material_from_textures` defaults to OpenPBR and falls back to PhysicalMaterial when OpenPBR is unavailable.
- `palette_laydown` loads texture folders into Compact Material Editor slots. `slot_content:"material"` creates OpenPBR preview materials; `slot_content:"bitmap"` uses raw Bitmaptexture slots; `slot_content:"pbr_material"`/`"full_pbr"` groups texture sets.
- Material Editor palette loaders must distinguish raw Bitmaptexture slots from OpenPBR preview-material slots. OpenPBR preview swatches should set `specular_color` to black.
- `get_material_slots slot_scope:"all" include_values:true` can be large on OpenPBR, Physical, and Arnold materials. Prefer `slot_scope:"map"` unless every param is needed.
- For OSL, use `write_osl_shader`, then `introspect_osl`, then wire with `set_material_property`.
- UberBitmap2.osl lives under `(getDir #maxroot) + "OSL\\UberBitmap2.osl"`; do not search for it.
- Packed ORM uses `MultiOutputChannelTexmapToTexmap`: output 2=R/AO, 3=G/roughness, 4=B/metalness.

## Controllers

- Use `inspect_track_view` before targeting a controller path.
- `list_wireable_params` returns paths with grouping like `[#Object (Box)][#Parameters][#height]`; pass these through as-is.
- `get_wired_params` returns `[#name]` paths; pass directly to `unwire_params`.
- `assign_controller` and `set_controller_props` accept string or numeric values in `params`.
- `add_controller_target` only works on script, expression, and constraint controllers. Use `assign_controller controller_type:"float_script"` when node references are needed.
- If display-style paths fail, normalize to lowercase underscore form like `[#transform][#position][#z_position]`.

## Scene Organization

- Use `manage_layers` for layer list/create/delete/current/properties/add/select. Supported properties include hidden, frozen, renderable, color, boxMode, castShadows, rcvShadows, xRayMtl, backCull, rename, parent.
- Use `manage_groups` for list/create/ungroup/open/close/attach/detach.
- Use `manage_selection_sets` for list/create/delete/select/replace.
- For instance grouping, use `get_instances` before moving objects into layers.

## MAXScript

Use `execute_maxscript` only when no dedicated tool exists: custom scripted ops, animation keyframing, render/environment settings, or temporary probes.

Do not use `execute_maxscript` for object creation, transforms, property setting, material assignment, selection, batch edits, or inspection when a tool exists.

Before writing unfamiliar MAXScript, read the relevant reference file in `skills/3dsmax-mcp-dev/`:

- `maxscript-core-syntax.md`
- `maxscript-common-patterns.md`
- `maxscript-3dsmax-objects.md`
- `maxscript-mesh-poly-ops.md`
- `maxscript-materials-textures.md`
- `maxscript-animation-controllers.md`
- `maxscript-rendering-cameras.md`
- `maxscript-splines-shapes.md`
- `maxscript-scripted-plugins.md`
- `maxscript-ui-rollouts.md`

Pitfalls:

- Keyword args: `Box width:10`, not `Box() width:10`.
- Wrap probes in `try (...) catch (ex) (ex)`.
- Escape JSON output with `MCP_Server.escapeJsonString`.
- Convert .NET strings before MAXScript string methods.
- `(getDir #temp)` is Max temp, not OS temp.
- `Noise` is a texture map; `Noisemodifier` is a modifier.
- `CompositeTexturemap name:"..."` can route `name` into plugin params; instantiate `CompositeTexturemap()` first, then assign `.name`.

## Tool And Action Discovery

- `list_macroscripts`: macros by category/pattern.
- `list_action_tables`: menu/shortcut action tables.
- `introspect_interface`: FPInterface functions, properties, enums, and live values.
- `invoke_interface`: call FPInterface functions/set properties without MAXScript parsing.
- `run_macroscript`: execute a discovered macroscript.
- To open Unwrap UVW editor, call `modifierInstance.edit()` on the `Unwrap_UVW` modifier; the `OpenUnwrapUI` macroscript does not open the editor.

## Standalone Chat

In-Max chat runs inside 3ds Max and dispatches through the same `CommandDispatcher`; `safe_mode` still guards `execute_maxscript`.

Default token controls in `%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini` `[llm]`:

- `prompt_mode=compact|full|none`
- `tool_profile=core|full`
- `include_scene_snapshot=true|false`
- `max_scene_roots`, `max_prompt_chars`, `max_tool_result_chars`, `max_history_tool_chars`, `max_tool_summary_chars`, `max_display_tool_chars`, `max_tool_loops`

Slash commands are client-side: `/reload`, `/clear`, `/help`.
