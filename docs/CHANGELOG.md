# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [1.7.8] — 2026-11-07

- Added `tyflow_script` for tyFlow Script operators (C#). tyFlow compiles a Script operator once and ignores later code changes, and reports neither compile errors nor runtime exceptions, so this tool gives the full loop: `api` returns the installed tyFlow's script API; `check` compiles the code against that API with line and column diagnostics and enforces the required callbacks; `set` refuses code with errors and replaces an already compiled operator in place (name, position, settings and wiring kept) so the new code compiles; `run` resets and simulates, then reports whether tyFlow compiled the operator, its `Print()` output and any exceptions the callbacks threw. `set` needs safe mode off and `allow_executable=true`. `tyflow_apply_patch` refuses script changes through `set_properties`, which tyFlow ignored on compiled operators.
- `tyflow_script` refuses a `%` in `Print`/`PrintAll` text (`TYFLOW002`): tyFlow formats it printf-style and 3ds Max crashes.
- tyFlow 2.101 adds operators from MAXScript only to a flow whose editor is open, so `create_tyflow`, `tyflow_apply_patch` and `tyflow_script` failed with "tyFlow has no operator type". Adding an operator now opens the flow's editor when needed and closes it again only if it was closed; `tyflow_event_census` and the manifest probe do the same, and a harvest in which tyFlow added no operator is not cached.
- `tyflow_script` works on 3ds Max 2023-2025, which ship neither Max's C# compile helper nor Roslyn: the checker comes prebuilt with its own Roslyn (`helpers/tyflow_checker/lib`) and reads tyFlow's in-memory API assembly the .NET Framework way. 3ds Max 2026 and later still compile it with the Roslyn that ships with Max.
- `get_tyflow_graph` reads the wiring and each operator's type from the flow itself (`wiring_source: "scene"`), so wires drawn in the tyFlow editor and renamed operators are reported as they are; the MCP ledger remains only for older bridges. `graph_hash` covers the wiring but not event positions, which tyFlow moves by itself, so patches are no longer refused for a change nobody made.
- tyFlow tools reach every operator: Shape, Birth Burst, Actor and Spline Paths can now be renamed, enabled and removed. An operator of the wrong family for its event (particle, terrain, VDB, Inferno) is refused instead of reported as added, and the manifest records each type's family.
- `cosmos_search(image_path=...)` finds Cosmos assets that resemble a local image, best match first, through Chaos's Cosmos MCP endpoint (Cosmos Browser 2026.11.04 or later); `sizes=true` adds each model's bounding box in centimeters and scene units. Without the endpoint, image search reports `COSMOS_MCP_UNAVAILABLE` and the rest of the search is unaffected.
- `cosmos_search(thumbnails=true)` returns one labeled contact sheet of the page's previews instead of full-size images. Adds Pillow to the dependencies.
- Cosmos tools find the importer when a second Cosmos instance holds the default port, and report duplicate importers instead of guessing.
- `cosmos_import` detects the nodes a V-Ray model import created (Chaos importers do not tag them), instead of waiting 30 s and returning `imported_unverified`, and reports plain node handles that other tools accept.
- The call right after a dialog is answered no longer fails with "Named pipe ... not found". The bridge has no pipe instance for a moment while the calls queued behind the dialog finish, so the client retries the connect for up to 0.25 s before it calls the bridge gone.
- The agent skill's tyFlow reference covers VDB events, the Script operator (reading meshes, surfaces through VDB or `tyParticleSkin`), Spline Paths and tySplines, cloth setup, and the settings that crashed or stalled 3ds Max.
- Rebuilt bridges for Max 2023-2027.

## [1.7.7] — 2026-10-31

- `capture_multi_view` captures animation: `frames` (up to 12 frame numbers) or `frame_count` (2 to 12 frames spread over the animation range) give one view over time in a single image. The camera is framed once over everything the subject covers on those frames and held, so motion reads across the tiles; the time slider comes back afterwards.
- `capture_multi_view` takes more views: `three_quarter`, `current` (the view as it is) and `[x, y, z]` directions from the subject toward the eye, framed like the named views. `shading` (`smooth`, `flat`, `wireframe` or `clay`, any letter case) and `edges` apply to that capture only, and the view's own settings come back afterwards. A view name it does not know is an error instead of being skipped.
- `capture_multi_view` lists only each tile's camera in `agent_views` (one entry for a frame strip); the panel's full state comes once, in `restored_agent_viewport`. A 12-frame strip's reply is about 2k characters instead of 10k. A native bridge older than this release refuses the new options instead of returning a different capture.
- Long calls no longer hold up the agent: `render_scene` and `capture_multi_view` wait up to `wait_seconds` (50 at most), then return `{"status": "running", "job": ...}` while 3ds Max keeps working, and `capture_multi_view` adds its progress. Call the same tool with `job=` to wait again; the work is never sent twice. Jobs belong to the MCP server process that started them.
- `agent_viewport(action="open")` works on the first try in a freshly started 3ds Max. Showing a floating viewport for the first time rebuilds it, so open failed with "unavailable or its layout changed" or "has not redrawn" and left the panel half-open; it now opens in smooth shading as intended. Status names every visual style (`realistic`, `smooth`, `facets`, `flat`, `hidden_line`, `wireframe`, `bounding_box`, `clay`, `model_assist`, `stylized`) instead of `other`.
- A missing bridge pipe (`BRIDGE_DOWN`, "Named pipe ... not found") hints at `list_max_instances` and starting 3ds Max instead of a missing scene object.
- `replace_material` and `batch_replace_materials` take `include_sub_materials` (default true; false replaces objects' own materials only) and `source_from`, which limits the source search to one place: `node`, `sub_material`, `material_editor`, `scene_materials` or `material_library`. The source can now also come from the scene materials or the current material library, and the reply reports where it was found (`source_found_in`). A batch entry can carry its own `source_from`, and a native batch reports `total_replaced_slots`. The MAXScript fallback supports both options; a native bridge older than this release ignores them, and the reply then warns that `source_from` or `include_sub_materials=false` may not have applied.
- `replace_material` with `preview=true` lists the target's matches even when the source doesn't exist yet (`source_exists: false`, with a warning). Results explain the argument direction (source = the material to apply): `no_match` and `no_objects` warn that the arguments may be reversed, and `blocked` says they are not. A batch preview without the native bridge marks entries whose real run can differ with `depends_on_entries`.
- Material graphs (`inspect_material_network`, `material_roles`) read Autodesk Bitmap paths from Revit/ATF materials, resolving relative paths through the 3ds Max search paths. A hidden VRayBitmap parameter is no longer reported as a second texture file, which raised a false `FILE_MISSING`. Both need the native bridge from this release.
- `material_roles` reports file-bearing maps whose path can't be read with `FILE_PATH_UNREADABLE` and `complete: false`, and maps with no file assigned with `FILE_NOT_ASSIGNED` (native bridge from this release; an older bridge reports them as `FILE_PATH_UNREADABLE`).
- A busy or hung 3ds Max now gives `MAX_BUSY` or `MAX_NOT_RESPONDING`, with window and CPU evidence, instead of a bare timeout. The pipe lock has a 10 s timeout (`MAX_BUSY`, `request_sent: false`, retryable: nothing was sent). A sent request still ends at the 1.7.6 reply deadline, and Max is checked first: a main thread that stays blocked gives `MAX_NOT_RESPONDING`, and a hung Max then fails fast until it recovers. A Max that is still working gives `MAX_BUSY` (sent, not retryable) as before, and a Max that exits ends the call at once. Only windows on Max's main-window thread count, so a modeless window stuck on another thread does not make Max look hung. `request_sent` and `retryable` tell the two `MAX_BUSY` cases apart. A request whose reply was lost after it was sent gives `REQUEST_OUTCOME_UNKNOWN` instead of `BAD_PARAM`, and a request that may have run is never replayed.
- `get_bridge_status` answers while Max is hung and says what holds the main thread: this server's request, another MCP client's, a modal dialog (`blocked_by_dialog`) or work outside the bridge. It uses the bridge's new `health` command, which a pipe thread answers without touching Max's main thread, and falls back to `ping` with an older bridge.
- Added `capture_hang_diagnostics`: native thread stacks of a hung Max, read from the OS without sending anything to Max, with a summary of what blocks the main thread. Each thread is suspended for a few milliseconds while its stack is read. Captures are saved under `%LOCALAPPDATA%\3dsmax-mcp\diagnostics\`.
- The stdio server exits when its client process exits, and always after its stdin closes, so a crashed client no longer leaves a server connected to Max. `MAXMCP_PARENT_WATCHDOG=0` turns off the client-process check.
- Scripts that mention `resetMaxFile`, `loadMaxFile`, `fetchMaxFile`, `quitMax`, `checkForSave`, `max reset file`, `max file new`, `max file open`, `max fetch` or the Interface's `FileReset`, `FileFetch` or `LoadFromFile` no longer run in quiet mode, so a "save changes?" prompt reaches the agent as `BLOCKED_BY_DIALOG` instead of discarding unsaved work. `execute_maxscript(quiet=True)` on such a script needs `allow_discard=True` too. Replies say when quiet mode was off for this reason (`transport.quiet_override` and a warning). `manage_scene` fetch and reset keep their prompt even if something left Max quiet. `execute_maxscript` sends `quiet=False` for such scripts, so an older bridge shows the prompt too. `execute_maxscript` and `execute_python` refuse such a script with `UNSAVED_CHANGES`, before it runs, while the scene has unsaved changes (`loadMaxFile` never asks to save), unless `allow_discard=True`. A dialog that holds this check gives a retryable `BLOCKED_BY_DIALOG`: the script was not sent. Edits made with undo off, as plain `execute_maxscript` edits are by default, don't mark the scene modified, in Max too. Tool edits (create, transform, material, clone, delete) and `undo on` scripts do.
- `execute_maxscript` no longer reports an interrupted script (one that calls `quitMax` or `exit`, or an Esc abort) as a parse error: it gets `MAXSCRIPT_INTERRUPTED`, with Max's own error text when it gives any. Real syntax errors include the compiler's message.
- Dialog monitor: a hung Max with a dialog on screen gives `MAX_NOT_RESPONDING`, not `BLOCKED_BY_DIALOG`, which now needs Max's main thread to keep pumping. Dialogs on other threads are listed but never read or pressed (`DIALOG_NOT_ON_MAIN_THREAD`), and Qt dialogs are read only while the main thread pumps. The Cosmos browser, the Material Editor and floating viewports are never treated as dialogs. A transacted native call with an acknowledged MAXScript error box is always rolled back, never committed first; a Qt button press reported as failed never clicks afterwards, and one Max had already started reports `DIALOG_OUTCOME_UNKNOWN`; the agent viewport's render cleanup finishes after an error box. `max_dialogs` reports a call whose connection broke as `status: "lost"`, and gives `BRIDGE_OUTDATED` on a bridge without the dialog monitor.
- Every tool except the four routing tools takes an optional `expected_pid`: a call that would reach another 3ds Max fails with `TARGET_MISMATCH` and nothing is sent. The selection belongs to the MCP server process, which several sessions may share; `selection_generation` in every reply's transport changes on each select, release or re-resolve.
- A target the server picked by itself (default or claimed) follows its 3ds Max's restart, and the reply warns: every tool, including `get_bridge_status`, `reload_native_core`, `max_dialogs` and `capture_hang_diagnostics`, moves to the one Max that started after the old one stopped answering. A Max that was already open is never taken over: until the restart is up, calls fail with `TARGET_UNAVAILABLE` (retryable) and nothing is sent. Several new instances give `AMBIGUOUS`, and a claimed instance wins. Explicit and environment targets still fail with `TARGET_UNAVAILABLE` until you select or release.
- Rebuilt bridges for Max 2023-2027.

## [1.7.6] — 2026-10-14

- Faster mesh tools: `inspect_mesh` on a 288k-face mesh takes about 11 ms instead of 4.7 s (72k faces: 7 ms instead of 1 s), whatever the `limit`. `inspect_mesh`, `mesh_edit`, `create_mesh`, `pick_component` and `loft_mesh` get `mesh_token` from the native bridge, which hashes the Editable Poly base cage in the same main-thread step as the tool's script, instead of formatting every vertex, edge and face in MAXScript for a SHA-256. Tokens from earlier versions no longer match; inspect again. `mesh_edit` and `create_mesh` started inside another 3ds Max undo operation fail with `USER_BUSY` (retryable). These tools require the native bridge.
- Faster repeated `mesh_edit`: it updates the Modify panel only where it differs, so repeated edits on one object take a few milliseconds instead of about 400 ms.
- Faster `geometry_qa`: 8k triangles take 32 ms (was 265 ms) and 72k take 0.3 s (was 2.4 s). The native bridge exports the evaluated mesh and the analysis builds issue rows only for reported samples. It also works with safe mode on (the default); its MAXScript export used `createFile`, which safe mode blocks.
- Tools that take object names no longer act on an arbitrary object when several share a name. Single-object tools fail with `AMBIGUOUS` and list the candidates with their handles; batch tools (selection, visibility, layers, groups, selection sets, `set_parent`, `batch_rename_objects`, `set_modifier_property`, keyframes, `replicate_material`, `add_tyflow_collision` colliders, `assign_to` of texture and Shell materials) skip shared names and report them in `ambiguous` with `status: "partial"`. A name with no exact match matches by letter case alone, as MAXScript does.
- tyFlow, MCG, Data Channel and Boolean tools report what applied. Edits, collisions, PhysX settings, patches, census and manifest runs that applied in part return `status: "partial"` with warnings; edits that applied nothing are errors. `add_tyflow_collision` with only unknown names no longer clears the operator's colliders, an interrupted `harvest_tyflow_manifest` no longer replaces the cached manifest, and a failed `tyflow_apply_patch` returns `PATCH_APPLY_FAILED`. `load_dc_preset` rejects unknown presets before touching the stack. These tools keep `BLOCKED_BY_DIALOG`, `MAX_BUSY` and `TARGET_UNAVAILABLE` errors instead of flattening them.
- `transform_object` and `set_object_property(position)` fail with `TRANSFORM_IGNORED` when a constraint, script or expression controller ignores the edit, instead of reporting success for an object that did not move. A `transform_object` call that moved only some parts returns `status: "partial"` with `ignored`. `set_object_property` names `transform_object` for rotation and scale instead of reporting the property missing.
- `scene_patch` warns when no operation changed the scene, and `scene_qa` without the native bridge returns an error. Transforms that a constraint, script or expression controller ignored are reported in `ignored` with the controller (`status: "partial"`); a patch in which nothing took effect fails with `PATCH_APPLY_FAILED` and leaves no undo entry.
- A call that 3ds Max does not answer no longer freezes the MCP server. After its deadline (about 130 s for ordinary calls, the exact timeout for probes, longer timeouts honored) it returns `MAX_BUSY`, not retryable; the call keeps running in Max and `max_dialogs(action="inspect")` reports its result once it finishes.
- Mutating tools report what actually happened. A MAXScript error inside a native edit fails the call and rolls back its undo step instead of returning success. Requests that matched nothing are errors; requests that applied only in part return `status: "partial"` with the skipped names and warnings. This covers objects, selection, visibility, modifiers, layers, groups, selection sets, `set_parent`, `batch_rename_objects`, `manage_scene` save, materials, texture maps, controllers, wires, keyframes, effects and render.
- `replace_material` and `batch_replace_materials` also replace materials inside sub-material slots (Multi/Sub and other material trees), find the source in sub-material slots and the Material Editor, refuse slots that would create a reference loop, and report `replaced_slots`, `skipped` and a status of `replaced`, `no_match`, `no_objects`, `blocked` or `unchanged`. Batch entries apply in order.
- `contact_check` takes `time_budget_s` (default 20 s): it measures pairs with the fewest faces first and returns `complete: false` with what it measured when time runs out. Named group heads, dummies, shapes, lights, empty meshes and meshes over `max_faces` are returned in `skipped` instead of failing the call.
- The agent viewport restores its preview settings (temporary denoiser, render elements, VFB denoiser layer, render view lock) when a V-Ray or Corona preview is stopped from the renderer's own UI.
- MAXScript reached from a read handler that runs off Max's main thread now runs on the main thread; `inspect_properties` on modifiers without parameter blocks (Normal, Surface) no longer evaluated MAXScript off it.
- `set_modifier_property` matches `modifier_class` by the MAXScript class name (`Bend`) as well as the SDK name (`BendMod`).
- `add_tyflow_event` returns the new event instead of a parse error.
- Removed `inspect_modifier_properties`, a compatibility alias. Call `inspect_properties(name, target="modifier", modifier_index=N)`.
- Removed `refresh_plugin_manifest`. `get_plugin_manifest` already rebuilds the manifest from the live runtime on every call; the removed tool only added `"refreshed": true`.
- `create_tyflow` takes `preset` (rain, snow, fountain, burst, debris) with `amount` and `speed`, replacing `create_tyflow_preset`. Presets build the same operators, flow name and event name as before; `event_name` now overrides the preset's event name.
- `connect_tyflow_operator` takes `create_if_missing` to add a missing Send Out, replacing `connect_tyflow_events`. Call `connect_tyflow_operator(name, from_event, from_operator="Send Out", to_event, create_if_missing=true)`; the reply reports `created_send_out`.
- Removed `list_tyflow_operator_types`. It probed a fixed list of 34 operator names; `harvest_tyflow_manifest` probes the documented catalog the same way and caches it per tyFlow version, and `list_tyflow_operators` reports each type's `available` flag. Pass `operator_names` to `harvest_tyflow_manifest` to probe specific types live.
- tyFlow particle reads (`get_tyflow_info`, `get_tyflow_particle_count`, `get_tyflow_particles`) refuse objects that are not tyFlow and report a failed read instead of 0 particles; a frame update or attribute read that failed is a warning.
- A `tyflow_apply_patch` whose applied operations were kept records them in the wiring ledger, and a tyFlow edit whose ledger update failed says so in `warnings`.
- `add_data_channel` checks `order` and `expected_operator_count` before it adds a modifier or operator, so a rejected request leaves the stack unchanged. `list_dc_presets` escapes preset names.
- `mcg_search_operators` warns when it falls back to the offline catalog, and `mcg_create_graph` no longer treats a class-name probe that could not run as proof the name is free.
- `scene_qa` fix runs that had nothing to repair return `status: "noop"` with a warning.
- `create_lights` and `edit_lights` warn when a committed light could not be inspected afterwards.
- 3ds Max now quits while an MCP client is connected. The bridge waited for every connected client to disconnect, so Max kept running in the background after its window closed. Requests still queued when Max exits now fail at once instead of waiting out their timeout.
- After the selected 3ds Max exits or restarts, calls fail with `TARGET_UNAVAILABLE`, not retryable, and point to `list_max_instances`, `select_max_instance` and `release_max_instance`, instead of a retryable bridge error with a scene-search hint.
- `get_state_sets`, `get_camera_sequence`, `get_wired_params` and `introspect_instance` run on Max's main thread. They ran MAXScript on a pipe thread.
- `inspect_curve` and `edit_curve` accept Line objects, and `draw_spline` edits a Line in place instead of converting it, or collapsing its modifiers with `convert=true`.
- `contact_check` no longer misreads a whole-number `tolerance` or `near_gap` as almost zero.
- `clone_objects` refuses a member of a closed group and names the group, instead of cloning the whole group on top of the original. Clone the group, or open it to clone the member alone.
- `replace_material` returns `no_objects` with a warning when no object uses the target material, instead of reporting success.
- Agent viewport V-Ray previews (`vray_ipr`, `vray_vfb`) start on V-Ray GPU; they were refused.
- `execute_maxscript` and the agent skill warn that quiet mode skips the save prompt: `checkForSave()` returns true and `resetMaxFile()` discards unsaved work.
- Added Pulze Scene Manager tools: `pulze_inspect`, `pulze_preflight`, and guarded, undoable `pulze_set` edits with `pulze_restore` from a backup (#26).
- A Boolean forced to the modifier backend on 3ds Max 2023, which has no Boolean modifier, now fails with a script error naming the working backends instead of a bridge error.
- The native bridge is split into a permanent shell and a core. Installed bridges still ship as one plugin; development builds rebuild the core and reload it into a running Max with `reload_native_core`, and `scripts/mcpcall.py` runs a tool from current source without restarting the MCP server.
- Removed the Tool Playground GUI (`Tool Playground.bat`, `scripts/tool_playground.py`) and its generated catalog.
- Rebuilt bridges for Max 2023-2027.

## [1.7.5] — 2026-10-01

- Agents can read and answer any dialog blocking 3ds Max, Win32 or Qt. A call that opens or waits behind a dialog now returns `BLOCKED_BY_DIALOG` with the title, text and buttons instead of hanging; `max_dialogs` presses a chosen button and returns the interrupted call's result. Other tool replies warn while a dialog is open. Agents ask the user before save, overwrite, discard, Fetch, Reset or licensing choices unless told to proceed.
- MAXScript run by MCP calls now uses Max's quiet mode, so prompts take their default answer instead of stalling the call. `execute_maxscript(quiet=False)` shows them to the agent instead. MAXScript errors are returned as text rather than shown in a dialog.
- Recognized MAXScript error boxes during an MCP call are acknowledged and fail that call with `MAX_DIALOG_ERROR` and the error text. Script Controller Exception boxes are closed automatically and recorded.
- Added `script_controller`: inspect, validate and apply script controllers with typed inputs, frame sampling, token-guarded assignment and rollback.
- A queued bridge request that times out before starting no longer runs later.
- The agent skill is shorter and leaves per-tool detail to tool descriptions.
- `inspect_material_network` now reads 6 levels deep by default (up to 16), so typical wrapper chains no longer report `replicateReady: false` from depth alone. Compact output keeps `complete`. Rebuilt bridges for Max 2023-2027.
- `install.py` now updates an existing Claude Code registration instead of skipping it, keeping its environment variables such as `MCP_TOOL_PROFILE`. Registration failures show the agent CLI's error.
- `install.py` no longer replaces Claude Desktop, Cursor or Gemini settings it cannot parse (comments, invalid JSON), which previously deleted the other MCP servers in that file. It reads files saved with a BOM and writes settings atomically.
- `install.py` no longer breaks the plugin package when 3ds Max is running. It used to delete the package, then fail to copy past the loaded bridge, leaving out `mcp_server.ms` (a missing-component warning at Max startup) while reporting success. It now stops with the plugin package untouched until Max is closed, and verifies every installed file.

## [1.7.3] — 2026-09-23

- Added OpenCode client registration with preserved JSONC settings.
- Added `importFile #noPrompt` guidance to prevent import dialogs from blocking MCP calls.
- Fixed UTF-8 paths in material texture remapping.
- Added `material_roles` for texture sources across shared maps, composites and submaterials, with missing-file checks, advisory filename mismatches and paged scene scans.
- Material graph inspection now preserves every connection and reports depth/node/edge truncation.
- Added `contact_check`, a read-only pairwise check for parts that penetrate, cross, touch or float just short of contact, with depth, gap and world-space sample points.

## [1.7.2] — 2026-09-15

- Replaced `get_railclone_style_graph` with complete XML style read/write and generated-output tools for RailClone 7.3.5+.
- Added guarded, undoable RailClone style replacement and output inspection for source segments, transforms, bounds, and tags.
- Added RailClone modeling guidance for single-footprint buildings, clipping, and embedded geometry.
- Added a guarded ProBoolean fallback for Boolean operations in Max 2023, with explicit backend selection.

## [1.7.1] — 2026-09-14

- Added FStorm and FStormPBR material workflows, area lights, solar sun controls and cropped IPR capture.
- Added Windows window capture for agent viewports and FStorm, V-Ray and Corona frame buffers without desktop occlusion.
- Fixed plugin class-name aliases across discovery, lookup and material inspection.
- Fixed Phoenix detection, including false positives from Octane compatibility maps.
- Fixed FStorm palette warnings and stale light guards.
- Fixed spline tangent overshoot with bounded handles and backtracking checks.

## [1.7.0] — 2026-09-13

- Added Corona lights and environment support, plus Corona PBR materials for palette laydown, smart import and texture-folder workflows.
- Added Chaos Cosmos search, download and import tools for models, materials and HDRIs through Corona and V-Ray.
- Added `execute_python` with captured output, JSON results, tracebacks and undo rollback.
- Added Corona VFB interactive previews from the agent viewport and cropped captures across monitors.

## [1.6.8] — 2026-09-11

- Added a Windows installer with Python, dependencies, native bridges and agent skills included.
- Simplified uninstall and migration cleanup while preserving preferences and backups.
- Removed MCP Smoke from the Max UI and renamed the fallback controls to MCP Start (TCP) and MCP Stop (TCP).
- Simplified the installation instructions and added a warning to close AI clients before setup.

## [1.6.7] — 2026-09-07

### Added

- Sticky per-process Max routing with instance selection, release and startup PID
  pinning in every profile. Existing clients stay on their target when another Max
  is started or claimed; unavailable targets never silently fall back (#19).

- Renderer-agnostic lighting discovery, creation, inspection and guarded editing,
  with provider-specific emitters, output units and environment bindings.
- Plugin inspection schema v2 with exact identities, bounded queries, declared
  limits, sourced enums, linked maps and state tokens; atomic typed `plugin_patch`.

### Fixed

- Physical Material colors use the correct SDK parameter type; failed material
  parameters report errors instead of silently keeping defaults.
- Concurrent fallback viewport and identification captures use unique output paths.

- Guarded light edits distinguish actual sharing from script, undo and renderer
  bookkeeping. Corrected photometric initialization/dimensions and Octane shape IDs.
- Agent viewport framing supports light rigs; light vectors use numeric arrays
  and vertical aiming has a stable default orientation.
- Failed object parameters propagate errors instead of leaving default objects.
- Lost native responses are not replayed as new mutations or fallback constructors.
- SDK introspection distinguishes declared defaults/ranges from uninitialized
  metadata, and the new inspector discovers deferred classes through Max's registry.

## [1.6.6] — 2026-09-05

**Astra Special Release** — an agent modeling workspace with independent vision,
precise component editing and persistent curve construction.

### Added

- **AGENT VIEWPORT:** an owned floating viewport for orbiting, framing, projection,
  visual targeting and captures while the user keeps their working view. Its title
  asks users not to close or minimize it while the agent is working.
- `create_mesh`, `inspect_mesh`, `mesh_edit` and `pick_component` for editable quad
  cages, labeled component inspection, image-to-geometry targeting and guarded,
  undoable vertex/edge/face edits that preserve modifiers.
- `curve_model` for named curves, local construction planes, tangent arcs, rounded
  profiles, sweeps and resampled quad lofts. Numeric controls and source recipes
  persist in the `.max` file and support guarded parameter updates.
- `inspect_curve` and `edit_curve` for world-space knots and Bezier handles,
  labeled captures, visual picking and atomic topology edits with stale-token checks.
- `loft_mesh` for parameterized matched-section quad lofts, plus `geometry_qa` for
  evaluated mesh boundaries, winding conflicts, degeneracy and connected components.
- Basic V-Ray preview controls through the agent viewport, with viewport/VFB capture,
  cropped screen capture and a separate render-cancellation channel. (This might change)

### Fixed

- Spline construction and edits dispatch on the scene node in local coordinates
  and refresh through `updateShape`, preserving modifiers and correct world-space
  targeting under rotation, nonuniform scale and object offsets.

### Changed

- Full remains the default MCP profile. Full, core and progressive discovery expose
  the new modeling tools; native diagnostic schemas exclude Python-only orchestration.
- The portable usage skill includes curve recipes and inspect/edit/verify workflows.

### Removed

- `build_floor_plan` tool and its progressive discovery toolset.
- Standalone chat

### Additional Notes

- Keep the agent viewport visible for Nitrous captures as it cannot update when it's minimized. Exact surface-intersection/thickness checks and an assembly-wide parameter graph
  are not yet added. Curve QA uses sampled geometry. ActiveShade/V-Ray IPR is experimental.

## [1.5.5] — 2026-08-31

Progressive tool discovery, atomic scene operations, and expanded native animation tooling.

### Added

- **Progressive MCP profile.** Advertises only `list_toolsets`, `describe_toolset`, and `call_tool`, then loads exact operational schemas on demand. The installer now offers progressive, full, and core profiles and persists the choice in user configuration.
- Native canonical NodeRefs and `scene_patch` for preflighted rename, relative-transform, flag, and parenting operations committed as one undo step with mutation-only stale-scene guards.
- Deterministic, non-mesh `scene_qa` checks and narrowly scoped naming repairs.
- Native keyframe timeline, delete/move/scale, bake/resample, tangent-normalization, hierarchy-aware match, and loop operations.

### Changed

- Scene journaling separates persistent mutation sequence changes from selection activity, so normal viewport interaction does not invalidate guarded scene patches.
- Native mutation dispatch defers nested work arriving during an active main-thread item, and node flag edits now have explicit undo/redo restore records.
- Generated tool registries, smoke cases, documentation, and the bundled agent skill include the new discovery, scene, and animation surfaces.

## [1.5.1] — 2026-08-04

Packaging fixes and Chinese docs.

### Fixed

- `mcp[cli]` now pinned `<2.0.0`. mcp 2.0 removed `mcp.server.fastmcp` (it is `mcp.server.mcpserver` now), so any resolve that skipped `uv.lock` — `pip install`, a fresh `uv pip install` — pulled 2.x and crashed on import.
- The package directory is renamed `src/` → `maxmcp/`, so the wheel no longer installs a top-level package called `src` — that name collides with any other distribution shipping the same layout, and made the import surface hostage to whatever `src/` happened to be on `sys.path`. Imports inside the package are relative; imports elsewhere (`tests/`, `scripts/`) are now `maxmcp.*`. Console script entry point is `maxmcp.server:main`. Distribution name on PyPI is unchanged (`3dsmax-mcp`); `3dsmax_mcp` is not a legal Python identifier, hence `maxmcp` as the import name.
- Tool-profile counts in ADVANCED.md corrected to 151 full / 87 core (were 114/77), and the specialty module list now includes `mcg`, `render_automations`, and the four `tyflow_*` modules.

### Added

- **Installable from PyPI.** The wheel now carries the whole Max-side payload — the five per-year `.gup` binaries, `mcp_server.ms`, the PackageContents template, `mcp_config.ini`, `.env.example` and the agent skill — plus a `3dsmax-mcp-install` console script, so `pip install 3dsmax-mcp` followed by `3dsmax-mcp-install` is a complete install with no checkout. Assets are shipped at the same relative paths the repo uses, so `install.py` runs unmodified either way (`ROOT` is the repo root from a checkout, the package directory from a wheel); only MCP client registration differs, using the console script's absolute path where there is no repo to point `uv run --directory` at. Matters most for users behind slow GitHub access, who can now install via a domestic PyPI mirror.
- `README.zh-CN.md` — Chinese documentation written for the domestic stack: Cline + DeepSeek as the primary client config (Qwen/GLM via OpenAI-compatible endpoints), archviz and MMD/animation walkthroughs, and TUNA/Aliyun pip mirrors for installs behind slow GitHub access.

## [1.5.0] — 2026-08-01

Modeling tools, faster native inspection/material workflows, and a new install format.

### Added

- `boolean_operation`: Boolean modifier (BooleanMod) workflows — apply union/subtract/intersect/merge/attach/insert/split operands (imprint/cookie options, mesh/OpenVDB method, live references), list/retune/rename/disable operands, remove or extract them. Non-live operands are consumed and keep their node names in the operand list. Extract handles the Modify-panel context requirement internally.
- `boolean_operation action=apply`: inline `cutters` — scratch primitives defined in-call ({name, shape: box|cylinder|sphere, size, pos (bbox center), rot, operation?}), created, named, and consumed atomically; failed appends are deleted on the spot, so no scene litter on any path. `repeat` {count, axis, spacing} arrays every cutter along an axis (`vent_1..N` naming) for vents, ribs, and window grids. `operands` is optional when `cutters` is given.
- `draw_spline`: spline authoring from world-space points (corner/smooth/bezier knots, closed loops, multi-spline holes via add_spline), knot readback with length-uniform samples, knot edits (bezier handles dragged along by default), insert/delete knots, renderable thickness.
- `edit_vertices`: Editable_Poly vertex reads (index/bbox/radius filters), moves with soft (1-d/r)² falloff, explicit sets, and conform — pull verts onto a spline (axis-masked, e.g. fit the xz silhouette while preserving width) or ray-project onto geometry. World-space; edits the poly base beneath live modifiers.

### Changed

- Install format: ApplicationPlugins bundle at `%ProgramData%\Autodesk\ApplicationPlugins\3dsmax-mcp\` (PackageContents.xml + per-year GUPs + `mcp_server.ms` as post-start-up script) replaces copying into the Max install directory. `python install.py` migrates automatically — it removes old-format files from every detected Max install before deploying, and elevates when ProgramData or legacy paths need admin rights. One bundle serves Max 2023–2027; the per-version prompt is gone. (PR #17, hardened: uninstall now removes the bundle, elevation fallbacks, staged bundle outputs gitignored.)
- `native/build.bat` finds the SDK via `ADSK_3DSMAX_SDK_<year>` (falls back to the default SDK path) and stages GUPs into `bundle/Contents/bin/`; `deploy` now defers to `install.py`. New `scripts/stage_bundle.py` + `ADSK_APPLICATION_PLUGINS` for testing a staged bundle without installing.
- `get_plugin_capabilities`, `get_material_library`, `backup_material_library`, `isolate_and_capture_selected`, and existing-material `create_shell_material` use native SDK routes when available, with compatibility fallback for older bridges.
- New `DictValue` coerced type (stringified JSON objects absorbed, same spirit as the list coercions).
- `build_skill.py` bundles `tyflow-graphs.md` into the deployed skill and rewrites its AGENTS.md link.

### Fixed

- `assign_material` access violation (0xC0000005) on a non-material `material_class`. The native handler's class lookup fell back to an unfiltered `FindClassDescByName` across every superclass and blind-cast the result to `Mtl*`; `material_class="Physical"` therefore instantiated the Physical *Camera* and dispatched `SetName`/`SetMtl`/redraw through the wrong vtable. The lookup now stays inside `MATERIAL_CLASS_ID` and raises a structured `BAD_PARAM` with `hint.didYouMean` (`Physical` → `PhysicalMaterial`). Requires a native redeploy; the same unguarded pattern remains in the modifier and object handlers.
- Native compatibility fixes cover Max 2023–2027 SDK differences in scene-node filtering, material-library paths, class enumeration, reference cloning, class labels, persistent object ownership, and GDI+ capture lifetime.

### Removed

- `maxscript/startup/mcp_autostart.ms` — the bundle manifest starts `mcp_server.ms` directly.

## [1.3.1] — 2026-07-20

### Fixed

- Tool replies never inline base64: captures always return the saved file path (`return_image` is deprecated and ignored), and the envelope spills any image or oversized binary payload to `%TEMP%\3dsmax-mcp\payloads` as an `image_file`/`bytes_file` reference.

## [1.3.0] — 2026-07-19

Agentic Max Creation Graph authoring, robust Data Channel control, and portable MCG references.

### Added

- End-to-end MCG tools for context discovery, graph/operator search, temporary graph creation, structured patching, compilation, instance inspection, modifier application, testing, checkpoint restore, and workspace cleanup.
- A bounded MCG iteration transaction with expected-hash concurrency checks, safety checkpoints, compile diagnostics, disposable semantic verification, automatic rollback, and compact proof-of-change.
- Native MCG handlers for deterministic Viper validation/compilation, exact generated-class resolution, safe modifier application, typed parameter assignment, instance readback, and UI error text capture.
- A read-only Autodesk 3ds Max 2017 MCG reference corpus containing 43 `.maxtool` and 282 `.maxcompound` XML graphs, pinned to its MIT-licensed upstream commit and shipped without scenes or packages.
- Data Channel operator discovery, preset discovery/loading, modifier inspection, operator editing, and validated stack management.

### Changed

- Data Channel builds now use the live Max operator catalog, explicit Replace mode for the first Input operator, complete reorder validation, and UI error readback instead of version-fragile hardcoded IDs.
- MCG graphs compile and verify only from process-scoped temporary workspaces; installed graphs and bundled samples remain read-only fork sources.
- MCG executable surfaces and impure operators fail closed under safe mode, while graph UUID/version identity, source ports, named destination ports, and generated plug-in classes are preserved or resolved explicitly.
- Procedural graph guidance moved into a dedicated skill reference so the normal 3ds Max MCP prompt remains compact.

### Fixed

- Native bridge error codes survive Python wrapper failures instead of degrading to generic `BAD_PARAM` responses.
- MCG compilation returns actual Viper diagnostics and generated wrapper details instead of relying on undocumented or nonexistent reload-message APIs.
- Data Channel's first operator no longer remains in the invalid unset blend mode that causes `First operator must be an Input Operator and in Replace`.

## [1.2.0] — 2026-07-09

Structured tool envelopes, centralized error hints, atomic undo, and handle addressing.

### Changed

- MCP tools return a structured `ToolEnvelope` object (`ok`/`result`/`error`/`hint`) with an advertised output schema, replacing JSON-string tripbacks.
- Errors carry a closed `code` enum (`NOT_FOUND`, `AMBIGUOUS`, `PLUGIN_MISSING`, `BRIDGE_DOWN`, `RENDER_BUSY`, `SAFE_MODE`, `BAD_PARAM`) plus `retryable`; native structured errors propagate instead of falling back to MAXScript.
- Mutating native handlers run inside a `theHold` transaction: one MCP call = one undo step, and mid-operation failures roll back atomically.
- Mutating tools return compact post-state proof (new transform/bbox, modifier stack order, resolved material class) so agents don't need a verify round trip.
- MAXScript intent-suggestion rules moved to `src/helpers/error_hints.py` and now apply at the envelope layer, so exceptions from `execute_maxscript` get intent hints too.
- Tool docstrings gained "Use when / Not when" guidance to steer agents toward dedicated tools.

### Added

- `undo_last` — reverts the previous MCP-initiated scene change.
- Anim-handle addressing: tripbacks include `handle`, node tools accept name or handle, and ambiguous names return candidate lists (handle + class + layer) in the hint.
- NodeEvent scene journal in the native bridge; `query_scene(action=delta)` reads seq-numbered changes and answers `unchanged_since` cheaply instead of rescanning.
- MCP tool annotations (`readOnlyHint`/`destructiveHint`/`idempotentHint`), `dry_run` on destructive tools, and explicit scene units in `get_session_context`.
- Auto-resolved error hints when the tool authored none: not-found → `query_scene`, safe-mode → don't retry, bridge/pipe failures → `get_bridge_status`. Tool-authored hints always win.
- Hint normalization: string, plural `hints`, and list hints coerce to a canonical `{message, suggested_tools, next}` shape.
- `scripts/benchmark_agent_ergonomics.py` — measures round trips and approximate tokens per canonical task against a live Max.

## [1.1.0] — 2026-07-06

Render automation (done-signal) and material-library tooling.

### Added

- `render_automations` — arms a render done-signal at 3ds Max's `NOTIFY_POST_RENDER` and reports completion (with the real `frames_rendered` count) through an event-driven file watcher (`scripts/render_signal_wait.ps1`); no polling, never blocks the bridge. Includes `cancel` to abort a render in flight from the pipe thread.
- Native `render_start` / `render_cancel` handlers and an always-on render-completion pinger.
- `get_material_library` — inspects the volatile material scratchpads (`currentMaterialLibrary` and the Compact Material Editor slots) that aren't saved with the scene, and warns when the current library has no backing `.mat` file.
- `backup_material_library` — saves those scratchpads to timestamped `.mat` files without touching the scene.

### Changed

- The bridge is a render *listener*, not a trigger: `render_automations(start)` only arms the done-signal, and the render is fired externally (Render button, or `max quick render` via `execute_maxscript`). Launching the render from inside the bridge caused 3ds Max to auto-start a second render on completion and loop; keeping the trigger outside the bridge avoids it.
- `execute_maxscript` suggests the material-library tools when raw MAXScript touches the material library.
- `SKILL.md` trimmed to Max-usage gotchas only.

## [1.0.6] — 2026-06-24

Keyframing and installer release draft with the package version bumped to `1.0.6`.

### Added

- `keyframe_tracks` for native key inspection, setting, endpoint matching, loop closure, tangent styling, and out-of-range behavior edits.
- Compact, budgeted keyframe summaries for baked animation and mocap-heavy controllers.
- Keyed `value` and `move` writes so animation edits can avoid `transform_object` offset side effects.

### Changed

- Keyframe result counters distinguish logical track edits from raw sub-controller edits.
- Installer discovery covers classic and Microsoft Store Claude Desktop config paths.

### Fixed

- Composite Position/Euler/Scale controllers now create and style explicit-frame keys through their child tracks.
- Keyframe styling reports stable candidate counts on first set-and-style calls.
- Track-path matching is exact so narrow keyframe edits do not leak into similarly named tracks.

## [1.0.5] — 2026-06-01

Material-network release draft with the package version bumped to `1.0.5`.

### Added

- `inspect_material_network` for native semantic material graph reads: wired slots, nested maps, file manifests, health issues, and compact mode.
- `replicate_material` for preview-first graph cloning, texture remapping, verification, and explicit apply.
- Material-network tool catalog, smoke input coverage, in-Max chat registry entries, and user-facing docs/spec.

### Changed

- Viewport capture tools now return saved-file metadata by default, with `return_image=true` preserving inline image behavior.
- Native viewport captures accept max dimensions and report source/final image sizes.
- Tool surface is streamlined around `inspect_properties(target="modifier")`; `inspect_modifier_properties` remains as a compatibility alias but is hidden from the playground and default smoke pass.
- `get_object_properties` and `inspect_object` descriptions now distinguish compact readback from deep exploratory inspection.

### Fixed

- Material replication now blocks missing source textures and non-texture graph dependencies unless explicitly allowed.
- OSL shader source is no longer misclassified as a remappable file path; OSL file paths remain visible in inspection output.
- Wired material slots are deduplicated in graph inspection output.

## [1.0.0] — 2026-05-28

First stable release. Production-ready MCP bridge for 3ds Max 2023–2027 with prebuilt native plugins shipped in the repo.

> **Patched in place (2026-05-28)** — binaries rebuilt, no version bump: fixed a native main-thread deadlock in the **MCP Smoke** macro, a `palette_laydown` crash on flat texture folders, invalid spatial JSON from `create_object` on TCP transport, Forest Pack dropping zero-footprint items, unescaped names in `query_scene` MAXScript fallbacks, and `query_scene(delta)` mis-tracking duplicate-named nodes.

### Highlights

- **114 MCP tools** (77 in `core` profile) — scene reads, objects, modifiers, materials, controllers, viewport capture, plugin introspection, and specialty modules (tyFlow, Forest Pack, RailClone, Data Channel, and more).
- **Native bridge** — named-pipe transport by default; prebuilt `mcp_bridge_20XX.gup` for Max 2023–2027 in `native/bin/`.
- **`query_scene`** — unified scene reads (`overview`, `filter`, `class`, `property`, `selection`, `delta`) replacing scattered snapshot tools.
- **`smart_import`** — folder-aware mesh + PBR import (per-subfolder asset packs, shared atlases, multi-variant bundles).
- **`create_shell_material`** — dual render/export pipeline wrapper for any two materials or texture-built PBR pair (OpenPBR, Physical, Arnold, Octane, etc.).
- **`scatter_forest_pack`** — per-geometry footprint sizing for multi-variant Forest Pack scatters.
- **Spatial placement** — `create_object` / `clone_objects` ground-contact placement with rich tripback (`bbox`, `placement`, `groundContact`).
- **Tool Inspector** — `Tool Playground.bat` GUI for manual one-tool-at-a-time testing with full tripback.
- **Live smoke harness** — `run_tool_smoke`, `run_live_tool_smoke.py`, in-Max **MCP Smoke** macro.
- **Multi-Max** — **MCP Claim This Max** routes clients to the correct instance.
- **Agent skill** — bundled Max usage guide + MAXScript reference files; installer deploys automatically.
- **Docs** — user-facing README + [Advanced configuration](ADVANCED.md).

### Breaking changes (from 0.8.x)

- **`create_shell_material`** — new API: wrap existing materials by name or build from `texture_folder` + `render_material_class` / `export_material_class`. Old UberBitmap-only parameters removed.
- **Scene reads** — prefer `query_scene(action=…)` over removed `get_scene_snapshot` / legacy snapshot modules.
- **Standalone in-Max chat** — still experimental (WIP); external MCP recommended for production.

### Install / upgrade

```powershell
git pull
uv sync
uv run python install.py
```

Restart 3ds Max after install.

---

## [0.8.5] and earlier

Pre-1.0 development releases. See git history for details.
