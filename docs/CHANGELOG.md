# Changelog

All notable changes to this project are documented here.

## [Unreleased]

- **In-Max scripts and `sdk` on 3ds Max 2023 to 2026.** `run_python` ran inside 3ds Max only where Max's Python was the server's (2027, Python 3.13); on 2023 to 2026 it fell back to the server process, without `sdk`, `rt` or the single undo step. The tool layer now runs on Python 3.9 to 3.13, a release carries pydantic and Pillow for each of those Pythons (`maxmcp/inmax/site`, installed by `scripts/release/vendor_inmax.py` from hash-pinned lists), and every bridge ships its own `_mx` (`mx_2023` to `mx_2027`). 3ds Max 2023 embeds Python 3.9, which nanobind 3 does not support, so its `_mx` builds against nanobind 2.15 (`native/third_party/nanobind2`). Two differences remain by SDK: before 2027 a parameter name resolves through the parameter block's alias table instead of MAXScript's resolver, and 3ds Max 2023 has no transform list controller.
- The repository no longer carries built bridges. A release (the Windows setup, the PyPI package) ships them with their `_mx` as before; a checkout builds its own with `native\build.bat <year>` before `install.py`, which now says so when none is built.
- **`render_setup`: one command for lights, sun, environment, camera, render settings and renders, the same on every renderer.** A script names what it wants in neutral terms (`{"kind": "sphere", "radius": 4, "intensity": 800, "unit": "lm", "kelvin": 2700}`, `sun={"elevation": 40, "azimuth": 220}`, `camera={...}`, `render={"camera": "Cam"}`) and the active renderer's preset turns that into its classes and parameters. A name that exists is updated with only the fields given; everything a call changes is one undo step verified by readback. It replaces `lighting_capabilities`, `create_lights`, `inspect_lights`, `edit_lights`, `render_scene` and the `mx.area_light` / `mx.point_light` / `mx.sun` / `mx.lights` helpers.
- **Renderer presets are data.** Each renderer is a JSON file in `maxmcp/assets/renderers` (Octane, FStorm, V-Ray, Corona, Scanline, ART) that maps the neutral vocabulary onto its classes, parameters, units and enum values, each with the evidence it came from. One engine interprets them; there is no per-renderer code left in Python. A file in `%LOCALAPPDATA%\3dsmax-mcp\renderers` adds or replaces a renderer.
- `sdk` loses its own light layer (`sdk.light`, `sdk.environment_light`, `sdk.lights`, `sdk.light_capabilities`, `node.light`, the `Light` class): it was a second copy of the per-renderer tables, and `render_setup` is the one place for lights now. `sdk.Node` gains `.transform` (the world matrix, settable) and `.look_at(point)`; both raise when a controller keeps part of the transform. `mx.help("sdk")` is a one-line-per-name index, 42 % shorter; `mx.help("sdk.*")` lists every signature.
- New in the presets: a sun for Octane (DayLight), FStorm and ART (Sun Positioner with its Physical Sun & Sky) that takes `elevation` and `azimuth`, FStorm and ART render settings, Octane samples and denoising, quality levels (`draft`, `preview`, `final`), a camera per renderer, `visible` and `double_sided` on lights, and `intensity={"lm": 800, "fstorm": 30}` for a script meant for several renderers. Photometric units convert into each other through the emitter's shape, and the reply says when a value was converted or approximated.
- Octane's HDRI environment rendered black: its Texture environment was created with `texture_input_type` 3; a test render per value shows 2 is the one that uses the image.
- A render takes the camera to render from (`render={"camera": "Cam"}`); `render_scene` rendered whichever viewport was active. The image comes back as MCP image content with the `run_python` reply, downsized, so it needs no second call to read the file; `mx.show(path)` returns any other image the same way.
- With several 3ds Max instances the default is the one the user can see (a shown window before a minimized one, then the one nearer the front) instead of the one that registered last, and `list_max_instances` reports each instance's window title, `minimized` and `foreground`.
- `plugin_patch` plans can create cameras, move existing nodes (`transforms`, by matrix or by position alone), place a created node after its parameters are written, and hand a created object's scene node to a node parameter (`{"created": "<id>:node"}`), whose class restriction is checked against the node's object. Inspection and edits reach plugins that keep their parameter blocks as references without reporting them (Octane's renderer). A new native command assigns the production renderer by class ID. A failed plan that leaves a plugin-made helper node behind reports `ROLLBACK_FAILED` with the original cause.
- **Scripts are the default way to work** ([#38](https://github.com/cl0nazepamm/3dsmax-mcp/issues/38)). By default the server advertises five tools: `run_python`, and the four that choose which 3ds Max it talks to. Their definitions take about 2k Claude tokens of every request, where the full profile of 1.7.8 took about 103k. The agent writes Python against `mx`, and every other tool is a function called from a script as `mx.tools.<name>(**arguments)`. `mx.help()` lists the tools by group, `mx.help("<group>")` lists one group with signatures, `mx.find_tools("boolean subtract")` returns the manuals of the best matches, and `mx.help("<name>")` prints one tool's signature and manual. A call checks its arguments against that signature before anything is sent to 3ds Max: a wrong argument name or type raises `mx.MaxToolError` (`BAD_PARAM`) that names the argument and repeats the signature. This is the `code` tool profile, and what a fresh install gets. A client that called tools such as `query_scene` directly calls them from a script, or keeps listing them with one of the profiles below.
- **The `full`, `core` and `progressive` profiles stay, as a choice.** `MCP_TOOL_PROFILE`, `[mcp] tool_profile` in `mcp_config.ini`, `install.py --tool-profile` and the Windows setup's profile page (`/TOOLPROFILE=` for unattended installs) select `code`, `full`, `progressive` or `core`. An update keeps a profile that is already installed: the setup shows it selected, and changes only `tool_profile` in an existing `mcp_config.ini`, only when another one is picked. Scripts reach every tool in every profile.
- **Listed tools take 63% less context** ([#38](https://github.com/cl0nazepamm/3dsmax-mcp/issues/38)). The full profile's tool list went from about 103k to 28k Claude tokens per request, core to 19k. The 14 most used tools keep their full description and JSON Schema; every other tool is advertised as its docstring's first line plus a compact Python-style signature, and the new `tool_help(name)` returns any tool's full manual and schema. Signature tools validate arguments strictly: an unknown argument name is refused instead of silently dropped, and the error repeats the signature. `expected_pid` is still accepted by every tool but explained once in the server instructions instead of in every schema. Set `[mcp] tool_docs = complete` (or `MCP_TOOL_DOCS=complete`) for the previous fully documented listing, or `signature` for the smallest one.
- **Progressive lists the common tools.** It advertises the 14 most common tools directly (scene query, object create/edit/delete/clone, modifiers, materials, MAXScript, viewport capture, undo) and `run_python`, plus the routing and discovery tools, about 7k tokens. `find_tools(query)` searches the tools that are not listed and returns the best matches with their exact input schemas, so reaching a rarely used tool takes one discovery call. `list_toolsets` names each group's tools, and `describe_toolset(toolset, tools=[...])` describes only the named tools; the toolsets are the groups `mx.help()` prints. `call_tool` refuses argument names the tool does not take (they were dropped silently before, running the tool on defaults) and answers with the tool's signature, and it suggests close names for an unknown tool.
- Advertised input schemas drop Pydantic titles, spell optional `X | None = None` parameters as plain `X`, and in `complete` mode the `expected_pid` parameter every tool carries has a one-line description. Arguments are still validated against the Python signatures, so an explicit `null` is accepted.
- **The source is reorganized; no tool changes.** `maxmcp/tools` has one folder per subject (`maxmcp.tools.objects` is now `maxmcp.tools.objects.objects`), what every tool shares is in `maxmcp/core` (`maxmcp.max_client` is now `maxmcp.core.max_client`), and files that are not Python are in `maxmcp/assets`. `scripts/` is sorted into `dev`, `codegen`, `release` and `live`, and `native/src` into `shell`, `core` and `shared`. Code that imports `maxmcp` modules directly needs the new paths; `maxmcp.mx` and every tool name are unchanged.
- **`sdk` in `run_python`.** Scripts that run inside 3ds Max get `sdk` (also `mx.sdk`): `_mx`, the scene object model bound straight from the 3ds Max SDK with nanobind, with calls in microseconds (`sdk.node("Ball").pos`, `node.params.radius = 12`, `node.add_modifier("Bend", angle=30)`, `with sdk.at(10):`). `mx.help("sdk")`, `mx.help("sdk.Node")` and `mx.help("sdk.Node.pos")` read its own docstrings, and `scripts/codegen/gen_mx_reference.py` writes the same text to `docs/mx-sdk-reference.md`. `native/build.bat` builds `_mx` for 3ds Max 2027 and ships it beside the bridge (`Contents/bin/mx_2027/`); a development bridge loads the newest build from its build tree. sdk nodes and `mx.Node` accept each other.
- **`sdk` has collections, shaped like `bpy.data`.** What the scene or 3ds Max has many of is one object, and every one answers the same way: `sdk.nodes["Ball"]`, `sdk.nodes.get("Ball")`, `"Ball" in sdk.nodes`, `len(sdk.nodes)`, iteration, `sdk.nodes("Wall*", cls="Box")` to list by wildcard and filters, `sdk.nodes.new("Sphere", "Ball", radius=10)` to create and `sdk.nodes.remove(node)` to delete. The collections are `nodes`, `selection` (`.set`, `.add`, `.remove`, `.clear`), `materials`, `texmaps` (new: every map the scene uses), `layers` (`.current`), `groups`, `selection_sets`, `state_sets`, `effects`, `render_jobs`, `classes`, `plugins`, `macros`, `interfaces` and `action_tables`. They replace the functions that did the same work under mixed verbs: `node()`, `create()`, `delete()`, `find()`, `select()`, `material()`, `texmap()`, `layer()`, `group()`, `selection_set()`, `camera_sequence()`, `class_info()`, `macro()` and `interface()` are gone, and `Group(node)` is `groups[node]`. A plural called with a pattern still lists, so `sdk.nodes("Wall*")` and `sdk.layers()` read as before. `classes["Bend", "modifier"]` and `macros[name, category]` name the kind or category where a name is shared. `layers.new()` and `selection_sets.new()` refuse a name that exists instead of returning or replacing it.
- **One node type.** Inside 3ds Max the `mx` helpers (`mx.box`, `mx.get`, `mx.find`, `mx.clone`, ...) return sdk nodes. Outside it they return `mx.Node`, which now offers the same core under the same names and meanings: `handle`, `name` (settable), `exists`, `class_name`, `pos` (world, settable), `bbox` (`.min`, `.max`, `.center`, `.size`), `hidden` (settable) and `delete()`. `mx.Point3` and `mx.Box` mirror the sdk value types. `Node.info()`, `.move()`, `.rotate()`, `.scale()`, `.set()`, `.center`, `.size` and `.data` are gone: outside Max, rotation and scale are `mx.tools.transform_object`. The lint checks that sdk.Node has every member `mx.Node` has and that `mx.Node` has no other.
- **Every `sdk` signature says what it takes and gives.** No annotation is a bare `object`, `dict`, `list` or `tuple` any more (133 were when the count started); three values that pass through whatever something else returns are named as dynamic (`native()`, `Interface.call()`, `Macro.run()`). A report dictionary names its keys in its description (`Keys: {...}`), and the live sweep compares 21 of them with what the call returns. Animation values are typed: a rotation value is a `Point3` of Euler degrees where it was a plain tuple, and so is the track value of a colour parameter that is not animated. `track.key(frame)` without a value no longer fails on an integer parameter.
- A parameter of a type neither the tools nor `sdk` can read or set (a bitmap, for one) is marked `read_only` in `parameters` and `params.info()`, and a write to it says so, instead of reading as `None` and failing on write.
- `inspect_object` takes `handle` as well as `name`, so it can read one of several objects that share a name, and its result carries the object's `handle`. Tools that take a handle no longer find a deleted node that is still alive in the undo buffer.
- A script that replaced a keyed controller and then failed could crash 3ds Max while rolling back: `track.assign()`, `add_target()`, `wire()` and `unwire()` ran outside the script's undo step, so 3ds Max freed the old controller while the step's records still pointed at it. Inside a script they are recorded like every other edit, and a rollback restores the controllers, targets and wires too.
- **Everything a script changes is one undo step.** The bridge now opens the script's undo step itself (`native:script_hold`), so `sdk` edits and raw `rt` edits are recorded beside the tool calls, and a script that raises rolls all of them back; before, only tool calls were. A script that changes nothing leaves no undo entry. `mx.tools.undo_last()` and `sdk.undo()` work inside a script: what it changed so far becomes its own step first. Scene reset and fetch inside a script close the step around them. A native core reload is refused while a script runs.
- A failed script says when it could not be rolled back completely. A full MAXScript `gc()` inside a script discards the undo records made since its last tool call, and a `theHold.Suspend()` left without `Resume()` stops recording: both used to leave edits behind while the reply said `rolled_back: true`. The reply now has `rolled_back: false` and a `rollback_error` that names the cause, a script that succeeds gets the same as a warning, and what was recorded is still rolled back. A script is refused with `USER_BUSY` while 3ds Max's undo system is suspended, mid-undo or inside an open super-level, instead of running unrecorded. Objects `sdk` creates and then discards inside a script (a modifier 3ds Max refuses, a mesh with a bad face) are freed through the undo step instead of at once, where the step's records still pointed at them.
- Tools that edit the scene through MAXScript alone (Data Channel stacks, `draw_spline`, `scatter_forest_pack`) run each edit in an undo step, so `undo_last` undoes them and a failed script rolls them back. tyFlow graph tools stay outside undo: with tyFlow 2.101, freeing the undo records of a `create_tyflow` step (a rollback, or a redo flush after undo) crashed 3ds Max at its next save or hold.
- In-Max `run_python` imports `maxmcp` anew when its source changed since the last script, instead of keeping the version 3ds Max first imported.
- `transform_object` and `scene_patch` rotated the wrong way: `rotate=[0, 0, 90]` turned +X to -Y. They now turn like MAXScript's `rotate node (eulerAngles x y z)`, counterclockwise about X, then Y, then Z, and `inspect_object` reads back the angles given.
- `query_scene`/`mx.find` patterns match `*` and `?` anywhere, without regard to case (`VT_*_A`); only leading and trailing `*` worked.
- `create_object` finds deferred-load plug-in classes (RailClone_Pro was "Unknown object class" until something else loaded RailClone) and MAXScript class spellings.
- `contact_check` and `sdk.contacts` report parts that overlap with coplanar faces (two boxes sharing their side planes) as penetrating instead of touching.
- `get_material_slots` counts every parameter and caps only the slots it lists: with the defaults it reported 0 map slots on a Physical Material. `max_slots_reached` says when the cap cut the list.
- `wire_params` refuses an expression that does not evaluate with the driving track's name (it reported "wired" while the driven value stayed 0), accepts bare paths such as `width`, says which track path was not found, and defaults the expression to the driver itself instead of `source_value`, which wired a constant 0.
- `set_railclone_style` works inside a `run_python` script (it refused with `USER_BUSY`).
- `mx.tools.invoke_tool(tool=...)` no longer collides with `mx.call`'s own argument.
- `manage_data_channel_stack(action="reorder")` works; it always failed with "copy wanted 1, got 2".
- `set_tyflow_physx` changes only the settings passed; it reset every other PhysX setting to its default and could misparse a negative gravity.
- `capture_tyflow_editor` captures the tyFlow editor window alone (Windows window capture, unobscured) after the graph has painted; it captured the whole desktop, other applications included.
- `tyflow_script` says "X is a Display operator, not a Script operator" instead of a raw MAXScript error; `set_tyflow_wiring_ledger` lists the edges a scene-read flow did not record (`ignored_edges`, `status: "partial"`).
- `inspect_plugin_class` (schema_version 1) reads properties that `showClass` prints with a UI name (`.BendAngle (Angle) : float`) and stops at the next class.
- `render_automations(action="status")` reports an unknown job id as `NOT_FOUND` instead of "render still in progress".
- `execute_python` puts `rt` (pymxs.runtime) in scope, as `run_python` does.
- `run_tool_smoke(tier="read")` no longer writes material-library backups to Documents.
- Controller tools (`assign_controller`, `inspect_controller`, `set_controller_props`, `add_controller_target`, `wire_params`, `unwire_params`) share one track resolver: paths from `list_wireable_params` and `inspect_track_view` with spaces (`[#X Position]`), with or without `[#Parameters]`, transform shorthands (`pos`, `pos.x`, `rotation.z`, `scale`) and base-object parameter names (`radius`) all work, and an unknown path is `NOT_FOUND` with the forms that do. `pos.x` used to fail with "Call needs function or class".
- `unwire_params` keeps the value the driven track showed; it dropped to the driver's own value (a height wired as `Width * 2` fell from 30 to 15).
- `keyframe_tracks(action="set")` refuses a `value` it cannot write at every time (`times=[0, 20], value=[0,0,0,100,0,0]` silently keyed 0 twice).
- `select_objects` applies `pattern` and `class_name` together; `class_name` was ignored when both were given.
- `manage_selection_sets(action="create")` replaces a set of that name (`replaced: true`) instead of adding a second one that a later delete left behind.
- `get_effects`, `delete_effect` and sdk `Effect.name` report the effect's own name (`VT_blur`), not its class (`Blur`).
- Class names that several classes share as a UI label resolve to the one with that MAXScript name (`Box` is the Box object, not the Box texture map; `Bend` the modifier, not the space warp), and MAXScript-only names (`SpaceBend`, `BoxtextureMap`) resolve too. `introspect_class` refused all of them as `AMBIGUOUS`; an ambiguity that remains names the candidates.
- `execute_maxscript` returns Point3, color, array and other values as MAXScript prints them (`[10,20,0]`) instead of `OK`.
- `sdk.capture_screen("screen")` needs `desktop=True`, like the tool's `enabled=True`: the desktop shows other applications.
- `max_dialogs` called inside an in-Max script says to use `run_python(where="server")`.
- **`run_python` runs inside 3ds Max.** The bridge exports an in-process entry point, `mcp_bridge_call`, routed exactly like a named-pipe request. When Python is allowed (`safe_mode` off), `run_python` runs the script in Max's own Python: `mx` calls reach the native handlers in-process, `rt`/`pymxs` are in scope, and the script's tool calls are one undo step that rolls back entirely if the script raises. `maxmcp.inmax` loads the unchanged tool layer inside Max for any script. The server and the Windows setup move to Python 3.13, the version 3ds Max 2027 embeds, so the same compiled dependencies load in both.
- **`mx` Python API and `run_python`.** `run_python(code)` runs one script against `maxmcp.mx` instead of many tool calls: `mx.box`, `mx.boolean`, `mx.area_light`, `mx.capture` and other helpers return `mx.Node` handles with live readback (`.bbox`, `.center`, `.move()`), and `mx.tools.<name>(**arguments)` reaches every tool through the same validation and guards. Failures raise `mx.MaxToolError` with the script line; `print()` output, a `result` variable and the list of tool calls come back, with the warnings 3ds Max gave beside a call's result (an open dialog, a call that went to another Max). Scripts get `mx` plus the standard math/json/re family, a time limit, and no file or process access. `expected_pid` on `run_python` covers every tool call the script makes, and one `mx.tools` call takes it too.

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
