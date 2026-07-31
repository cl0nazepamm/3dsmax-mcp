# Changelog

All notable changes to this project are documented here.

## [1.7.0] — 2026-07-31

### Changed

- `boolean_operation action=apply`: inline `cutters` — scratch primitives defined in-call ({name, shape: box|cylinder|sphere, size, pos (bbox center), rot, operation?}), created, named, and consumed atomically; failed appends are deleted on the spot, so no scene litter on any path. `repeat` {count, axis, spacing} arrays every cutter along an axis (`vent_1..N` naming) for vents, ribs, and window grids. `operands` is now optional when `cutters` is given.
- New `DictValue` coerced type (stringified JSON objects absorbed, same spirit as the list coercions).

### Removed

- Builder mode (`builder_session`, `builder_gate`, builder.md) and the unexposed advanced-vision overlay (Python module + native handler) are out of the tree. Field runs showed builder output quality tracks the driver model too steeply to ship; retrieve from git history if revisited. Native rebuild drops the dead `native:advancedvision` handler — no callers remain.

Modeling release: booleans, splines, vertex fitting — and builder mode taught to use them. Python-side only; no native redeploy needed.

### Added

- `boolean_operation`: Boolean modifier (BooleanMod) workflows — apply union/subtract/intersect/merge/attach/insert/split operands (imprint/cookie options, mesh/OpenVDB method, live references), list/retune/rename/disable operands, remove or extract them. Non-live operands are consumed and keep their node names in the operand list. Extract handles the Modify-panel context requirement internally.
- `draw_spline`: spline authoring from world-space points (corner/smooth/bezier knots, closed loops, multi-spline holes via add_spline), knot readback with length-uniform samples, knot edits (bezier handles dragged along by default), insert/delete knots, renderable thickness.
- `edit_vertices`: Editable_Poly vertex reads (index/bbox/radius filters), moves with soft (1-d/r)² falloff, explicit sets, and conform — pull verts onto a spline (axis-masked, e.g. fit the xz silhouette while preserving width) or ray-project onto geometry. World-space; edits the poly base beneath live modifiers.

### Changed (builder mode)

- Form pass gains a deterministic **shaping gate**: geometry components whose base object is still a raw primitive (no shaping modifiers, no Boolean operands) hard-fail until booleaned, spline-built, poly-edited, or deformed — or declared `primitive: true` in the spec. Detection keys off the base-object class (any modifier flips the world-state class to Editable_mesh) and ignores non-silhouette modifiers (UVW map, Smooth, ...). Metrics report the route each component cleared it (`shaped`: base | boolean | modifier | declared-primitive).
- Boolean operand names count as detail anchors; new detail `via` kinds `boolean` and `spline`.
- Extruded/lathed splines (mesh output) satisfy geometry coverage; bare profile splines fail with a hint; mesh-producing shapes are now held to the unspecced-geometry gate at finish.
- Gate metrics include per-component `center_off_root`.
- builder.md: shaping-toolkit section (boolean cuts, spline profiles, vertex conform); the form rubric now rejects components that still read as their source primitive.

## [1.5.1] — 2026-07-26

Builder-mode hardening from the first external field run (M4 carbine): the pipeline completed but the vision layer self-absolved and two cameras were left at scene root.

### Added

- Session-litter gate: `start` records scene-root nodes in the ledger; any node appearing at scene root afterwards warns during the build and hard-fails at finish. Catches abandoned projection cameras and misfired tool calls that the descendants-only census could not see.
- Per-detail review: `check` returns `details_to_review` at the detail pass and `record continue` is refused unless the evidence names every detail id — one grid glance cannot verify sixteen features.
- Hedge-word gate: `record continue` refuses evidence containing self-absolving vocabulary ("stylized", "proxy", "placeholder", "chunky", "acceptable for", "good enough", "for now"); those are `refine-scene` words.
- Optional `budget.min_tris` underbuild floor, checked at finish.

### Changed

- Projection recipe requires the camera parented under the root on `_builder`, plus a silhouette-match acceptance test before the map is wired — an unmatched camera projects garbage.

### Fixed

- `assign_material` access violation (0xC0000005) on a non-material `material_class`. The native handler's class lookup fell back to an unfiltered `FindClassDescByName` across every superclass and blind-cast the result to `Mtl*`; `material_class="Physical"` therefore instantiated the Physical *Camera* and dispatched `SetName`/`SetMtl`/redraw through the wrong vtable. The lookup now stays inside `MATERIAL_CLASS_ID` and raises a structured `BAD_PARAM` with `hint.didYouMean` (`Physical` → `PhysicalMaterial`). Requires a native redeploy; the same unguarded pattern remains in the modifier and object handlers. See `docs/CRASH_LOG.md`.

## [1.5.0] — 2026-07-26

Builder mode: spec-gated staged asset construction from a reference image.

### Added

- `builder_session` (start/spec/status/abandon): sculpt spec + pass state as an AppData ledger on a root assembly node; deterministic validation rejects shallow specs before any geometry (component/detail floors scale with declared complexity).
- `builder_gate` (check/record): one-census hard gates — coverage, sorted-dims proportion, ratios, symmetry/mirror/ground/touch relations, degenerate scale, material assignment + declared params, detail anchors, tri budget, layer hygiene. Multi-view capture fires only after gates pass; `record continue` re-checks and refuses while violations remain; 3 failed checks per pass escalate to request-input.
- Skill reference `builder.md`: pass rubrics, spec contract, detail-anchor naming, camera-map projection recipe for patterned surfaces.

Agentic tyFlow graph authoring with a shadow wiring ledger.

### Added

- `main_thread`: main/UI-thread hygiene. Lists redraw-views callbacks, live .NET timers held in globals, general-callback count, and playback state — `callbacks.show()` misses redraw callbacks and timers. `action=native_threads` attributes every process thread to its owning DLL with per-thread CPU sampled over ~500 ms, so a chugging native plugin is visible even though `SetTimer`/`RegisterNotification` have no enumeration API. Kill actions cover redraw callbacks, timers, and callback ids.
- `get_tyflow_graph`: full event/operator/property readback with structural hash, wiring edges, and ledger staleness (fresh/stale/absent). tyFlow's wiring is not readable at any layer (live-probed), so edges made through MCP are recorded in a ledger stored as AppData on the flow node.
- `tyflow_apply_patch`: transactional graph edits (11 operation types) with expected-hash concurrency gate, checkpoint clone, empirical sim verification via particle counts at probe frames, and automatic rollback that restores the ledger.
- Wiring on tyFlow's documented connect API: `connect_tyflow_operator`, `disconnect_tyflow_operator`, `set_tyflow_wiring_ledger` for reconciling foreign flows; `connect_tyflow_events` rewritten off dead property-name guessing.
- Operator manifest: `harvest_tyflow_manifest` probes all 196 known operator types (property names, defaults, availability, executable flags) cached per tyFlow version; `list_tyflow_operators` queries the cache offline.
- `tyflow_event_census`: per-event particle counts via temporary Mapping-channel instrumentation (mappingMode 7, live-verified).
- `capture_tyflow_editor`: tyFlow editor screenshot plus per-event rectangles for visual wiring reconciliation.
- Skill reference `tyflow-graphs.md`: closed-graph reality, ledger semantics, the agentic loop, and probe-gently rules.

### Changed

- Stability hardening against tyFlow Qt deadlocks: additive-only patches roll back via synthesized inverse operations instead of checkpoint-clone churn, and census/checkpoint/patch scripts pump posted messages at every churn point.
- tyFlow name handling is canonical on the underscore form (`Send Out` reads back as `Send_Out`); lookups accept both.
- tyFlow mutation tools (`create_tyflow`, `add_tyflow_event`, `modify_tyflow_operator`, `set_tyflow_shape`, `add_tyflow_collision`, `remove_tyflow_element`) now maintain the wiring ledger and structural hash.

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
- **Docs** — user-facing README + [Advanced configuration](docs/ADVANCED.md).

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
