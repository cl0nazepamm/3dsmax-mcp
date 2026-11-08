---
name: 3dsmax-mcp
description: Tool choices, workflows, and MAXScript pitfalls for controlling 3ds Max via MCP.
---

# 3ds Max MCP — Agent Guide

Each tool's description documents its parameters, modes and limits. This guide covers what no
single tool can: routing, tool choice, cross-tool workflows, safety rules and MAXScript pitfalls.

## Tool Profile Routing

- **Full/core:** operational tools such as `query_scene` and `create_object` are advertised directly; call them by name.
- **Progressive:** the common tools are advertised directly; every other tool is reached through `call_tool`. Never call an unlisted name as a top-level tool. Find one with `find_tools(query=...)`, which returns the best matches with their exact schemas, then run it with `call_tool(name=..., arguments=...)`. `list_toolsets` and `describe_toolset(toolset=..., tools=[...])` browse the groups. Tool names in this guide that are not listed are reached the same way.

## Principles

- Match the user's request. Do not run setup, discovery or scene analysis by habit, and do not call `get_bridge_status` or `get_session_context` as a preamble.
- Prefer a dedicated tool over raw MAXScript whenever one clearly matches.
- Do not render unless the user asks. Viewport capture is fine when visual proof helps.
- Verify meaningful edits with `query_scene(action="delta")`, re-inspection or a capture.
- Keep mutating calls sequential. Concurrent writes interleave undo transactions: a crash, then persistent corruption such as phantom successes and wrong handles.
- Multiple Max instances: the first native connection stays bound to that Max and never silently switches while it runs. Use `list_max_instances`, `select_max_instance(pid)`, `get_selected_max_instance` and `release_max_instance`. `MCP_MAX_PIPE` or `MCP_MAX_PID` pins the startup target (the pipe takes precedence); release also clears it.
  - **Shared selection:** the selection belongs to the MCP server process, so sessions that share one server share it, and one session's select moves the others. Pass `expected_pid` (every tool takes it) to any call that must reach one particular Max. A mismatch returns `TARGET_MISMATCH` with nothing sent. `selection_generation` in each reply's transport changes on every select, release or re-resolve.
  - **Default target after a restart:** a target the server picked by itself (`target_source` "default" or "claim") follows its Max's restart (the Max that started after it stopped answering), and the reply warns. A Max that was already open belongs to another session and is never taken over: until the restart is up, calls fail with retryable `TARGET_UNAVAILABLE` and nothing is sent; `select_max_instance` it to work there. Several new instances: `AMBIGUOUS`, select one. A claimed instance wins. Explicit and environment targets never move; they fail until you select or release.

## Tool Choice

- **Scene reads:** `query_scene` (overview/filter/class/property/selection/delta), `get_hierarchy`, `get_instances`, `get_dependencies`, `walk_references`. `resolve_node_refs` gives canonical handle/name/path identity. `scene_qa` checks naming, transforms, hierarchy and timeline only; it never judges meshes or visuals, and its `fix` repairs naming only (preview with `dry_run=true` unless approved).
- **Inspection:** `inspect_object`, `inspect_properties`, `analyze_node_orientation` (before rig, vehicle or camera transforms). For unfamiliar plugin APIs: `introspect_class`, `introspect_instance`, `discover_plugin_classes`, `map_class_relationships`, `inspect_plugin_class`. Arnold materials such as `ai_standard_surface` may be missing from class discovery; use `inspect_plugin_class` or `introspect_osl`.
- **Edits:** object, modifier, material, controller, organization (`manage_layers`, `manage_groups`, `manage_selection_sets`) and viewport tools. Use `scene_patch` for a preflighted batch of renames, relative transforms, flags or parenting committed as one undo step.
- **Fallbacks:** `execute_maxscript`, or `execute_python` (pymxs, needs safe mode off), only when no dedicated tool exists.
- **Scene files:** `manage_scene` (hold/fetch/reset/save/info). `inspect_max_file`, `search_max_files`, `merge_from_file` and `batch_file_info` work without loading a scene.
- **Interactive sessions:** `watch_scene` follows the user's actions.

## Workflows

### Identity and tokens
- Node handles are stable only within the loaded scene. Cross-check cached handles with `name` or `path`, and refresh after a reset or load.
- Pass `expected_scene_seq` from `resolve_node_refs` to `scene_patch` when stale targeting matters; selection changes do not invalidate it.
- Guarded edits take the token from a fresh inspection: `expected_mesh`, `expected_view`, `curve_token`, `expected_style`, `expected_controller`, plugin schema/state tokens, `expected_dialog`. A refused token means inspect again, never bypass the guard.

### Modeling
- Polygon edits: `inspect_mesh` or an AGENT VIEWPORT capture → `pick_component` with the capture's `view_token` as `expected_view` → `mesh_edit(expected_mesh=...)` → verify with `inspect_mesh`, `geometry_qa` or a capture.
- Construction: `create_mesh` for computed vertex/face arrays; `loft_mesh` for parameterized section lofts; `curve_model`, `inspect_curve` and `edit_curve` for curve-driven forms (read [curve-construction.md](curve-construction.md) first); `draw_spline` plus Lathe/Extrude/Bevel_Profile/Sweep through `add_modifier`, refined with spline knot edits or `edit_vertices` conform.
- Edits act on the editable base cage and preserve modifiers above it. Collapsing (`convert=true`, `collapse_modifier_stack`) happens only when requested.
- `boolean_operation`: prefer inline `cutters` (named, atomic, no scene litter). Non-live operands are consumed, so never consume a node other tools still reference by name.
- After placing parts that should sit on or join each other, run `contact_check` and judge reported depth against the design intent.

### Materials
- Create and assign: `assign_material`, `create_material_from_textures`, `smart_import`, `palette_laydown`. They default to OpenPBR; pass `material_class` for another renderer. Share an existing material with `assign_material(names=[...], source_name=...)`.
- Edit: `set_material_property`, `set_material_properties`, `set_sub_material`, `create_texture_map`, `set_texture_map_properties`. Switch pipelines with `create_shell_material`, `replace_material` and `batch_replace_materials`.
- `replace_material` applies `source_material` wherever `target_material` is used. `source_from` picks where the source is found, e.g. `"material_editor"` for a re-imported Cosmos copy. On `no_match`, check the direction before retrying; on `blocked`, do not swap the arguments.
- Audit: `get_material_slots` (prefer `slot_scope="map"`), `get_materials`, and `material_roles` (follow `next_offset`; check `complete` and `truncated`).
- OSL: `write_osl_shader`, then `introspect_osl` before wiring. The shader name must match `shader_name`; OSLMap lowercases parameter names.
- `material_class` is the material's own class name: `PhysicalMaterial`, not `Physical` (the camera).

### Lighting and plugin settings
- `lighting_capabilities` → `create_lights` → `inspect_lights` → `edit_lights`. Never infer integer enum meanings. RGB is linear in the rendering color space, Kelvin is explicit, and EXR/HDR inputs get no extra gamma.
- Other plugin settings: `inspect_plugin_class` or `inspect_plugin_instance` with `schema_version=2`, then `plugin_patch` with the returned schema and state tokens. Shared or animated resources need deliberate handling.

### Viewport and capture
- Open `agent_viewport(action="open")` before inspection captures. Navigation and captures then default to it and never redirect into the user's view; `source="active"` targets the user's view explicitly. Release and reopen after a scene load.
- `set_viewport` aims by eye and target without creating a camera. Use `capture_viewport`, `capture_multi_view` or `capture_screen(enabled=True)` for stills and frame-buffer crops.
- `capture_multi_view` views take names, `three_quarter`, `current` or `[x, y, z]` directions. `shading` and `edges` apply to that capture only: clay with edges shows topology. For animation, rigs and simulations, `frames` or `frame_count` give one view over time in a single image instead of repeated scrub-and-capture calls.
- Start interactive previews (`agent_viewport(action="render")`) and production renders (`render_automations`) only on request. A capture never certifies convergence or finished denoising.

### Animation and controllers
- Script controllers: `script_controller` inspect → validate → apply with `expected_controller`. A controller that returns zero at every frame has failed; inspect it with `sample_frames`.
- Other controllers: `assign_controller`, `inspect_controller`, `inspect_track_view`, `set_controller_props`, `add_controller_target`. Pass `list_wireable_params` paths, including their `[#Parameters]` levels, to `wire_params` unchanged; `get_wired_params` and `unwire_params` read and remove wiring.
- `keyframe_tracks`: `timeline` reads or sets frame rate, current frame and range. `list` inspects keys; parent `numKeys` is often 0 because keys live on Bezier Float sub-controllers. Retimes need `time`/`times` or both `from_time` and `to_time`. `bake` and `resample` skip list, constraint, expression, script and motion-capture controllers. Use `loop`, or `match` with `order=hierarchy`, for parented rigs. `tracks` takes exact tokens only: `all`, `position`/`pos`, `rotation`/`rot`, `scale`/`scl`, `transform`/`tm`.
- On animated objects, edit keys with `keyframe_tracks`; `transform_object` rewrites keys at the current frame.

### Plugins and assets
- tyFlow graph work: read [tyflow-graphs.md](tyflow-graphs.md) completely before acting.
- RailClone (7.3.5+): `get_railclone_style` → edit the XML → `set_railclone_style(expected_style=...)` → verify with `get_railclone_output`. Read [railclone.md](railclone.md) first.
- Data Channel and Max Creation Graph: read [procedural-graphs.md](procedural-graphs.md) completely before acting.
- Forest Pack: `scatter_forest_pack`, then hide the source meshes.
- Cosmos: `cosmos_search` → `cosmos_download` → `cosmos_import`. Repeating a completed import adds another instance.
- Plugin surfaces: `discover_plugin_surface`, `get_plugin_manifest`, and `resource://3dsmax-mcp/plugins/{name}/manifest|guide|recipes|gotchas`.

## Dialogs

- `execute_maxscript` and tool scripts run in Max's quiet mode: prompts such as `queryBox`, overwrite or missing-file warnings silently take their default answer. Pass `execute_maxscript(quiet=False)` when the user wants to be asked.
- Quiet mode would also skip the save-changes prompt (`checkForSave()` returns true, `resetMaxFile()` discards unsaved work, `loadMaxFile` never asks), so a script that mentions `resetMaxFile`, `loadMaxFile`, `fetchMaxFile`, `quitMax`, `checkForSave`, `max reset file`/`max file new`/`max file open`/`max fetch` or the Interface's `FileReset`/`FileFetch`/`LoadFromFile` runs with prompts shown: a "save changes?" prompt returns `BLOCKED_BY_DIALOG` instead of discarding unsaved work. Never add `#noPrompt` or `quiet:true` to those commands, or pass `quiet=True` with `allow_discard=True`, without the user's explicit OK: each discards unsaved changes. The check reads only the script's text: pass `quiet=False` when a script runs other scripts (`fileIn`, `python.ExecuteFile`, `macros.run`) that may reset, open or quit. While the scene has unsaved changes `execute_maxscript` and `execute_python` refuse such a script with `UNSAVED_CHANGES` and run nothing: ask the user, then save first or pass `allow_discard=True`. A `BLOCKED_BY_DIALOG` with `retryable: true` means only the unsaved-changes check waited on a dialog: answer it, then repeat the call. Edits made with undo off (plain execute_maxscript edits by default) don't mark the scene modified.
- Reset with `manage_scene`, which keeps the prompt; for a load, fetch or quit script, ask the user first.
- A call that opens or waits behind a modal dialog returns `BLOCKED_BY_DIALOG` with its title, text and buttons. The call keeps running inside Max: never repeat it. Other replies warn while any dialog is open.
- Read and answer dialogs with `max_dialogs`. `respond` presses a button by label or index and returns the interrupted call's result. A call whose connection broke comes back `status: "lost"` (outcome unknown): inspect before running it again.
- Only dialogs on Max's main thread can be read or pressed. Another thread's dialog is listed but not read, and `respond` fails with `DIALOG_NOT_ON_MAIN_THREAD`; `BRIDGE_OUTDATED` means the bridge has no dialog monitor. In both cases ask the user to answer it in Max.
- Choose the button yourself only when the user asked you to proceed without confirmation and the task determines the answer. Otherwise show the user the dialog and ask. Always ask before save, Don't Save, overwrite, discard, Fetch, Reset or licensing choices, even when working unattended.
- A MAXScript error box during a call fails that call with `MAX_DIALOG_ERROR`. Callback errors appear just after the triggering call returns; the next reply warns, so read the error and acknowledge it.

## Results and Errors

- Replies are a `ToolEnvelope` dict (`ok`/`result`/`error`/`hint`), not a JSON string. Tool-authored hints win over automatic ones; `hint.suggested_tools` may list alternatives.
- Classify raw structured errors by `error`, `code` or `status=error|failed`, never by `message` alone.
- `status: "partial"` means only part of the request applied. Read `warnings` and the skipped items (`notFound`, `ambiguous`, `ignored`) before continuing.
- `AMBIGUOUS`: several objects share the name. Retry with a candidate's `handle` where the tool takes one; otherwise rename them first (`set_object_property(handle=..., property="name")`). Never guess which object was meant.
- `TRANSFORM_IGNORED`: a constraint, script or expression controller drives that part of the transform. Edit the controller's targets or values, or ask before replacing it; repeating the move changes nothing.
- `USER_BUSY`: Max has an open undo operation and the write was rejected before any change. Continue read-only and retry after it finishes; never bypass it with MAXScript.
- `REQUEST_OUTCOME_UNKNOWN`: the request was sent and its reply was lost, so it may have run. Inspect the scene before running it again; never replay it blindly.
- `status: "running"` with a `job`: a long `render_scene` or `capture_multi_view` outlived its `wait_seconds` and keeps running in Max. Call the same tool with `job=` to wait again; never repeat the original call. Jobs belong to the MCP server process that started them.
- `MAX_BUSY`: Max did not answer within the call's deadline (about 130 s, or a long tool's own timeout). The call keeps running inside Max: never repeat it; `max_dialogs(action='inspect')` reports its result in `blocked_calls` once it finishes.
- `MAX_NOT_RESPONDING`, and a `MAX_BUSY` before the request was sent: Max is hung or busy. Retry only when `retryable` is true; `request_sent: false` means nothing reached Max. Wait up to ~10 min, re-checking with `get_bridge_status`; still blocked after that means a deadlock, and the user must end Max (unsaved work is lost). `get_bridge_status` answers while Max is hung and says what holds its main thread; `capture_hang_diagnostics` shows where it is stuck without contacting Max. A modal dialog is not a hang while Max's main thread keeps pumping: calls return `BLOCKED_BY_DIALOG` and `get_bridge_status` reports `blocked_by_dialog`.

## Native Core Development

Only when changing the bridge's native C++ handlers; ordinary sessions never need it. `reload_native_core` (full and progressive profiles) swaps a rebuilt core into the running Max.

- `reload_native_core(action="status")` tells you whether reload applies: `layout="hot"` reloads. `static`, the shipped layout, cannot; a native change there applies only after the rebuilt plugin is installed and Max restarts.
- After rebuilding the core, call `reload_native_core(action="reload")`, check that `build` changed (a warning flags an unchanged one), then verify the changed behavior through its real tool, not just the status.
- On any error the previous core keeps serving, unless the message says no core is loaded:
  - `CORE_STATIC`: static layout. Do not retry.
  - `CORE_BUSY` (retryable; with `wait_ms` up to 20000 the tool already retried): native calls were still running (including one waiting on a dialog), Max's main thread was busy (a render), or another reload was running. Let long calls finish or answer the dialog with `max_dialogs`, then reload, or raise `wait_ms` (up to 600000).
  - `CORE_LOAD_FAILED`: the DLL is missing, is not a core build, or would not load or initialize. Fix the cause and rebuild; never retry unchanged.
  - `CORE_VERSION_MISMATCH`: the core does not match the running shell's interface, SDK, compiler or build configuration. Rebuild Release from the same tree; a change to the shell itself cannot be reloaded.
  - A lost reload response: never resend it; compare `build`, `reloads` and `last_error` from status.
- A reload stops `watch_scene` and drops its events; start it again if needed. `query_scene(action="delta")` positions, dialogs, the agent viewport, armed render jobs and undo history survive. Calls that arrive mid-swap get a retryable `CORE_BUSY`.
- A reload cannot apply shell changes: the named pipe, main-thread executor, dialog monitoring (Win32 and Qt), scene journal, agent viewport, render job notifications, undo records, the core loader, and the shell/core interface. Those need the rebuilt plugin installed and a Max restart.
- Python tool changes need a fresh MCP server process; a reload does not apply them.

## execute_maxscript

- Use it only when no dedicated tool exists: unsupported controller operations, render or environment settings, one-off scripted operations.
- `code` is JSON-unescaped once before MAXScript parses it. A `BAD_PARAM` parse error without a line number almost always means escaping corruption, not bad logic:
  - Use forward slashes in path literals (`"C:/assets/tex/"`) or derive paths at runtime; `\"` and `\U` break literals.
  - Do not put `\n` or `\t` inside string literals.
  - Keep the script on one line, with statements separated by `;`.
  - Debug by shrinking to a known-good core (`try (...) catch (getCurrentException() as string)`) and adding pieces back.
- Quit Max only when the user asks: `try (quitMax #noPrompt quiet:true) catch ()` discards unsaved changes and drops the connection. `MAXSCRIPT_INTERRUPTED` means a script was stopped (quitMax, exit, an Esc abort), not that its syntax is wrong.

## MAXScript Pitfalls

- Always pass `#noPrompt` to `importFile`, e.g. `importFile @"C:/assets/model.fbx" #noPrompt using:FBXIMP`; configure importer options first.
- No parentheses with keyword arguments: `Box width:10`, not `Box() width:10`. Box `width` is X, `length` is Y, `height` is Z.
- Wrap risky code: `try (...) catch (ex) (ex)`.
- `Noise` is a texture map; `Noisemodifier` is the modifier.
- `getDir #temp` is Max's temp folder, not the OS temp folder.
- Convert .NET strings to MAXScript strings before using string methods.
- `snapshotAsMesh` returns a world-space TriMesh: read vertices in `coordsys world` without reapplying `node.objectTransform`, then delete it. Base-cage poly vertices still need the object transform.
- Normalize controller and wire display tokens: `[#Z Position]` becomes `[#z_position]`.
- `getClassInstances Material` is invalid because Material is not a MAXClass; use `sceneMaterials`.
- `getHandleByAnim` returns values like `12345P`; quote them when building JSON.
- Open Unwrap UVW with `$Box001.modifiers[#Unwrap_UVW].edit()`, not only the `OpenUnwrapUI` macro.
- TCP fallback is opt-in. If viewport interaction stutters while it runs, stop it and use the native bridge.

## MAXScript Reference (bundled)

Read the relevant file before writing unfamiliar MAXScript:

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
