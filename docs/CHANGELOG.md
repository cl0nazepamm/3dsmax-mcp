# Changelog

All notable changes to this project are documented here.

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
