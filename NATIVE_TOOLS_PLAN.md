# Plan: Rebuild All Tools as Native C++ Handlers

## Context
6 tools already run through the C++ native pipe (86-130x faster). Now migrating ALL remaining ~96 tools (except `execute_maxscript`). Ships .gup binary in repo. MAXScript TCP fallback stays for Max 2024/2025 without the .gup.

## Two Strategies Per Tool

- **Pure SDK** — Direct C++ SDK calls. 86-130x speedup. For scene reads, node manipulation.
- **Hybrid** — C++ handler receives JSON params, builds MAXScript internally, calls `ExecuteMAXScriptScript()`. ~5-8x speedup (pipe transport). For class-name creation, plugin APIs, property-by-name.

## Infrastructure First

**`native/include/mcp_bridge/handler_helpers.h`** — Extract shared helpers:
- `WideToUtf8`, `Utf8ToWide`, `NodeClassName`, `NodeLayerName`, `NodePosition`, `NodeWireColor`, `CollectNodes` (from `scene_handlers.cpp`)
- New: `FindNodeByName(string)`, `RunMAXScript(string, MCPBridgeGUP*)` (for hybrid handlers)

**Refactor dispatcher** to `unordered_map<string, HandlerFunc>` after Phase 3.

## Implementation Phases

### Phase 1: `object_handlers.cpp` (8 tools) — ALL Pure SDK
| Tool | Python | SDK Approach |
|------|--------|-------------|
| `get_object_properties` | objects.py | INode props + IDerivedObject modifiers + bbox |
| `set_object_property` | objects.py | INode direct props + IParamBlock2 by `internal_name` lookup |
| `create_object` | objects.py | `DllDir::ClassDir()` name→ClassDesc lookup + `CreateInstance()` + IParamBlock2 params |
| `delete_objects` | objects.py | `Interface::DeleteNode()` loop |
| `transform_object` | transform.py | `INode::Move/Rotate/Scale` or `SetNodeTM()` |
| `select_objects` | selection.py | `Interface::SelectNode/ClearNodeSelection/SelectNodeTab` |
| `set_visibility` | visibility.py | `INode::Hide()/Freeze()` |
| `clone_objects` | clone.py | `Interface::CloneNodes()` with CloneType enum |

### Phase 2: `modifier_handlers.cpp` (6 tools)
| Tool | Strategy |
|------|----------|
| `add_modifier` | Hybrid |
| `remove_modifier` | Pure SDK |
| `set_modifier_state` | Hybrid |
| `collapse_modifier_stack` | Hybrid |
| `make_modifier_unique` | Hybrid |
| `batch_modify` | Hybrid |

### Phase 3: `material_handlers.cpp` (5 tools)
| Tool | Strategy |
|------|----------|
| `get_materials` | Pure SDK |
| `assign_material` | Hybrid |
| `set_material_property` | Hybrid |
| `set_material_properties` | Hybrid |
| `get_material_slots` | Hybrid |

### Phase 4: `inspect_handlers.cpp` (3 tools)
| Tool | Strategy |
|------|----------|
| `inspect_object` | Pure SDK |
| `inspect_properties` | Hybrid |
| `inspect_modifier_properties` | Redirects to inspect_properties |

### Phase 5: `viewport_handlers.cpp` (4 tools)
All Hybrid — `capture_viewport`, `capture_model`, `capture_screen`, `render_scene`

### Phase 6: `effects_handlers.cpp` (3 tools)
| Tool | Strategy |
|------|----------|
| `get_effects` | Pure SDK |
| `toggle_effect` | Hybrid |
| `delete_effect` | Hybrid |

### Phase 7: `controller_handlers.cpp` (5 tools)
All Hybrid — `assign_controller`, `inspect_controller`, `inspect_track_view`, `add_controller_target`, `set_controller_props`

### Phase 8: `scene_manage_handlers.cpp` (6 tools)
All Hybrid — `manage_scene`, `batch_rename_objects`, `get_state_sets`, `get_camera_sequence`, `get_instances`, `get_dependencies`

### Phase 9: `build_handlers.cpp` (7 tools)
All Hybrid — `build_structure`, `build_floor_plan`, `place_on_grid`, `place_grid_array`, `place_circle`, `isolate_and_capture_selected`, `find_objects_by_property`

### Phase 10: `wire_handlers.cpp` (4 tools)
All Hybrid — `list_wireable_params`, `wire_params`, `get_wired_params`, `unwire_params`

### Phase 11: `data_channel_handlers.cpp` (6 tools)
All Hybrid — all data channel tools

### Phase 12: `plugin_handlers.cpp` (8 tools)
All Hybrid — all plugin discovery tools + `get_plugin_capabilities`

### Phase 13: `tyflow_handlers.cpp` (14 tools)
All Hybrid — all tyFlow tools

### Phase 14: `scatter_handlers.cpp` (2 tools)
All Hybrid — `scatter_forest_pack`, `get_railclone_style_graph`

### Skip (no C++ handler needed)
- 12 composition tools (verified workflows, `get_session_context`, `get_scene_delta`) — benefit automatically
- `execute_maxscript` — comes last per user request

## Per-Phase Workflow
1. Create/update handler `.cpp`
2. Add declarations to `native_handlers.h`
3. Add routes to `command_dispatcher.cpp`
4. Add source to `CMakeLists.txt`
5. Update Python tools: `if client.native_available:` send JSON → else MAXScript fallback
6. Build → deploy to Max plugins + `native/bin/`
7. Test each tool

## Key Files
- `native/src/handlers/scene_handlers.cpp` — reference pattern
- `native/src/command_dispatcher.cpp` — routing + HandleMaxScript (extract for RunMAXScript)
- `native/include/mcp_bridge/native_handlers.h` — declarations
- `src/tools/scene.py` — reference Python fallback pattern (lines 27-45)
- `native/CMakeLists.txt` — add new source files

## Start
Infrastructure (shared helpers + RunMAXScript) → Phase 1 → build & test → Phase 2 → ...
