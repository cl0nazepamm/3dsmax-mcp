# 3dsmax-mcp Tools Checklist

**Total: 128 tools** | Native C++: 6 | MAXScript: 122

`[x]` = Native C++ (named pipe) | `[ ]` = MAXScript (TCP)

## Scene Read
- [x] `get_scene_info` — scene.py — object list with filters
- [x] `get_selection` — scene.py — selected objects
- [x] `get_scene_snapshot` — snapshots.py — compact scene overview
- [x] `get_selection_snapshot` — snapshots.py — selected objects detail
- [ ] `get_scene_delta` — snapshots.py — track changes since baseline

## Hierarchy
- [x] `get_hierarchy` — hierarchy.py — recursive child tree
- [ ] `set_parent` — hierarchy.py — parent/unparent objects

## Bridge
- [x] `get_bridge_status` — bridge.py — connection health check

## Execute
- [ ] `execute_maxscript` — execute.py — raw MAXScript eval

## Objects
- [ ] `get_object_properties` — objects.py — read object props
- [ ] `set_object_property` — objects.py — set object prop
- [ ] `create_object` — objects.py — create scene object
- [ ] `delete_objects` — objects.py — delete objects

## Transform
- [ ] `transform_object` — transform.py — move/rotate/scale

## Selection
- [ ] `select_objects` — selection.py — select objects by name

## Visibility
- [ ] `set_visibility` — visibility.py — show/hide/freeze

## Clone
- [ ] `clone_objects` — clone.py — copy/instance/reference

## Inspect
- [ ] `inspect_object` — inspect.py — deep object inspection
- [ ] `inspect_properties` — inspect.py — property enumeration
- [ ] `inspect_modifier_properties` — inspect.py — modifier props

## Materials
- [ ] `get_materials` — materials.py — list scene materials
- [ ] `assign_material` — materials.py — assign material to object
- [ ] `set_material_property` — materials.py — set material prop
- [ ] `set_material_properties` — materials.py — batch set material props
- [ ] `get_material_slots` — materials.py — material slot info

## Material Operations
- [ ] `create_texture_map` — material_ops.py — create texture map
- [ ] `set_texture_map_properties` — material_ops.py — set texture props
- [ ] `set_sub_material` — material_ops.py — set sub-material
- [ ] `create_material_from_textures` — material_ops.py — material from textures
- [ ] `write_osl_shader` — material_ops.py — write OSL shader

## Modifiers
- [ ] `add_modifier` — modifiers.py — add modifier
- [ ] `remove_modifier` — modifiers.py — remove modifier
- [ ] `set_modifier_state` — modifiers.py — enable/disable modifier
- [ ] `collapse_modifier_stack` — modifiers.py — collapse stack
- [ ] `make_modifier_unique` — modifiers.py — make instance unique
- [ ] `batch_modify` — modifiers.py — batch modify objects

## Controllers
- [ ] `assign_controller` — controllers.py — assign animation controller
- [ ] `inspect_controller` — controllers.py — inspect controller
- [ ] `inspect_track_view` — controllers.py — browse track view
- [ ] `add_controller_target` — controllers.py — add list controller target
- [ ] `set_controller_props` — controllers.py — set controller properties

## Wire Parameters
- [ ] `list_wireable_params` — wire_params.py — list wireable params
- [ ] `wire_params` — wire_params.py — wire two params
- [ ] `get_wired_params` — wire_params.py — get wired params
- [ ] `unwire_params` — wire_params.py — unwire params

## Data Channel
- [ ] `add_data_channel` — data_channel.py — add data channel modifier
- [ ] `inspect_data_channel` — data_channel.py — inspect data channel
- [ ] `set_data_channel_operator` — data_channel.py — set operator
- [ ] `add_dc_script_operator` — data_channel.py — add script operator
- [ ] `list_dc_presets` — data_channel.py — list presets
- [ ] `load_dc_preset` — data_channel.py — load preset

## Scene Query
- [ ] `find_class_instances` — scene_query.py — find all instances of a class
- [ ] `get_instances` — scene_query.py — find instanced copies
- [ ] `get_dependencies` — scene_query.py — trace reference graph
- [ ] `find_objects_by_property` — scene_query.py — find by property value

## Scene Management
- [ ] `manage_scene` — scene_manage.py — new/open/save/merge

## Effects
- [ ] `get_effects` — effects.py — list render effects
- [ ] `toggle_effect` — effects.py — enable/disable effect
- [ ] `delete_effect` — effects.py — delete effect

## State Sets
- [ ] `get_state_sets` — state_sets.py — list state sets
- [ ] `get_camera_sequence` — state_sets.py — camera sequence info

## Session
- [ ] `get_session_context` — session_context.py — fast live context

## Viewport
- [ ] `capture_viewport` — viewport.py — capture viewport image
- [ ] `capture_model` — viewport.py — capture model view
- [ ] `capture_screen` — viewport.py — capture full screen

## Render
- [ ] `render_scene` — render.py — render scene

## Plugins
- [ ] `discover_plugin_surface` — plugins.py — discover plugin API surface
- [ ] `list_plugin_classes` — plugins.py — list plugin classes
- [ ] `inspect_plugin_class` — plugins.py — inspect plugin class
- [ ] `inspect_plugin_constructor` — plugins.py — inspect constructor
- [ ] `inspect_plugin_instance` — plugins.py — inspect live instance
- [ ] `get_plugin_manifest` — plugins.py — get plugin manifest
- [ ] `refresh_plugin_manifest` — plugins.py — refresh manifest cache
- [ ] `get_plugin_capabilities` — capabilities.py — plugin capabilities

## Scattering
- [ ] `scatter_forest_pack` — scattering.py — Forest Pack scatter

## RailClone
- [ ] `get_railclone_style_graph` — railclone.py — read style graph

## tyFlow
- [ ] `list_tyflow_operator_types` — tyflow.py — list operator types
- [ ] `create_tyflow` — tyflow.py — create tyFlow
- [ ] `get_tyflow_info` — tyflow.py — inspect tyFlow
- [ ] `add_tyflow_event` — tyflow.py — add event
- [ ] `modify_tyflow_operator` — tyflow.py — modify operator
- [ ] `set_tyflow_shape` — tyflow.py — set shape operator
- [ ] `connect_tyflow_events` — tyflow.py — connect events
- [ ] `add_tyflow_collision` — tyflow.py — add collision
- [ ] `set_tyflow_physx` — tyflow.py — set PhysX
- [ ] `remove_tyflow_element` — tyflow.py — remove element
- [ ] `get_tyflow_particle_count` — tyflow.py — particle count
- [ ] `get_tyflow_particles` — tyflow.py — get particles
- [ ] `reset_tyflow_simulation` — tyflow.py — reset sim
- [ ] `create_tyflow_preset` — tyflow.py — create from preset

## Verified Workflows
- [ ] `inspect_active_target` — workflows.py — smart inspect current target
- [ ] `create_object_verified` — workflows.py — create + verify
- [ ] `assign_material_verified` — workflows.py — assign material + verify
- [ ] `set_material_verified` — workflows.py — set material prop + verify
- [ ] `add_modifier_verified` — workflows.py — add modifier + verify
- [ ] `transform_object_verified` — workflows.py — transform + verify
- [ ] `set_modifier_state_verified` — workflows.py — modifier state + verify
- [ ] `set_object_property_verified` — workflows.py — set prop + verify
- [ ] `create_tyflow_basic_verified` — plugin_workflows.py — tyFlow + verify
- [ ] `create_tyflow_scatter_from_objects_verified` — plugin_workflows.py — scatter + verify
- [ ] `verify_scatter_output` — verification.py — verify scatter result

## Build & Layout
- [ ] `build_structure` — build.py — parametric structure builder
- [ ] `build_floor_plan` — floor_plan.py — floor plan from spec
- [ ] `batch_rename_objects` — identify.py — batch rename
- [ ] `isolate_and_capture_selected` — identify.py — isolate + capture
- [ ] `place_on_grid` — grid.py — place object on grid
- [ ] `place_grid_array` — grid.py — array on grid
- [ ] `place_circle` — grid.py — circular array
