# Native C++ Handler Migration Plan

Current state: All 93 tools send MAXScript strings through the C++ pipe. This plan adds native SDK handlers so the C++ plugin does the work directly — no MAXScript parsing.

## How It Works

Each native handler is a C++ function registered in the command dispatcher. The Python tool switches from sending MAXScript code to sending a structured JSON command:

```python
# Before: builds MAXScript string, C++ evals it
client.send_command("(for o in objects collect ...)", cmd_type="maxscript")

# After: sends structured params, C++ handles natively
client.send_command('{"limit":50}', cmd_type="native:scene_info")
```

One-line change per Python tool. C++ does the rest.

---

## Tier 1 — Scene Reads (biggest impact)

These are called constantly. Currently each one builds and evaluates large MAXScript strings that loop over every object.

### `native:scene_info`
**Python tool:** `get_scene_info`
**Current:** ~50 lines of MAXScript, loops all objects, string concatenation
**Native:** `INode` tree walk, direct `ClassName()`, `IsHidden()`, `IsFrozen()` checks
**Params:** `{ "class_name": "", "pattern": "", "limit": 100 }`
**Returns:** `{ "totalObjects", "classCounts", "layers", "hiddenCount", "frozenCount" }`

### `native:scene_snapshot`
**Python tool:** `get_scene_snapshot`
**Current:** Per-object MAXScript property reads, huge string
**Native:** Single pass over `INode` tree, batch read `GetNodeTM()`, `GetMtl()`, modifier stack
**Params:** `{ "max_roots": 50 }`
**Returns:** `{ "roots": [{ "name", "class", "pos", "material", "modifiers", "children" }] }`

### `native:selection_snapshot`
**Python tool:** `get_selection_snapshot`
**Current:** Same per-object loop pattern
**Native:** `GetSelNodeCount()` / `GetSelNode(i)`, batch property read
**Params:** `{ "max_items": 50 }`
**Returns:** `{ "count", "items": [{ "name", "class", "pos", "material", "modifiers" }] }`

### `native:selection`
**Python tool:** `get_selection`
**Current:** MAXScript `selection as array`
**Native:** `GetSelNodeCount()` / `GetSelNode(i)` loop
**Params:** `{}`
**Returns:** `{ "count", "names": [...] }`

### `native:scene_delta`
**Python tool:** `get_scene_delta`
**Current:** Captures full scene state in MAXScript, diffs
**Native:** Store `INode` handle set on capture, diff on next call
**Params:** `{ "capture": false }`
**Returns:** `{ "added": [], "removed": [], "modified": [] }`

### `native:find_class_instances`
**Python tool:** `find_class_instances`
**Current:** `for o in objects where classof o == X collect...`
**Native:** Walk `INode` tree, compare `Object::ClassID()`
**Params:** `{ "class_name": "Box", "limit": 100 }`
**Returns:** `{ "count", "instances": [{ "name", "pos" }] }`

### `native:find_objects_by_property`
**Python tool:** `find_objects_by_property`
**Current:** MAXScript property check per object
**Native:** `ParamBlock2` property reads during tree walk
**Params:** `{ "property": "radius", "value": "25", "limit": 100 }`
**Returns:** `{ "matches": [{ "name", "value" }] }`

---

## Tier 2 — Object Inspection & Transforms

### `native:inspect_object`
**Python tool:** `inspect_object`
**Current:** Multiple MAXScript evals (pos, rot, scale, material, modifiers, properties)
**Native:** One `INode*` lookup, read everything in one shot
**Params:** `{ "name": "Box001" }`
**Returns:** `{ "name", "class", "superclass", "pos", "rotation", "scale", "wireColor", "material", "modifiers": [...], "parent", "children", "properties": {...} }`

### `native:get_object_properties`
**Python tool:** `get_object_properties`
**Current:** Builds MAXScript to read properties via `getPropNames`
**Native:** `IParamBlock2` enumeration, direct value reads
**Params:** `{ "name": "Box001" }`
**Returns:** `{ "name", "class", "pos", "rotation", "scale", "material", "modifiers", "properties": {...} }`

### `native:transform_object`
**Python tool:** `transform_object`
**Current:** MAXScript string `$.pos = [x,y,z]`
**Native:** `INode::SetNodeTM()`, `INode::Move()`, `INode::Rotate()`, `INode::Scale()`
**Params:** `{ "name": "Box001", "position": [0,0,0], "rotation": [0,0,0], "scale": [1,1,1], "mode": "absolute" }`
**Returns:** `{ "name", "pos", "rotation", "scale" }`

### `native:get_hierarchy`
**Python tool:** `get_hierarchy`
**Current:** Recursive MAXScript string
**Native:** `INode::GetChildNode()` pointer walk, recursive
**Params:** `{ "name": "Root001" }`
**Returns:** `{ "name", "class", "children": [{ ... recursive ... }] }`

### `native:create_object`
**Python tool:** `create_object`
**Current:** MAXScript `Box()` string eval
**Native:** `CreateInstance(Class_ID, ...)` + `AddNewNode()`
**Params:** `{ "type": "Box", "name": "MyBox", "params": { "length": 10, "width": 10, "height": 10 } }`
**Returns:** `{ "name", "class", "pos" }`

### `native:delete_objects`
**Python tool:** `delete_objects`
**Current:** MAXScript `delete $'name'` per object
**Native:** `GetINodeByName()` → `DeleteNode()` batch
**Params:** `{ "names": ["Box001", "Box002"] }`
**Returns:** `{ "deleted": [...], "notFound": [...] }`

### `native:set_object_property`
**Python tool:** `set_object_property`
**Current:** MAXScript `execute ("$.prop = value")`
**Native:** `IParamBlock2::SetValue()` direct
**Params:** `{ "name": "Box001", "property": "height", "value": 50 }`
**Returns:** `{ "name", "property", "value" }`

---

## Tier 3 — Materials & Modifiers

### `native:get_materials`
**Python tool:** `get_materials`
**Current:** MAXScript loops `sceneMaterials`
**Native:** `GetCOREInterface()->GetSceneMtls()` enumeration
**Params:** `{}`
**Returns:** `{ "count", "materials": [{ "name", "class", "subMtlCount" }] }`

### `native:inspect_modifier`
**Python tool:** `inspect_modifier_properties`
**Current:** MAXScript `getPropNames` on modifier
**Native:** `IDerivedObject::GetModifier()` → `IParamBlock2` reads
**Params:** `{ "name": "Box001", "modifier_index": 0 }`
**Returns:** `{ "name", "class", "enabled", "properties": {...} }`

### `native:add_modifier`
**Python tool:** `add_modifier`
**Current:** MAXScript `addModifier $ (Bend())`
**Native:** `CreateInstance()` modifier + `AddModifier()`
**Params:** `{ "name": "Box001", "modifier": "Bend", "params": { "angle": 45 } }`
**Returns:** `{ "name", "modifier", "index" }`

### `native:remove_modifier`
**Python tool:** `remove_modifier`
**Current:** MAXScript `deleteModifier`
**Native:** `DeleteModifier()` direct
**Params:** `{ "name": "Box001", "modifier": "Bend" }`
**Returns:** `{ "removed": "Bend" }`

### `native:batch_modify`
**Python tool:** `batch_modify`
**Current:** N separate MAXScript evals
**Native:** Single loop, N operations in one call
**Params:** `{ "objects": ["Box001","Box002"], "operations": [{ "type": "add_modifier", "modifier": "Bend" }] }`
**Returns:** `{ "results": [...] }`

---

## Tier 4 — Viewport & Capture

### `native:capture_viewport`
**Python tool:** `capture_viewport`
**Current:** MAXScript `gw.getViewportDib()` → save to file → Python reads file
**Native:** `ViewExp::GetDIB()` → encode PNG in memory → return base64
**Params:** `{ "width": 0, "height": 0 }`
**Returns:** raw PNG bytes (or base64 string)

### `native:capture_model`
**Python tool:** `capture_model`
**Current:** Same file roundtrip
**Native:** Same direct capture, zoom extents first

---

## Implementation Order

### Phase 2a — Scene Reads (do first)
1. `native:scene_info`
2. `native:selection`
3. `native:scene_snapshot`
4. `native:selection_snapshot`
5. `native:find_class_instances`
6. `native:get_hierarchy`

### Phase 2b — Object Operations
7. `native:inspect_object`
8. `native:get_object_properties`
9. `native:transform_object`
10. `native:create_object`
11. `native:delete_objects`
12. `native:set_object_property`

### Phase 2c — Materials & Modifiers
13. `native:get_materials`
14. `native:inspect_modifier`
15. `native:add_modifier`
16. `native:remove_modifier`
17. `native:batch_modify`

### Phase 2d — Viewport
18. `native:capture_viewport`
19. `native:capture_model`

### Not migrated (keep as MAXScript)
These are complex, rarely called, or depend on third-party plugin MAXScript APIs:
- `build_structure`, `build_floor_plan` — procedural builders, MAXScript is fine
- `scatter_forest_pack` — Forest Pack's own MAXScript API
- `get_railclone_style_graph` — RailClone's own MAXScript API
- All `tyflow_*` tools — tyFlow's own MAXScript API
- `write_osl_shader` — text file writing
- `wire_params`, `unwire_params` — MAXScript `paramWire` API
- All `state_sets`, `effects`, `data_channel` tools — niche, low frequency
- All `verified` workflow tools — wrappers that call other tools, migrate automatically when underlying tools migrate

---

## C++ Architecture

```
native/src/command_dispatcher.cpp
  ├── "maxscript"           → ExecuteMAXScriptScript (existing)
  ├── "ping"                → HandlePing (existing)
  ├── "native:scene_info"   → handlers/scene_handlers.cpp
  ├── "native:selection"    → handlers/scene_handlers.cpp
  ├── "native:inspect_object" → handlers/object_handlers.cpp
  ├── "native:transform_object" → handlers/object_handlers.cpp
  └── ...

native/src/handlers/
  scene_handlers.cpp     — scene_info, scene_snapshot, selection, find_class, hierarchy
  object_handlers.cpp    — inspect, properties, transform, create, delete
  material_handlers.cpp  — get_materials, inspect_modifier, add/remove modifier
  viewport_handlers.cpp  — capture_viewport, capture_model
```

Each handler:
1. Parses JSON params from the command string
2. Runs on main thread via `MainThreadExecutor::ExecuteSync`
3. Calls SDK functions directly
4. Returns JSON result string

---

## Expected Performance

| Operation | MAXScript (current) | Native (expected) |
|---|---|---|
| `get_scene_info` (233 objects) | ~200ms | <5ms |
| `get_scene_snapshot` (50 roots) | ~500ms | <10ms |
| `inspect_object` | ~50ms | <1ms |
| `transform_object` | ~30ms | <1ms |
| `find_class_instances` | ~150ms | <3ms |
| `capture_viewport` | ~100ms (file I/O) | <20ms (in-memory) |
| Bulk transforms (233 objects) | ~7s (233 roundtrips) | <10ms (one call) |
