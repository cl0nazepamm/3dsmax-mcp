---
name: 3dsmax-mcp
description: Tool choices, workflows, and MAXScript pitfalls for controlling 3ds Max via MCP.
---

# 3ds Max MCP — Agent Guide

Each tool's manual (`mx.help`) documents its parameters, modes and limits, and its errors say what to
do next. This guide holds only what no single tool says: rules of conduct, cross-tool workflows and
MAXScript pitfalls.

## Calling Tools

- A tool this guide names is a Python function: call it inside a `run_python` script as `mx.tools.<name>(**arguments)`, unless your tool list shows it as a tool of its own.

## Principles

- Match the user's request. Do not run setup, discovery or scene analysis by habit, and do not call `get_bridge_status` or `get_session_context` as a preamble.
- Prefer a dedicated tool over raw MAXScript whenever one clearly matches.
- Do not render or start an interactive preview (`render_setup(render=...)`, `agent_viewport(action="render")`) unless the user asks. Viewport capture is fine when visual proof helps.
- Verify meaningful edits with `query_scene(action="delta")`, re-inspection or a capture.
- Keep mutating calls sequential. Concurrent writes interleave undo transactions: a crash, then persistent corruption such as phantom successes and wrong handles.
- Reset the scene with `manage_scene`, which keeps the save prompt. Before a script that loads, fetches or quits, ask the user.
- Node handles are stable only within the loaded scene. Cross-check cached handles with `name` or `path`, and refresh after a reset or load.
- A guarded edit takes its token (`expected_mesh`, `expected_view`, `expected_controller`, ...) from a fresh inspection. A refused token means inspect again, never bypass the guard.

## Workflows

### Inspection and parameters
- Run `analyze_node_orientation` before rig, vehicle or camera transforms.
- Arnold materials such as `ai_standard_surface` may be missing from class discovery; use `inspect_plugin_class` or `introspect_osl`.
- Old-style and scripted plugins (Lathe, Twist; source `maxscript`) publish no choice names or ranges: pass their integers only when the plugin's documentation pins the meaning. Never infer what an integer means.
- Token-guarded plugin edits (shared or animated resources, concurrent sessions): `inspect_plugin_class` or `inspect_plugin_instance` with `schema_version=2`, then `plugin_patch` with the returned schema and state tokens.

### Modeling
- Polygon edits: `inspect_mesh` or an AGENT VIEWPORT capture → `pick_component` with the capture's `view_token` as `expected_view` → `mesh_edit(expected_mesh=...)` → verify with `inspect_mesh`, `geometry_qa` or a capture.
- Construction: `create_mesh` for computed vertex/face arrays; `loft_mesh` for parameterized section lofts; `curve_model`, `inspect_curve` and `edit_curve` for curve-driven forms (read [curve-construction.md](curve-construction.md) first); `draw_spline` plus Lathe/Extrude/Bevel_Profile/Sweep through `add_modifier`, refined with spline knot edits or `edit_vertices` conform.
- `boolean_operation`: prefer inline `cutters`, and never consume a node other tools still reference by name.
- After placing parts that should sit on or join each other, run `contact_check` and judge reported depth against the design intent.

### Materials
- OSL: `write_osl_shader`, then `introspect_osl` before wiring. The shader name must match `shader_name`; OSLMap lowercases parameter names.
- `material_class` is the material's own class name: `PhysicalMaterial`, not `Physical` (the camera).

### Lighting and rendering
- Read the `notes` in `mx.render_setup()` once per renderer before lighting: they hold what differs (which units exist, how the sun is placed, what a kind cannot do).
- An over- or underexposed frame is fixed on the camera or the lights, then rendered again in the same call.

### Viewport
- Open `agent_viewport(action="open")` before inspection captures, so they never move the user's view. Release and reopen it after a scene load.

### Animation
- Pass `list_wireable_params` paths, including their `[#Parameters]` levels, to `wire_params` unchanged.
- `keyframe_tracks`: parent `numKeys` is often 0 because keys live on Bezier Float sub-controllers. `bake` and `resample` skip list, constraint, expression, script and motion-capture controllers. Use `loop`, or `match` with `order=hierarchy`, for parented rigs. `tracks` takes exact tokens only: `all`, `position`/`pos`, `rotation`/`rot`, `scale`/`scl`, `transform`/`tm`.

### Plugins
- tyFlow graph work: read [tyflow-graphs.md](tyflow-graphs.md) completely before acting.
- RailClone: [railclone.md](railclone.md) is its MAXScript reference (properties, exported parameters, base and Segment arrays, methods).
- Data Channel and Max Creation Graph: read [procedural-graphs.md](procedural-graphs.md) completely before acting.
- Forest Pack: after `scatter_forest_pack`, hide the source meshes.
- Plugin surfaces: `discover_plugin_surface`, `get_plugin_manifest`, and `resource://3dsmax-mcp/plugins/{name}/manifest|guide|recipes|gotchas`.

## Results and Errors

- `AMBIGUOUS`: several objects share the name. Retry with a candidate's `handle`, or rename them first; never guess.
- `TRANSFORM_IGNORED`: a controller drives that part of the transform. Edit the controller, or ask before replacing it; repeating the move changes nothing.
- `USER_BUSY`: Max has an open undo operation and nothing changed. Retry after it finishes; never bypass it with MAXScript.
- `status: "partial"`: only part of the request applied. Read `warnings` and the skipped items (`notFound`, `ambiguous`, `ignored`) before continuing.

## MAXScript Pitfalls

- Before writing unfamiliar MAXScript, read the matching bundled reference: `maxscript-<topic>.md` for `core-syntax`, `common-patterns`, `3dsmax-objects`, `mesh-poly-ops`, `materials-textures`, `animation-controllers`, `rendering-cameras`, `splines-shapes`, `scripted-plugins` or `ui-rollouts`.
- Inside a `run_python` script use `gc light:true`, never a full `gc()`: a full collection discards the script's undo records.
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
