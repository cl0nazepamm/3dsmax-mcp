---
name: 3dsmax-mcp
description: Tool choices, workflows, and MAXScript pitfalls for controlling 3ds Max via MCP.
---

# 3ds Max MCP — Agent Guide

Principles:
- Match the user's request. Do not run setup, discovery, or scene analysis by habit.
- Do not call `get_bridge_status` or `get_session_context` as a session preamble.
- Prefer a dedicated MCP tool over raw MAXScript when a tool clearly matches the task.
- Do not render unless the user explicitly asks. Viewport capture is fine when visual proof is useful.
- Multiple Max instances: use **MCP Claim This Max** in the target window so tools hit the right session.

## Tool Choice

Scene reads — use **`query_scene(action=...)`**:
- `overview` | `filter` | `class` | `property` | `selection` | `delta`
- **`get_instances`** / **`get_dependencies`** — instancing and reference graph
- **`get_session_context`** — bridge + capabilities + overview + selection (on demand only)

Object/material/plugin inspection:
- `inspect_object`, `inspect_properties`, `get_material_slots`, `get_materials`, `get_material_library`
- `analyze_node_orientation` — pivot, bbox, local axes, world matrix before rig/vehicle/camera transforms
- `introspect_class`, `introspect_instance`, `introspect_osl`, `discover_plugin_classes`, `map_class_relationships` — unfamiliar plugin APIs and exact param names
- Arnold materials such as `ai_standard_surface` may not appear in class discovery; inspect with `inspect_plugin_class` or `introspect_osl`

Mutation:
- Use object, modifier, material, controller, organization, and viewport tools when they match.
- Verify after meaningful edits with `query_scene(action=delta)`, re-inspection, or viewport capture.

Debugging:
- `walk_references` — trace dependencies from a live object
- `watch_scene` — track user actions during an interactive session
- `execute_maxscript` — fallback only when no dedicated tool exists

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
`query_scene` `get_hierarchy` `get_instances` `get_dependencies`

### Objects
`get_object_properties` `analyze_node_orientation` `set_object_property` `create_object` `delete_objects` `transform_object` `select_objects` `set_visibility` `clone_objects` `set_parent` `batch_rename_objects`

### Modifiers
`add_modifier` `remove_modifier` `set_modifier_state` `set_modifier_property` `collapse_modifier_stack` `make_modifier_unique`

### Modeling
- `boolean_operation` — Boolean modifier (BooleanMod): apply union/subtract/intersect/merge/attach operands, list/retune/rename/extract them. Inline `cutters` build scratch primitives in the same call ({name, shape: box|cylinder|sphere, size, pos (bbox center), rot, operation?}) — consumed on apply, zero scene litter; `repeat` {count, axis, spacing} arrays every cutter (`vent_1..N`) for vents/ribs/window grids. Recipes: hole = Z-axis cylinder cutter overshooting both faces (rot to orient); slot = box cutter; panel line = cutter + `operation_option="imprint"`.
- `draw_spline` — spline shapes from world-space point lists; knot readback and editing, holes via add_spline, renderable thickness
- `edit_vertices` — Editable_Poly verts in world space: get (filtered), move (soft falloff), set, conform to a spline or ray-projected onto geometry
- Curved-form recipe: `draw_spline` the reference profile → Lathe/Extrude/Bevel_Profile/Sweep via `add_modifier` → refine with `set_knots` or `edit_vertices conform`

### Materials
- Create + assign: `assign_material`, `create_material_from_textures`, `smart_import`, `palette_laydown`
- Edit: `set_material_property`, `set_material_properties`
- Inspect: `get_material_slots`, `get_materials`, `get_material_library`
- Scratch libraries: `backup_material_library` saves `currentMaterialLibrary` / `meditMaterials` to `.mat`
- Multi/Sub: `set_sub_material`
- Textures: `create_texture_map`, `set_texture_map_properties`
- Dual pipeline: `create_shell_material`, `replace_material`, `batch_replace_materials`
- OSL: `write_osl_shader`

### Material notes
- `create_material_from_textures` and `smart_import` default to **OpenPBR**. Pass `material_class` for Physical, Arnold, Redshift, V-Ray, MaterialX, Octane, etc. (see tool tripback `hint.renderers`).
- `create_shell_material` wraps two scene materials in `Shell_Material` (render slot 0, export/viewport slot 1), or builds from `texture_folder` with `render_material_class` / `export_material_class`. Shell is a container, not a renderer.

### Viewport
- Fast: `capture_viewport`
- Multi-angle grid: `capture_multi_view`
- Fullscreen: `capture_screen` (requires `enabled=True`)

### External .max files (no scene load)
- `inspect_max_file`, `search_max_files`, `merge_from_file`, `batch_file_info`

### Plugin discovery
- `discover_plugin_surface`, `get_plugin_manifest`, `refresh_plugin_manifest`
- `inspect_plugin_class`, `inspect_plugin_constructor`, `inspect_plugin_instance`
- MCP resources: `resource://3dsmax-mcp/plugins/{name}/manifest|guide|recipes|gotchas`

### tyFlow
- Create: `create_tyflow`, `create_tyflow_preset`
- Inspect: `get_tyflow_info` (`include_operator_properties` for deep readback)
- Edit: `modify_tyflow_operator`, `set_tyflow_shape`, `set_tyflow_physx`, `add_tyflow_collision`
- Simulate: `reset_tyflow_simulation`, `get_tyflow_particle_count`, `get_tyflow_particles`

For tyFlow graph work (event/operator topology, wiring, transactional edits, operator
discovery, per-event census), read [tyflow-graphs.md](tyflow-graphs.md) completely before
acting. It covers `get_tyflow_graph`, `tyflow_apply_patch`, the wiring ledger and its
staleness rules, `harvest_tyflow_manifest` / `list_tyflow_operators`, `tyflow_event_census`,
and `capture_tyflow_editor` for foreign flows.

### Forest Pack
- `scatter_forest_pack` — surfaces + source geometry; auto footprint per variant

### Controllers & wiring
- `assign_controller`, `inspect_controller`, `inspect_track_view`, `set_controller_props`, `add_controller_target`
- `list_wireable_params`, `wire_params`, `get_wired_params`, `unwire_params`

### Procedural graph systems

For Data Channel or Max Creation Graph work, read [procedural-graphs.md](procedural-graphs.md) completely before acting. It contains the dedicated tool workflows, agentic compile/verify loop, safety gates, validation rules, and runtime pitfalls.

### Scene management
- `manage_scene` (hold/fetch/reset/save/info)
- `get_state_sets`, `get_camera_sequence`

## When to Use `execute_maxscript`

**Almost never.** Only when there is genuinely no dedicated tool:
- Animation keyframing, render/environment settings, custom one-off scripted operations

**Do not use for:** anything a dedicated tool already does — properties, objects, materials, selection, batch ops, inspection.

### Passing code through `execute_maxscript` (serialization gotchas)

The `code` string is delivered as a JSON value, so it is **un-escaped once before MAXScript ever parses it**. A generic `parse error (BAD_PARAM)` with no line number almost always means the string was corrupted in transit — **not** that your logic is wrong. `if/then/else`, chained `and`, `not`, and `for` loops all parse fine on their own; the failures are escaping artifacts. Confirmed causes and fixes:

- **Backslashes in string literals break the literal.** `"C:\Users\...\textures\"` arrives with single backslashes, so MAXScript reads `\"`, `\U`, etc. as escapes — the trailing `\"` eats the closing quote and the string never terminates. **Use forward slashes in path literals** (`"C:/Users/.../textures/"` — Max accepts them on Windows), or derive paths from runtime values (`getFilenamePath`/`getFilenameFile`) instead of hardcoding.
- **`\n` / `\t` inside `"..."` become real control chars** and corrupt the literal the same way. Don't embed escapes in strings you send; build output without them.
- **Keep the whole script on one line, statements separated by `;`.** Multi-line code through the transport is unreliable; `;` is not.
- **Debug tell:** on a `BAD_PARAM` parse error, shrink to a known-good core — `try ( local n=0; for x in (getClassInstances C) do (...); n ) catch (getCurrentException() as string)` — and add pieces back. The piece that reintroduces a `\` or `\n` in a *literal* is the culprit.

## MCP Tool Pitfalls

- `set_modifier_property`: `name` + `modifier_index` (1-based) for one modifier; `modifier_class` + `names` for batch. Inspect with `inspect_properties(target="modifier")` first.
- `smart_import`: default `lod_filter="lod0"`. Shared maps match on asset id; variant meshes in a bundle folder with `Textures/` share one material key — omit `name_pattern` for all variants.
- `palette_laydown`: `sample_mode="random_per_subfolder"` for large per-subfolder asset libraries; `overflow_mode="palette_then_library"` when more than 24 picks.
- `scatter_forest_pack`: needs non-zero `widthlist`/`heightlist` per geometry item. Hide source meshes after scatter.
- `get_material_slots`: prefer `slot_scope="map"` unless you need every param (`slot_scope="all"` + `include_values:true` is huge on Arnold/Physical).
- `create_object`: default `pos_mode="ground"` — `pos` is bottom-center contact, not bbox center. Tripback includes `bbox`, `placement`, `groundContact`.
- Box: `width=X`, `length=Y`, `height=Z`.
- `boolean_operation`: non-live operands are **consumed** — scene node deleted, geometry captured; the operand keeps its node name inside the modifier (rename cutters *before* applying). `live=true` keeps the node (hidden) for later transform tweaks at extra eval cost. Never consume a node other tools still reference by name. Prefer inline `cutters` over scene-node cutters for cuts — pre-named, atomic, no litter on failure.
- `draw_spline`: all coordinates world-space; bezier `in_vec`/`out_vec` are absolute handle **positions**, not directions. Edit actions auto-convert parametric shapes to SplineShape (reported as `converted_to_splineshape`).
- `edit_vertices`: edits the Editable_Poly **base** beneath the stack (cage editing — TurboSmooth above stays live); non-poly bases need `convert=true` (collapses) or `collapse_modifier_stack`. `conform` to geometry is ray-based — `skipped` verts had no hit along `axis`; unsigned tokens (`z`) cast both ways, signed (`-z`) one way.
- `snapshotAsMesh` evaluates the stack but exposes local-space vertices; multiply vertices by `node.objectTransform` for world-space measurements and delete the temporary mesh afterward.
- `list_wireable_params` paths include `[#Parameters]` levels — pass through to `wire_params` as-is.
- `create_shell_material`: `mcp_findMaterialByName` uses `sceneMaterials` — `getClassInstances Material` is invalid (Material is not a MAXClass).
- `material_class` must be the **material's** own class name, never a shortened token: `PhysicalMaterial`, not `Physical` — `Physical` is the Physical *Camera*. A non-material class name now returns `BAD_PARAM` with `hint.didYouMean`.
- `getHandleByAnim` formats as values like `12345P`; quote it as a string when building JSON, or the result is invalid JSON.
- MCP tripback is a structured `ToolEnvelope` dict (`ok`/`result`/`error`/`hint`), not a JSON string. Error envelopes may include `hint.suggested_tools`; tool-authored hints win over auto-hints.
- Success JSON payloads may include `message`; classify raw structured errors by `error`, `code`, or `status=error|failed`, not by `message` alone.
- Never issue mutating native tool calls concurrently: pre-guard bridges interleaved `theHold` transactions via nested message pumps (0xC0000005, then persistent corruption — phantom successes, wrong handles, bad class resolution). The main-thread executor now defers work items that arrive mid-item, but keep agent-side mutations sequential regardless.
- Native cross-version builds: `INode::IsSceneNode()` is unavailable in the Max 2023 SDK; use `!node->IsSceneXRefNode()` when the actual requirement is excluding XRef-only dependents.
- Native cross-version builds: `Interface::GetMatLibFileName()` returns `const MCHAR*` in Max 2023–2024 and `MSTR` in Max 2025+; wrap the return behind `MAX_SDK_VERSION` before converting to UTF-8.
- Native GDI+ capture: initialize lazily outside the DLL loader lock, and do not call `GdiplusShutdown` when a detached capture worker failed to drain during GUP shutdown.
- Native viewport proof capture: guard viewport, hidden, and selection state with RAII; reserve unique outputs and validate GDI+ status, decoded dimensions, and nonzero file size before returning.
- Native class discovery: iterate filtered `SubClassList` entries with `GetFirst(ACC_PUBLIC)` / `GetNext(ACC_PUBLIC)` and index `operator[]` with those returned zero-based indices; `1..Count(...)` walks past the end and can surface as an unknown main-thread exception.
- Native reference cloning: reuse one `RemapDir` for a related object graph and call `Backpatch()` before serializing, but use a fresh `RemapDir` per independent top-level material-library entry or repeated source pointers collapse and change MAXScript `copy` counts.
- Native class labels: `ClassDesc::InternalName()` is not always `(classOf value) as string` (`OpenPBR` vs `OpenPBR_Material`); on the main thread use the MAXScript C++ `MAXClass` metadata registry inside `ScopedMaxScriptEvaluationContext` for script-visible names without evaluating MAXScript.
- Native class enumeration from a GUP: `MAXClass::classes`, `MAXClass::n_classes`, and `MAXSuperClass::complete_init()` are declared but not exported by `MaxScrpt.lib`, while `MAXSuperClass::get_classes()` can throw from a GUP; enumerate `SubClassList` public and private entries, resolve each token with `MAXClass::lookup_class` inside `ScopedMaxScriptEvaluationContext`, and sort by unsigned `Class_ID` to match MAXScript `.classes`.
- Native cross-version MAXScript metadata: Max 2023 only exposes `MAXClass::lookup_class(Class_ID*, ...)`, while 2024+ also accepts const/reference overloads; pass a mutable local `Class_ID` by pointer for one source path across 2023–2027.
- Native object creation: every returned `ReferenceTarget` needs a persistent owner such as an assigned node, material library, or reference maker before the handler returns.
- Native capture output: sanitized node names can collide case-insensitively on Windows; disambiguate same-call filenames with the anim handle.
- Native instance grouping: never use a null base-object pointer as a shared identity key; keep unresolved roots in separate groups.
- Native MAXScript parity: superclass tokens are case-sensitive contract values such as `material` and `textureMap`, not UI labels like `Material` and `TextureMap`.
- Standalone chat bypasses Python result postprocessors; native batch/file handlers must emit a top-level error status when any non-skipped item fails.

### Keyframes (`keyframe_tracks`)
- **`action=list`** — read-only inspection; pass `from_time`/`to_time` for `loopGaps`. Parent `numKeys` is often 0 — keys live on Bezier Float sub-controllers.
- **`action=loop`** — copies evaluated pose from `from_time` to `to_time` parent-first; use for parented reflection rigs (e.g. `Plane001` → children). Defaults: frames 1→100.
- **`action=match`** with `order=hierarchy` — same parent-first copy as `loop` when closing endpoints on rigged hierarchies.
- Prefer **`value`/`move` on keyed tracks** over `transform_object` for animated objects — `transform_object` rewrites keys at the current slider frame.
- **`tracks`** accepts exact tokens only: `all`, `position`/`pos`, `rotation`/`rot`, `scale`/`scl`, `transform`/`tm` — not substring matches.

## MAXScript Pitfalls

- **No parens with keyword args**: `Box width:10` not `Box() width:10`
- **Wrap in try/catch**: `try (...) catch (ex) (ex)`
- **`Noise` vs `Noisemodifier`**: texture map vs modifier
- **`(getDir #temp)`** is Max temp, not OS temp
- **.NET strings**: convert to MAXScript strings before string methods
- Controller/wire paths: normalize display tokens like `[#Z Position]` to `[#z_position]`
- TCP fallback is opt-in; prefer the native bridge, and if Max viewport interaction stutters while fallback is running, stop the fallback and use the native bridge path.

### OSL
- Use `write_osl_shader` for file I/O and compilation
- Use `introspect_osl` before wiring — not `introspect_class` on OSLMap (massive output)
- Shader function name must match `shader_name`; use unique names (cache reuse)
- OSLMap lowercases param names

## MAXScript Reference (bundled)

Read the relevant reference file before writing unfamiliar MAXScript:

| File | Covers |
|------|--------|
| `maxscript-core-syntax.md` | Variables, scope, types, operators, control flow |
| `maxscript-common-patterns.md` | Undo/animate blocks, callbacks, file I/O |
| `maxscript-3dsmax-objects.md` | Nodes, transforms, hierarchy, properties |
| `maxscript-mesh-poly-ops.md` | Sub-object mesh/poly ops |
| `maxscript-materials-textures.md` | Materials, texmaps, PBR |
| `maxscript-animation-controllers.md` | Controllers, constraints, wire params |
| `maxscript-rendering-cameras.md` | Render settings, cameras, environment |
| `maxscript-splines-shapes.md` | Splines and shapes |
| `maxscript-scripted-plugins.md` | Scripted geometry, modifiers, utilities |
| `maxscript-ui-rollouts.md` | Rollout UIs and dialogs |

### Unwrap UVW
- Open the editor: `$Box001.modifiers[#Unwrap_UVW].edit()` — not the `OpenUnwrapUI` macro alone

## Standalone Chat (WIP)

Experimental in-Max chat (Customize UI → MCP → MCP Chat). Prefer external MCP for production.

- Same tools and `safe_mode` as external MCP
- Call `query_scene` / `inspect_object` for scene state — not auto-injected by default
- Slash commands: `/reload`, `/clear`, `/help`
