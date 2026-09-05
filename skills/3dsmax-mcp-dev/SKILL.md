---
name: 3dsmax-mcp
description: Tool choices, workflows, and MAXScript pitfalls for controlling 3ds Max via MCP.
---

# 3ds Max MCP — Agent Guide

## Tool Profile Routing

- **Full/core:** Operational tools such as `query_scene` and `create_object` are advertised directly; call the matching tool by name.
- **Progressive:** If the advertised surface contains only `list_toolsets`, `describe_toolset`, and `call_tool`, never call an operational name as a top-level MCP tool. Choose the relevant capability with `list_toolsets`, load only that group with `describe_toolset`, then invoke the selected operation through `call_tool(name=..., arguments=...)`.
- Do not describe every toolset up front. Load only the group needed for the current request; if the exact operational tool and arguments are already known, `call_tool` can dispatch it directly.

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
- **`resolve_node_refs`** — turn a name, handle, or absolute JSON-Pointer hierarchy path into a canonical handle/name/path identity; multiple selectors are cross-checked
- **`scene_qa(action="scan")`** — deterministic naming, transform, hierarchy/group, and timeline checks only; it never analyzes meshes, UVs, topology, normals, skinning, or visual quality
- **`get_session_context`** — bridge + capabilities + overview + selection (on demand only)

Object/material/plugin inspection:
- `inspect_object`, `inspect_properties`, `get_material_slots`, `get_materials`, `get_material_library`
- `analyze_node_orientation` — pivot, bbox, local axes, world matrix before rig/vehicle/camera transforms
- `introspect_class`, `introspect_instance`, `introspect_osl`, `discover_plugin_classes`, `map_class_relationships` — unfamiliar plugin APIs and exact param names
- Arnold materials such as `ai_standard_surface` may not appear in class discovery; inspect with `inspect_plugin_class` or `introspect_osl`

Mutation:
- Use object, modifier, material, controller, organization, and viewport tools when they match.
- Use `scene_patch` for a preflighted batch of rename, relative transform, visibility/freeze/render flags, or parenting edits that must commit as one native undo step. Pass the mutation-only `expected_scene_seq` from `resolve_node_refs` when stale targeting matters; selection and sub-object selection do not invalidate it.
- Use `scene_qa(action="fix")` only for its explicit deterministic naming fixes; preview with `dry_run=true` when the caller has not already approved the repair.
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
`query_scene` `resolve_node_refs` `scene_qa` `get_hierarchy` `get_instances` `get_dependencies`

### Atomic scene edits
- `scene_patch` — accepts NodeRefs (`handle`, `name`, or JSON-Pointer `path`), validates every operation before editing, rejects hierarchy/name conflicts, supports mutation-only stale-sequence guards and dry-run, and rolls back the whole native hold on apply failure; activity-only selection events remain observable without blocking writes
- Node handles are stable only within the current loaded scene/session. Cross-check a cached handle with `name` and/or `path`, and refresh after a scene reset/load.

### Objects
`get_object_properties` `analyze_node_orientation` `set_object_property` `create_object` `delete_objects` `transform_object` `select_objects` `set_visibility` `clone_objects` `set_parent` `batch_rename_objects`

### Modifiers
`add_modifier` `remove_modifier` `set_modifier_state` `set_modifier_property` `collapse_modifier_stack` `make_modifier_unique`

### Modeling
- `curve_model` — named local-plane curves, rounded profiles, tangent arcs, sweeps and resampled quad lofts with saved numeric controls. `preview` checks locally; `create` saves the recipe on one editable node; `read`/`update` use a model token to preserve placement and modifiers. Read [curve-construction.md](curve-construction.md) for recipes, supported operations and limits.
- `inspect_curve` / `edit_curve` — world knot/handle readback, sampled curve QA, labeled AGENT VIEWPORT capture and image targeting; atomic base edits guarded by `curve_token`. Re-inspect after topology changes. See [curve-construction.md](curve-construction.md).
- `create_mesh` — explicit world-space vertices and 1-based polygon faces, preserving quads/n-gons. Compute curved panels, lofted sections, and furniture geometry in Python, then send the arrays to Max.
- `loft_mesh` — matched cross sections become a quad cage with optional caps or closed path. Coordinates can use arithmetic expressions of named parameters, such as `width` or `backrest_curvature`. The definition persists on the mesh in the `.max` file. `read` returns compact controls (`include_definition=true` for sections); `update` changes parameters while preserving placement, subdivision, and cage IDs. Manual cage edits or instanced bases block parameter updates. Keep profile ordering consistent; no automatic resampling.
- `inspect_mesh` — base-cage vertex/edge/face IDs, centers, normals, mesh token, and optional labeled viewport capture. Omit the target for the one selected mesh. Filter by IDs, current selection, world bounds/proximity, face normal, open borders, or sharp edges.
- `pick_component` — normalized image x/y plus the capture's `expected_view` finds editable vertex/edge/face candidates. Defaults to the hit node; pass name/handle for silhouettes. It favors correspondence to the visible surface within screen tolerance. Inspect ambiguity and surface evidence, then pass the returned `mesh_token` to `mesh_edit(expected_mesh=...)`. Subdivision can separate the visible surface from its cage; proximity does not prove visibility.
- `mesh_edit` — batch select/move/scale/extrude/inset/bevel/chamfer/connect/bridge/delete/cap/relax in one undo step. Pass the inspected `expected_mesh` token when reusing IDs. Omitted component filters use the current sub-object selection; use `selection:{all:true}` explicitly for the whole mesh. World-space moves/scales preserve modifiers above an Editable Poly base; `convert=true` explicitly collapses a non-poly stack. Shared geometry changes in every instance.
- `geometry_qa` — evaluated mesh boundaries, non-manifold edges, inconsistent winding, degenerate/duplicate triangles, components and isolated vertices. Samples use world coordinates and evaluated snapshot IDs; re-inspect the base cage near a sample before editing. No intersection, thickness or outward-normal certification. Open seams and separate components may be intentional.
- `boolean_operation` — Boolean modifier (BooleanMod): apply union/subtract/intersect/merge/attach operands, list/retune/rename/extract them. Inline `cutters` build scratch primitives in the same call ({name, shape: box|cylinder|sphere, size, pos (bbox center), rot, operation?}) — consumed on apply, zero scene litter; `repeat` {count, axis, spacing} arrays every cutter (`vent_1..N`) for vents/ribs/window grids. Recipes: hole = Z-axis cylinder cutter overshooting both faces (rot to orient); slot = box cutter; panel line = cutter + `operation_option="imprint"`.
- `draw_spline` — spline shapes from world-space point lists; base-knot editing preserves modifiers above the spline (including CrossSection, Surface, and Sweep), holes via add_spline, renderable thickness
- `edit_vertices` — Editable_Poly verts in world space: get (filtered), move (soft falloff), set, conform to a spline or ray-projected onto geometry
- Curved-form recipe: `draw_spline` the reference profile → Lathe/Extrude/Bevel_Profile/Sweep via `add_modifier` → refine with `set_knots` or `edit_vertices conform`

### Materials
- Create + assign: `assign_material`, `create_material_from_textures`, `smart_import`, `palette_laydown`
- Share an existing material: `assign_material(names=[...], source_name="ExistingObject")` (or `source_handle`). Shares the full material/maps in one undo step; omit creation arguments.
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
- `agent_viewport(action="open")` reserves a shaded floating **AGENT VIEWPORT**. After opening, navigation and capture default to it. `start_minimized=true` parks it initially; `minimize`/`restore` park it between inspections. Captures require a visible on-screen panel and explicitly refuse while minimized. `status` reports `capture_ready`; `release` closes only the owned panel. Initial opening may briefly activate the panel before restoring user focus.
- `agent_viewport` also frames hierarchies, orbits (yaw/pitch degrees), pans (view-plane scene units), zooms (factor<1 closer), and picks surfaces (normalized image x/y, top-left origin). Pass the single capture's `view_token` as `expected_view` for picking; then inspect the hit node near its world point before editing base-cage components. View/scene changes invalidate the token; mesh IDs still require `expected_mesh`.
- Interactive preview: `agent_viewport(action="render", mode="activeshade"|"vray_ipr"|"vray_vfb"|"shaded")`. ActiveShade uses the assigned ActiveShade renderer (`renderer_source="production"` uses production if compatible). V-Ray previews enable progressive IPR and denoising; `vray_vfb` locks the VFB to the agent view. Start only when rendering is requested. Existing renders elsewhere are refused. Wait for `session_state="running"`, then use `action="capture"` or `"stop_capture"` (save image, then stop). Optional `crop=[x,y,width,height]` trims VFB pixels. Return to shaded before component targeting or minimizing. Captures do not certify convergence or completed denoising.
- Picking supports visible thick splines as well as geometry and returns a world surface normal. Use `draw_spline(action="get")` for spline knots. On thin panels, narrow face inspection by proximity and normal to separate the front from the back; frame the part before capturing labels.
- Aim/frame: `set_viewport` — world-space `eye` + `target`, named elevations, or `frame_names`; no camera node is created
- Fast: `capture_viewport`
- Multi-angle grid: `capture_multi_view` (`frame_root` frames a hierarchy). In the agent panel it does not hide other scene nodes; arbitrary-object isolation is not yet supported there. The legacy active-view route also temporarily isolates the hierarchy.
- `source="agent"` requires the agent panel; `source="active"` explicitly targets the user's active view. The default `auto` uses the agent panel once opened, and fails if that owned panel becomes unavailable instead of redirecting into the user's viewport. Release and reopen after a scene load or layout replacement.
- `inspect_mesh(capture=true)` uses the agent panel when open, drawing component labels into the saved image without adding overlays to the user's viewport.
- Fullscreen: `capture_screen` (requires `enabled=True`)
- V-Ray frame-buffer screen crop: `capture_screen(enabled=True, target="vray_vfb")`; optional `crop=[x,y,width,height]` trims inside its client area in physical pixels before resizing. The VFB must be visible and on screen; overlapping windows appear in the capture. No render is started by capture.
- Blocked production render recovery: `render_automations(action="cancel_capture", job_id=...)` saves visible VFB pixels and requests cancellation for the production job you armed and started. `capture_target="screen"` supports an explicit desktop crop for another renderer. Configure progressive sampling and its denoiser before starting; recovery cannot change blocked render settings. Cancellation is cooperative; check the done-signal separately and treat the image as partial.

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
- Unsupported controller operations, render/environment settings, custom one-off scripted operations

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
- `draw_spline`: all coordinates world-space; bezier `in_vec`/`out_vec` are absolute handle **positions**, not directions. Edits preserve a SplineShape base beneath modifiers. Bare parametric shapes auto-convert; a parametric base with modifiers requires `convert=true` to explicitly collapse. Conversion is reported as `converted_to_splineshape`.
- `edit_vertices`: edits the Editable_Poly **base** beneath the stack (cage editing — TurboSmooth above stays live); non-poly bases need `convert=true` (collapses) or `collapse_modifier_stack`. `conform` to geometry is ray-based — `skipped` verts had no hit along `axis`; unsigned tokens (`z`) cast both ways, signed (`-z`) one way.
- `snapshotAsMesh` evaluates the stack and returns a temporary world-space TriMesh. Read its vertices in `coordsys world` without applying `node.objectTransform` again; delete the temporary mesh afterward. Base-cage poly vertices still need the node's object transform.
- `list_wireable_params` paths include `[#Parameters]` levels — pass through to `wire_params` as-is.
- `create_shell_material`: `mcp_findMaterialByName` uses `sceneMaterials` — `getClassInstances Material` is invalid (Material is not a MAXClass).
- `material_class` must be the **material's** own class name, never a shortened token: `PhysicalMaterial`, not `Physical` — `Physical` is the Physical *Camera*. A non-material class name now returns `BAD_PARAM` with `hint.didYouMean`.
- `getHandleByAnim` formats as values like `12345P`; quote it as a string when building JSON, or the result is invalid JSON.
- MCP tripback is a structured `ToolEnvelope` dict (`ok`/`result`/`error`/`hint`), not a JSON string. Error envelopes may include `hint.suggested_tools`; tool-authored hints win over auto-hints.
- Success JSON payloads may include `message`; classify raw structured errors by `error`, `code`, or `status=error|failed`, not by `message` alone.
- Never issue mutating native tool calls concurrently: pre-guard bridges interleaved `theHold` transactions via nested message pumps (0xC0000005, then persistent corruption — phantom successes, wrong handles, bad class resolution). The main-thread executor now defers work items that arrive mid-item, but keep agent-side mutations sequential regardless.
- `USER_BUSY` means Max has an open undo operation. The native write was rejected before mutation; continue read-only planning and retry after that operation finishes. Do not bypass it with MAXScript or repeated immediate writes.

### Keyframes (`keyframe_tracks`)
- **`action=timeline`** — targetless read/set of `frame_rate`, `current_frame`, and `range_start`/`range_end`; omit setters for a read-only query.
- **`action=list`** — read-only inspection; pass `from_time`/`to_time` for `loopGaps`. Parent `numKeys` is often 0 — keys live on Bezier Float sub-controllers.
- **`delete_keys` / `move_keys` / `scale_keys`** — deterministic key-time edits. They require `time`/`times` or both `from_time` and `to_time`; retimes reject destination collisions. Use `time_offset`, or `time_scale` with optional `pivot_time`.
- **`resample` / `bake`** — native sampled keys over a required `from_time`/`to_time` window. `sample_step` defaults to one frame; `bake` replaces keys in-window by default, while `resample` preserves existing keys unless `replace_keys=true`. List, constraint, expression, script, and motion-capture controllers are intentionally skipped.
- **`normalize_tangents`** — normalizes bounded Bezier keys to smooth tangents by default; pass `key_type`, `in_type`, or `out_type` to choose another deterministic tangent style.
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
