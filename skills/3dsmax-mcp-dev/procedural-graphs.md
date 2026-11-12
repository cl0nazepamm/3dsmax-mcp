# Data Channel and Max Creation Graph

Read this reference completely before building, inspecting, editing, compiling, or verifying Data Channel or Max Creation Graph systems.
The tools' manuals cover their own arguments; this holds what they do not say.

## Data Channel

- Resolve operator class IDs from the live `NumberOperators`/`OperatorID` catalog; do not hardcode them across Max versions.
- In raw MAXScript, `operator_order` holds 0-based storage IDs while UI/operator indexes are 1-based positions, and deletion leaves stale entries in `operators`.
- Validate a complete reorder permutation before assigning `operator_order`; Autodesk's `ReorderStackOperator` does not bounds-check and unsafe input can destabilize Max.
- Max 2027 stores `operator_ops` as 0=unset, 1=Replace, 2=Add through 7=Cross despite older MAXScript help claiming 0..6; a fresh stack's first Input must be explicitly set to 1 or Max leaves the graph invalid and unevaluated.
- Treat Maxscript, Maxscript Process, and Expression operators as executable code; require explicit authorization and fail closed unless MCP safe mode is disabled.

## Max Creation Graph

### Graph identity and workspace

- Preserve both the root graph UUID and `graph_version` GUID during edits; regenerate both only when forking because the root UUID determines the generated class ID in Max 2027.
- `mcg_list_graphs` scope tokens are `session`, `samples`, `installed`, and `all`; `samples` is plural.
- Keep a compiled graph and its dependencies alive for the session because generated plug-ins retain the source path; Max 2027 emits `.ms` but normally no `.txt` diagnostic graph.
- A freshly compiled class may be invisible to `create_object` and `add_modifier`: instantiate it through `mcg_test_tool` or `mcg_apply_modifier`.
- Native `create_object` class lookup may reject `GeoSphere`; use the supported `Sphere` primitive or MAXScript only when a true geosphere is required.

### Safety

- Treat `EvalMAXScript`, nonblank `<customui>`, and executable compound dependencies as code-execution surfaces; block them by default and never override MCP safe mode.
- Treat live-catalog `Impure` operators as unsafe, not diagnostic warnings: disposable verification can execute scene-mutating operators such as selection, parenting, or cloning outside the probe object's cleanup boundary. Offline `OperatorMetaInfo.xml` does not declare impurity, so treat it as unknown until the live operator depot confirms.
- Embree operators (`RayTraceScene`, `RayTraceAddGeometry`, face-intersection variants) are `impure: true` and blocked under MCP safe mode even with `allow_executable`.

### Authoring

- Group nodes use `groupnode="..."` with a nested `<nodes>` comma list; nested groups are valid, but cycles and multiple direct memberships are not.
- Terminal Output connections use destination port `0`; numeric-but-nonzero terminal ports are structurally invalid even before Viper type validation.
- Validation requires every exposed `Parameter:` node to reach the terminal, so land parameters and their consumers in the same `mcg_apply_patch` transaction — a parameter staged for a later patch fails the whole patch.
- Tool parameter names with spaces become underscore PB2 names in the generated `.ms` (`ground spline` becomes `ground_spline`).
- Geometry verification quotes string parameters, so `Parameter: INode` cannot be set through `verification.parameters` name strings; compile with `verify=false`, then assign nodes in MAXScript (`obj.ground_spline = $Footprint`).
- A literal placed in a `Constant` node's XML `name` attribute parses as String and fails type-checking; agent-authored graphs should use typed constant operators instead (`FloatZero`, `FloatOne`, `One`, `Pi`, `HalfPi`, macros like `DivideByTwoFloat`).
- Higher-order operators (`MapRange`, `Combine`, `SplineFromFunction`) accept a lambda by wiring the chain-end node's function output (`source_port: 1`); the chain's unconnected inputs become the lambda's arguments in order.
- `Extrude` (`QuadMesh from Extruded Points`) treats `direction` as the per-segment offset, not total height; wire `floor_height` into direction and `floors` into `segmentCount`.
- For facade windows on extruded wall shells, use `ToTriMesh` with hidden edges, then `Mesh Extrude All Polygons` with positive inset and negated depth; assign `materialId` for glass panes on a Multi/Sub.
- Pure scene-node ray hits (`GetRayToSurfaceIntersections`, `UpdatePositionsWithIntersections` / `NodeIntersectsRay`) may compile but fail at evaluate with `-- Type error: Call needs function or class, got: undefined`; prefer `Ray` / `RayFromMatrixAxis` with `CloneMeshAlongRay` / `SampleAlongRayWithSpacing` for safe-mode ray demos.
