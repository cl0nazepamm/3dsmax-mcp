# Self-Describing Plugin Runtime

## Purpose

This document defines the implementation plan for a generic plugin-introspection and plugin-orchestration system in `3dsmax-mcp`.

Goal:
- let the LLM discover plugin surfaces naturally
- let it inspect classes, instances, properties, methods, and relationships
- avoid building one wrapper per parameter or mirroring the full 3ds Max UI

This system must work for:
- tyFlow
- Forest Pack
- RailClone
- renderer-specific materials/maps
- studio plugins
- future plugins we do not know yet

## Non-Goals

Do not:
- build wrappers for every plugin parameter
- attempt full UI parity for every plugin
- rely only on hand-written docs instead of runtime inspection
- trust mutation success strings without readback
- assume every plugin is fully introspectable from MAXScript alone

## Design Principles

### 1. Generic first

Build generic discovery and inspection before plugin-specific adapters.

### 2. Runtime truth over memory

The LLM should learn from the running host:
- available classes
- actual properties
- actual types
- actual instances
- actual installed plugins

### 3. Verify mutations with runtime introspection

After any mutation, re-read state via `introspect_instance`, `get_scene_delta`,
or a tool-specific readback. Prefer post-hoc verification over composed
"before/after" wrapper handlers — wrappers duplicate data and drift as
plugins evolve.

### 4. Keep token cost low

Discovery must start with compact summaries.
Deep dumps should be opt-in.

## High-Level Architecture

The system has three layers.

### Layer A: Plugin Discovery

Answer:
- what plugins/classes are available?
- which classes appear related to a plugin?
- which are entry-point classes vs helper classes?
- which already exist in the scene?

Primary output:
- compact plugin surface summaries

### Layer B: Class and Instance Introspection

Answer:
- what does this class expose?
- is it creatable?
- what properties and methods exist?
- what are likely enums, node refs, arrays, booleans, numerics?
- what does this live instance look like?

Primary output:
- structured manifests and typed instance snapshots

### Layer C: Plugin Manifests and Resources

Answer:
- what is the stable, reusable understanding of this plugin?
- what are the known-safe defaults?
- what recipes are worth exposing?
- what gotchas are known?

Primary output:
- resource-backed manifests
- optional cached JSON

## Proposed Tool Surface

## 1. Generic Discovery Tools

### `discover_plugin_surface(plugin_name: str = "", class_limit: int = 100) -> str`

Purpose:
- discover plugin-related classes and runtime availability

Behavior:
- if `plugin_name` is empty, return all recognized plugin families
- if `plugin_name` is provided, return matching classes and scene presence

Should return:
- plugin name
- installed boolean
- likely class names
- likely entry classes
- likely helper classes
- scene instance count summary
- notes on whether plugin appears object-based, modifier-based, material-based, or mixed

Example shape:

```json
{
  "plugin": "tyFlow",
  "installed": true,
  "families": ["geometry", "helper"],
  "entryClasses": ["tyFlow"],
  "relatedClasses": ["tyFlow", "tyMesher", "tyCache", "tySelect"],
  "sceneInstances": {
    "tyFlow": 2,
    "tyMesher": 0
  },
  "truncated": false
}
```

### `list_plugin_classes(plugin_name: str = "", superclass: str = "", limit: int = 200) -> str`

Purpose:
- enumerate classes that are likely tied to a plugin or superclass family

Use cases:
- quickly learn the runtime surface
- feed manifest generation

## 2. Generic Class Inspection Tools

### `inspect_plugin_class(class_name: str, include_methods: bool = True, include_properties: bool = True) -> str`

Purpose:
- inspect a class without requiring a live instance

Should attempt to return:
- class name
- superclass
- creatable boolean
- constructor notes if detectable
- property list
- declared type hints where possible
- methods/interfaces if discoverable
- guessed category for each property

Property categories:
- `node`
- `node_array`
- `string`
- `enum_like`
- `bool`
- `numeric`
- `color`
- `texturemap`
- `subanim`
- `unknown`

Important:
- this tool must clearly mark inferred information as inferred
- it should avoid pretending certainty where MAXScript reflection is weak

Example shape:

```json
{
  "class": "tyFlow",
  "superclass": "GeometryClass",
  "creatable": true,
  "properties": [
    {"name": "someProp", "declaredType": "boolean", "category": "bool"},
    {"name": "eventList", "declaredType": "MAXWrapper", "category": "unknown"}
  ],
  "methods": ["someMethod", "reset"],
  "interfaces": ["...", "..."],
  "warnings": ["Some nested UI-only structures may not be reflectable."]
}
```

### `inspect_plugin_constructor(class_name: str) -> str`

Purpose:
- return creation notes for a class

Output should include:
- creatable or not
- likely constructor form
- likely required post-creation wiring
- known failures

This is mostly useful for object-based plugins and renderer-specific map/material classes.

## 3. Generic Instance Inspection Tools

### `inspect_plugin_instance(name: str, detail: str = "normal") -> str`

Purpose:
- inspect a live scene instance with plugin-aware summarization

Behavior:
- start from generic `inspect_object`
- detect whether the object appears to belong to a plugin family
- inspect class-specific properties
- summarize node references, arrays, targets, sources, and nested structures

Detail levels:
- `summary`
- `normal`
- `full`

Output should prefer:
- counts before values
- names before full dumps
- null/broken reference warnings

### `inspect_plugin_subtree(name: str, max_depth: int = 2) -> str`

Purpose:
- inspect nested object/plugin structures where a single root controls a graph

Useful for:
- tyFlow-style event/operator trees
- compound plugin systems with nested references

## 4. Manifest and Resource Tools

### `get_plugin_manifest(plugin_name: str) -> str`

Purpose:
- return the normalized manifest for a plugin

Manifest fields:
- plugin name
- installed
- discovered classes
- entry classes
- key properties by class
- enum hints
- known recipes
- warnings/gotchas
- manifest version
- generation timestamp

### `refresh_plugin_manifest(plugin_name: str, force: bool = False) -> str`

Purpose:
- rebuild the manifest from runtime inspection and curated overlays

### MCP resources

Expose manifests as resources:
- `resource://3dsmax-mcp/plugins/{plugin}/manifest`
- `resource://3dsmax-mcp/plugins/{plugin}/recipes`
- `resource://3dsmax-mcp/plugins/{plugin}/gotchas`

This lets LLMs load plugin knowledge only when needed.

## Manifest System Design

Use a hybrid model:

### Runtime-generated base manifest

Generated from:
- installed classes
- class inspection
- scene instance inspection
- property typing heuristics

### Curated overlay

Stored on disk for plugins where runtime reflection is incomplete.

Suggested folder:
- `src/plugin_manifests/`

Suggested files:
- `src/plugin_manifests/tyflow.json`
- `src/plugin_manifests/forest_pack.json`
- `src/plugin_manifests/railclone.json`

Overlay should contain:
- friendly names
- workflow notes
- enum labels
- safe defaults
- reflectability warnings
- known unstable creation paths

### Merge rules

Runtime discovery is the source of truth for:
- installation
- available classes
- live instance presence

Curated overlay is the source of truth for:
- recipe names
- safe defaults
- gotchas
- UI-only concepts
- enum labeling when reflection is weak

## Implementation Phases

## Phase 1: Generic Discovery Foundation

Deliver:
- `discover_plugin_surface`
- `list_plugin_classes`
- `inspect_plugin_class`

Implementation notes:
- start with `find_class_instances`, `showProperties`, `showInterfaces`, `showMethods`, `getPropNames`
- use compact JSON summaries
- avoid deep recursion in first version

Success criteria:
- can identify installed plugin families
- can enumerate likely plugin classes
- can inspect class surfaces without live instances

## Phase 2: Instance Inspection

Deliver:
- `inspect_plugin_instance`
- `inspect_plugin_subtree`

Implementation notes:
- build on `inspect_object` and `inspect_properties`
- add array/reference summarization
- emit warnings for null/broken refs

Success criteria:
- can explain what a live plugin object is wired to
- can summarize nested plugin state without giant dumps

## Phase 3: Manifest Runtime

Deliver:
- `get_plugin_manifest`
- `refresh_plugin_manifest`
- MCP resources for manifests

Implementation notes:
- cache generated manifests in Python
- optionally persist to disk
- merge runtime and curated overlays

Success criteria:
- LLM can load plugin manifest as a resource
- manifest contains enough structure to support planning without deep rediscovery every turn

## tyFlow First-Class Target

tyFlow should be the first real plugin target because:
- it is high-value
- it is procedural
- it is likely to benefit from runtime manifests
- it is too broad for wrapper-per-parameter design

### tyFlow plan

#### Step 1
- discover classes related to tyFlow
- identify entry classes and helper classes

#### Step 2
- inspect live tyFlow instances and summarize graph structure

#### Step 3
- build a `tyFlow` manifest with:
  - classes
  - common property groups
  - known event/operator patterns
  - safe starting recipes

#### Step 4
- expose only a small set of curated helpers when introspection alone is not enough

## Heuristics for “Natural” Learning

The LLM should be able to build understanding in this order:

1. `get_bridge_status`
2. `get_session_context`
3. `discover_plugin_surface("tyFlow")`
4. `inspect_plugin_class("tyFlow")`
5. `get_plugin_manifest("tyFlow")`
6. `inspect_plugin_instance(...)` if a live object exists

This lets the LLM learn the plugin surface instead of depending on prebuilt wrappers alone.

## Verification Model

For plugin workflows, verification should include:
- before summary
- action result
- after summary
- null reference warnings
- property/value diffs when possible
- graph/relationship summary for nested systems

Important:
- `get_scene_delta` is useful but not sufficient for internal plugin parameter edits
- verification should compare plugin-specific readback where possible

## Failure Modes

The system must expect:
- UI-only properties
- arrays not writable from MAXScript
- properties exposed but not stable
- helper classes with no obvious constructor
- creation paths that technically work but produce useless setups
- host/plugin version drift

The response format should always allow:
- `warnings`
- `unsupported`
- `inferred`
- `notReflectable`

Do not fake completeness.

## Suggested File Layout

### New tools
- `src/tools/plugin_discovery.py`
- `src/tools/plugin_inspect.py`
- `src/tools/plugin_manifests.py`
- later: `src/tools/plugin_workflows.py` if plugin workflows outgrow `workflows.py`

### Manifest overlays
- `src/plugin_manifests/*.json`

### Optional docs/resources
- `docs/plugin_manifest_schema.md`
- `docs/tyflow_notes.md`

## JSON Shapes

Keep shapes stable and compact.

### Discovery result

```json
{
  "plugin": "tyFlow",
  "installed": true,
  "entryClasses": ["tyFlow"],
  "relatedClasses": ["tyFlow", "tyMesher"],
  "sceneInstances": {"tyFlow": 1},
  "warnings": []
}
```

### Manifest result

```json
{
  "plugin": "tyFlow",
  "installed": true,
  "manifestVersion": 1,
  "entryClasses": ["tyFlow"],
  "classes": {
    "tyFlow": {
      "creatable": true,
      "keyProperties": ["...", "..."]
    }
  },
  "recipes": [
    {"name": "basic_scatter"}
  ],
  "warnings": []
}
```

## Contributor Rules

- Add generic introspection before plugin-specific recipes.
- Add curated helpers only when introspection + raw handlers aren't enough.
- Keep manifests separate from orchestration.
- Keep runtime-generated data and curated overlays clearly distinguishable.
- Mark inferred information explicitly.
- Do not treat one plugin’s weirdness as a universal rule.

## Recommended Next Slice

Implement in this order:

1. `discover_plugin_surface`
2. `inspect_plugin_class`
3. manifest schema and `get_plugin_manifest`
4. tyFlow manifest overlay
5. `inspect_plugin_instance`

That is the smallest slice that proves the architecture without overcommitting.
