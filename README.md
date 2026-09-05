# 3dsmax-mcp

<p align="left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./images/logo-white.png">
    <img src="./images/logo.png" alt="3dsmax-mcp logo" width="600">
  </picture>
</p>

Connect AI agents to Autodesk 3ds Max through the [Model Context Protocol](https://modelcontextprotocol.io).

Automate everything!

**Current release: 1.6.6 — Astra Edition** — see [CHANGELOG.md](docs/CHANGELOG.md).

> 中文文档：[README.zh-CN.md](README.zh-CN.md)

## Features

- **161 MCP tools** — (101 in core profile) for scene reads, modeling, materials, modifiers, controllers, viewport capture, procedural graphs, and plugin workflows.
- **Native Bridge** — only 2023-2027 versions.
- **Introspection** — discover arbitrary Max classes for all kinds of automation and scripting purposes. 
- **Bundled agent skill** — There is a bundled maxscript documentation if you want to create your own tools.

## Requirements

- [Python 3.12+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/)
- Autodesk **3ds Max 2023–2027**

## Quick start

```powershell
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
uv sync
uv run python install.py
```

Choose the MCP tool profile when prompted. **Full** is the default for maximum client compatibility. **Progressive** exposes three discovery tools and loads exact operational schemas only when needed, which can substantially reduce context use for local or smaller models.

Restart 3ds Max, then connect your MCP client. The installer registers the server where it can; see [Advanced configuration](docs/ADVANCED.md) for manual client setup.

**Update an existing install:**

```powershell
git pull
uv sync
uv run python install.py
```

## Tools

### Bridge & session

| Tool | Description |
|------|-------------|
| `get_bridge_status` | Ping the MCP bridge when diagnosing connection errors |
| `get_session_context` | Bundle bridge status, capabilities, scene summary, and selection in one call |
| `get_plugin_capabilities` | Max version, renderers, installed plugins, and class counts |

### Scene state & transactions

| Tool | Description |
|------|-------------|
| `query_scene` | Unified reads: `overview`, `filter`, `class`, `property`, `selection`, `delta` |
| `resolve_node_refs` | Resolve names, handles, or hierarchy paths to canonical, cross-checked NodeRefs |
| `scene_qa` | Scan scene-graph hygiene and apply explicit safe naming repairs; never analyzes meshes |
| `scene_patch` | Preflight and atomically apply up to 256 mechanical node edits in one native undo step |
| `get_hierarchy` | Recursive child tree for an object |
| `get_instances` | All instances sharing the same base object |
| `get_dependencies` | Reference graph via dependents / dependent nodes |

### Objects

| Tool | Description |
|------|-------------|
| `create_object` | Create geometry with spatial placement feedback |
| `delete_objects` | Delete objects by name |
| `get_object_properties` | Compact properties for one object |
| `set_object_property` | Set a single object property |
| `transform_object` | Move, rotate, and/or scale by offset |
| `analyze_node_orientation` | Pivot, bbox, local axes, and world matrix for rigging and placement |
| `clone_objects` | Copy, instance, or reference clones |
| `set_parent` | Parent or unparent objects |
| `select_objects` | Change the current selection |
| `set_visibility` | Show, hide, freeze, or unfreeze |
| `batch_rename_objects` | Rename many objects in one call |

### Modifiers

| Tool | Description |
|------|-------------|
| `add_modifier` | Add a modifier to an object |
| `remove_modifier` | Remove a modifier by name |
| `set_modifier_state` | Enable/disable with viewport/render granularity |
| `set_modifier_property` | Set a modifier parameter on one or many objects |
| `collapse_modifier_stack` | Collapse the stack |
| `make_modifier_unique` | De-instance a shared modifier |
| `inspect_modifier_properties` | Compatibility alias for `inspect_properties(target="modifier")` |

### Modeling

| Tool | Description |
|------|-------------|
| `curve_model` | Named curves, rounded profiles, sweeps and quad lofts with controls saved in the scene |
| `inspect_curve` / `edit_curve` | Inspect, visually target and edit spline knots and handles with stale-token protection |
| `create_mesh` | Build editable polygon cages from explicit vertices and faces |
| `inspect_mesh` / `pick_component` | Read cage geometry, label components and map image positions to edit targets |
| `mesh_edit` | Undoable vertex, edge and face edits that preserve the modifier stack |
| `loft_mesh` | Matched-section quad lofts with persistent numeric parameters |
| `geometry_qa` | Check mesh boundaries, winding, degeneracy and connected components |
| `boolean_operation` | Apply, inspect, retune, rename, or extract Boolean modifier operands; supports inline repeated cutters |
| `draw_spline` | Create, read, and edit spline shapes from explicit world-space points and knots |
| `edit_vertices` | Read, move, set, or conform Editable Poly vertices in world space |

### Materials & textures

| Tool | Description |
|------|-------------|
| `get_materials` | List materials assigned in the scene |
| `get_material_library` | Inspect `currentMaterialLibrary` and Material Editor scratch slots |
| `backup_material_library` | Save temporary/scratch material libraries to `.mat` files |
| `get_material_slots` | Compact slot/property readback for a material |
| `inspect_material_network` | Semantic material graph, wired slots, texture manifest, health checks |
| `replicate_material` | Preview/apply structure-preserving material clone and texture remap |
| `assign_material` | Create a material and assign it to objects |
| `set_material_property` | Set one property on an object's material |
| `set_material_properties` | Set multiple material properties at once |
| `set_sub_material` | Create or assign a Multi/Sub-Object slot |
| `create_texture_map` | Create a texture map (stored as a MAXScript global) |
| `set_texture_map_properties` | Edit a texture map global |
| `create_material_from_textures` | Build a fully wired PBR material from texture files |
| `create_shell_material` | Wrap any render + export materials in Shell Material (dual pipeline) |
| `write_osl_shader` | Write OSL to disk and compile an OSLMap |
| `replace_material` | Swap one material for another on all users |
| `batch_replace_materials` | Batch material replacement |
| `palette_laydown` | Fill Material Editor palette slots from a texture folder |
| `smart_import` | Batch-import meshes from a folder with auto PBR assignment |

### Inspection

| Tool | Description |
|------|-------------|
| `inspect_object` | Deep exploratory object summary |
| `inspect_properties` | Deep property dump (`target="object"|"modifier"|"baseobject"|"material"`) |
| `introspect_osl` | API surface for OSLMap and shader classes |
| `walk_references` | Full reference dependency walk |
| `learn_scene_patterns` | Analyze class usage patterns in the live scene |
| `map_class_relationships` | ParamBlock2 reference relationships between classes |
| `watch_scene` | Live event watcher for interactive sessions |
| `isolate_and_capture_selected` | Per-selection isolated viewport captures |
| `main_thread` | Inspect or clean up callbacks and timers running on Max's main/UI thread |

### Plugins & introspection

| Tool | Description |
|------|-------------|
| `discover_plugin_surface` | Find plugin-related classes and entry points |
| `discover_plugin_classes` | Enumerate registered SDK classes |
| `list_plugin_classes` | List classes for a plugin or superclass family |
| `inspect_plugin_class` | Runtime class scan + showClass reflection |
| `inspect_plugin_constructor` | Creation notes for a plugin class |
| `inspect_plugin_instance` | Live instance inspection with plugin context |
| `get_plugin_manifest` | Structured manifest (classes, workflows, gotchas) |
| `refresh_plugin_manifest` | Rebuild manifest from live runtime |
| `introspect_class` | Full C++ SDK API surface for a class |
| `introspect_instance` | Deep SDK introspection with live values |

MCP resources: `resource://3dsmax-mcp/plugins/{name}/manifest|guide|recipes|gotchas`

### Controllers & animation

| Tool | Description |
|------|-------------|
| `assign_controller` | Create and assign a controller to a sub-anim track |
| `inspect_controller` | Inspect one controller track |
| `inspect_track_view` | Track View-style controller hierarchy |
| `set_controller_props` | Edit script text or controller properties |
| `add_controller_target` | Add a target to script/expression/constraint controllers |
| `keyframe_tracks` | Timeline control; bounded delete/move/scale, bake/resample, tangent normalization, pose matching, and loops |

### Parameter wiring

| Tool | Description |
|------|-------------|
| `list_wireable_params` | Discover wireable sub-anims on an object |
| `wire_params` | Connect parameters with a wire expression |
| `get_wired_params` | List existing wire connections |
| `unwire_params` | Remove a wire |

### Organization

| Tool | Description |
|------|-------------|
| `manage_layers` | Create, delete, list, and configure layers; move/select objects |
| `manage_groups` | Create, ungroup, open, close, attach, detach groups |
| `manage_selection_sets` | Named selection sets |
| `manage_scene` | Hold, fetch, reset, save, scene info |
| `undo_last` | Undo the last 3ds Max scene operation |

### Viewport & render

| Tool | Description |
|------|-------------|
| `agent_viewport` | Independent floating agent view, visual targeting and V-Ray preview controls |
| `set_viewport` | Position and frame the agent or user viewport |
| `capture_viewport` | Capture the agent or user viewport as an image |
| `capture_multi_view` | Capture several views into one image, with agent-view restoration |
| `capture_screen` | Capture visible desktop pixels or crop to the V-Ray frame buffer |
| `render_scene` | Render the current view |
| `render_automations` | Arm/poll completion signals, request cancellation or capture a progressive render before cancelling |

### External `.max` files

| Tool | Description |
|------|-------------|
| `inspect_max_file` | Read metadata and object names without loading |
| `merge_from_file` | Selective merge with duplicate handling |
| `search_max_files` | Scan a folder for objects matching a pattern |
| `batch_file_info` | Parallel metadata from many `.max` files |

### Effects & state sets

| Tool | Description |
|------|-------------|
| `get_effects` | List atmospheric and render effects |
| `toggle_effect` | Enable or disable an effect by index |
| `delete_effect` | Remove an effect by index |
| `get_state_sets` | State Sets with camera assignments |
| `get_camera_sequence` | Camera-assigned State Sets sorted by frame |

### Data Channel

| Tool | Description |
|------|-------------|
| `list_dc_operators` | Search the live version-specific operator catalog |
| `list_dc_presets` | List installed Data Channel presets |
| `add_data_channel` | Append operators to a Data Channel modifier stack |
| `inspect_data_channel` | Read the active stack in visible processing order |
| `set_data_channel_operator` | Edit one visible operator with rollback on failure |
| `manage_data_channel_stack` | Delete, enable, disable, or safely reorder operators |
| `load_dc_preset` | Load a preset into the stack |
| `add_dc_script_operator` | Add executable MAXScript with explicit authorization |

### Max Creation Graph

| Tool | Description |
|------|-------------|
| `mcg_get_context` | Compiler, temporary workspace, templates, samples, and safety state |
| `mcg_list_graphs` | List temporary, installed, or bundled sample graphs |
| `mcg_inspect_graph` | Normalize graph metadata, nodes, connections, and parameters |
| `mcg_search_operators` | Search live typed operators and offline compound references |
| `mcg_create_graph` | Fork a read-only source into the temporary workspace |
| `mcg_apply_patch` | Patch, compile, verify, checkpoint, and roll back transactionally |
| `mcg_compile_graph` | Validate and compile one temporary graph with diagnostics |
| `mcg_test_tool` | Create, inspect, and remove a disposable generated instance |
| `mcg_resolve_class` | Resolve the exact generated modifier class descriptor |
| `mcg_apply_modifier` | Compile and apply a typed MCG modifier safely |
| `mcg_inspect_instance` | Verify graph identity and inspect a live MCG modifier |
| `mcg_set_node_parameter` | Retarget one supported scalar node parameter |
| `mcg_restore_checkpoint` | Restore an opaque checkpoint with hash protection |
| `mcg_cleanup_workspace` | Remove one graph family or the temporary MCG workspace |
| `mcg_reload_operators` | Explicitly refresh Max's global MCG operator depot |

### tyFlow

| Tool | Description |
|------|-------------|
| `list_tyflow_operator_types` | Available operator names for this install |
| `create_tyflow` | Create tyFlow with events and operators |
| `create_tyflow_preset` | Presets: rain, snow, fountain, burst, debris |
| `get_tyflow_info` | Deep flow/event/operator readback |
| `harvest_tyflow_manifest` | Probe and cache the installed tyFlow operator surface by tyFlow version |
| `list_tyflow_operators` | Query the cached tyFlow operator manifest without touching Max |
| `get_tyflow_graph` | Read events, operators, properties, ledger edges, and wiring staleness |
| `tyflow_apply_patch` | Apply a batch of tyFlow graph operations as one verified transaction |
| `connect_tyflow_operator` | Connect or retarget a test/Send Out operator to an event |
| `disconnect_tyflow_operator` | Disconnect an operator output and remove its ledger edge |
| `set_tyflow_wiring_ledger` | Reconcile recorded graph wiring after external or visual edits |
| `tyflow_event_census` | Count particles per event at probe frames with temporary instrumentation |
| `capture_tyflow_editor` | Open and capture the tyFlow editor for visual wire inspection |
| `modify_tyflow_operator` | Edit operator properties |
| `set_tyflow_shape` | Configure Shape operator |
| `set_tyflow_physx` | Object-level PhysX settings |
| `add_tyflow_collision` | Collision operator + collider list |
| `add_tyflow_event` | Add an event |
| `connect_tyflow_events` | Wire Send Out destinations |
| `remove_tyflow_element` | Remove operator or event |
| `get_tyflow_particle_count` | Particle count at a frame |
| `get_tyflow_particles` | Particle data rows |
| `reset_tyflow_simulation` | Reset one or all tyFlow sims |

> **Work in progress** — the plugin and layout integrations below (tyFlow, Forest Pack, RailClone, Floor plan) are early-stage and may be incomplete or change between releases. Everything listed above is stable.

### Forest Pack (WIP)

| Tool | Description |
|------|-------------|
| `scatter_forest_pack` | Create a Forest Pack scatter with surfaces and source geometry |

### RailClone (WIP)

| Tool | Description |
|------|-------------|
| `get_railclone_style_graph` | Read style-editor bases, segments, and parameters |

### Floor plan (WIP)

| Tool | Description |
|------|-------------|
| `build_floor_plan` | Generate a 2D floor plan from grid-based room definitions |


### Scripting & diagnostics

| Tool | Description |
|------|-------------|
| `execute_maxscript` | Run MAXScript when no dedicated tool exists (respects safe mode) |
| `invoke_tool` | Call any registered tool from inside Max (testing) |

---

## Skill & reference

The installer builds an agent skill from `skills/3dsmax-mcp-dev/SKILL.md` with tool-choice rules, material pipeline notes, and MAXScript reference files. Rebuild manually with `python scripts/build_skill.py` — see [Advanced configuration](docs/ADVANCED.md#agent-skill).

## Further reading

- **[Advanced configuration](docs/ADVANCED.md)** — architecture, safe mode, tool profiles, native builds
- **[CHANGELOG.md](docs/CHANGELOG.md)** — release history
- **[LICENSE](LICENSE)**
