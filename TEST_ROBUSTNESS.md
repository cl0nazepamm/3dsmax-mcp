# MCP Tool Robustness Testing

Adversarial QA: run a weak/frontier model via Codex, analyze JSONL logs, fix why it fails.
Source: `.codex/sessions/2026/03/21/` — GPT 5.4 (frontier) sessions.

## Global Fixes (all tools)

| Fix | Status | Details |
|---|---|---|
| List param coercion | DONE | `src/coerce.py` — `StrList`, `IntList`, `FloatList`, `DictList` wrap single values and CSV strings into lists. All 126 tool signatures updated. |
| CSV string coercion | DONE | `"255,0,0"` → `[255,0,0]`, `"a,b,c"` → `["a","b","c"]` |
| Stringified JSON array | DONE | `'["a","b"]'` → `["a","b"]` — models sometimes stringify the whole array |

## Per-Tool Results

### create_object
| Issue | Status | Details |
|---|---|---|
| Empty params → radius:0 / invisible objects | DONE | `_TYPE_DEFAULTS` dict in `objects.py` injects sensible sizes (e.g. `radius:25`) when `params=""` |

### manage_layers
| Issue | Status | Details |
|---|---|---|
| `names` as CSV string | DONE | Coercion handles it |
| No pattern support → model abandons tool | DONE | Added `pattern` param with `fnmatch` resolution |
| Pattern case-sensitive | DONE | `fnmatch` now case-insensitive |

### assign_controller
| Issue | Status | Details |
|---|---|---|
| `variables: ""` instead of `[]` | DONE | Coercion handles it |
| Model uses `float_expression` when it needs `float_script` | DONE | Improved docstring warns that `Float_Expression` only supports math, not MAXScript object refs |

### set_controller_props
| Issue | Status | Details |
|---|---|---|
| Model sends MAXScript to `Float_Expression` via `script` param | DONE | Docstring now clearly distinguishes `float_script` (MAXScript) vs `Float_Expression` (math only) |

### place_grid_array
| Issue | Status | Details |
|---|---|---|
| `color: "255,0,0"` string | DONE | Coercion handles CSV→IntList |

### set_object_property
| Issue | Status | Details |
|---|---|---|
| Model sends wrong property names (`radius1` for Cylinder, which uses `radius`) | DONE | Docstring now advises calling `get_object_properties` first if unsure |

### wire_params
| Issue | Status | Details |
|---|---|---|
| Model invents paths like `[#Object (Cylinder)][#Parameters][#height]` instead of using `list_wireable_params` output | DONE | Docstring now warns paths MUST come from `list_wireable_params` |

### execute_maxscript
| Issue | Status | Details |
|---|---|---|
| Model sends `{"command": "..."}` instead of `{"code": "..."}` | DONE | Now accepts both `code` and `command` params |
| 29 MAXScript syntax failures | N/A | Intrinsic model limitation — model doesn't know MAXScript |

### capture_multi_view
| Issue | Status | Details |
|---|---|---|
| `views: '["front","right","top","perspective"]'` stringified JSON | DONE | Coercion now parses stringified JSON arrays |

### inspect_controller
| Issue | Status | Details |
|---|---|---|
| 5 "failures" are actually successful returns containing "error" substring in the expression value | FALSE POSITIVE | Detection artifact — tool works fine |

### list_wireable_params
| Issue | Status | Details |
|---|---|---|
| Model queries non-existent object name | N/A | User error / timing issue (object not yet created) |

## Tools with 0 failures (no action needed)

assign_material, find_objects_by_property, get_bridge_status, get_camera_sequence,
get_dependencies, get_effects, get_hierarchy, get_materials, get_object_properties,
get_scene_delta, get_scene_info, get_scene_snapshot, get_session_context,
inspect_active_target, inspect_object, inspect_plugin_instance, inspect_track_view,
learn_scene_patterns, manage_groups, manage_scene, write_osl_shader

## Remaining to test

Tools not exercised in these sessions — need targeted test prompts:

- [ ] batch_modify
- [ ] batch_rename_objects
- [ ] batch_replace_materials
- [ ] build_floor_plan / build_structure
- [ ] capture_viewport / capture_multi_view / capture_screen
- [ ] clone_objects
- [ ] collapse_modifier_stack
- [ ] create_material_from_textures / create_shell_material
- [ ] create_tyflow / add_tyflow_event / tyflow operators
- [ ] data_channel tools
- [ ] delete_objects
- [ ] discover_plugin_classes / discover_plugin_surface
- [ ] get_selection / get_selection_snapshot
- [ ] introspect_class / introspect_instance
- [ ] manage_selection_sets
- [ ] merge_from_file / inspect_max_file
- [ ] modifiers (add/remove/state/unique)
- [ ] place_on_grid / place_circle
- [ ] scatter_forest_pack
- [ ] select_objects
- [ ] set_parent
- [ ] set_visibility
- [ ] transform_object / transform_object_verified
- [ ] unwire_params
