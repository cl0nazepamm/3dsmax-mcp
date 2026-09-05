# Astra modeling development

## Authoring contract

### Curve construction

`curve_model` adds an arithmetic-only construction recipe with named curves and
one output: editable spline, swept quad cage or resampled loft. The pure Python
compiler lives in `helpers/curves.py`; no external geometry runtime is required.
Local frames, cubic arc conversion, interpolating curves, planar polygon fillets
and offsets, parallel-transport sweep frames, taper/twist and fixed loft seam
correspondence are explicit. Unknown fields, unsupported operations and invalid
geometry fail rather than silently changing construction intent.

Recipes use their own versioned AppData slot. Updates check both the stored
recipe and base geometry inside the same hold as the writes, retain component
connectivity and reject instanced/manual-edited bases. Recipe snapshots recognize
up to 16 geometry undo states. Raw AppData is restored explicitly on rollback.
Source curves are retained within the recipe; mesh output does not introduce
helper nodes or a scene-wide dependency graph.

`inspect_curve` and `edit_curve` use base shape data and explicit object transforms.
Curve tokens cover base knots, handles, segment types, closure, identity and
transform. Image targeting uses existing owned-view projection/capture handlers;
labels appear only in the saved image. Atomic edits preflight every ID and only
permit one topology operation per request. Re-inspection is required afterward.

Live Max 2027 acceptance on 2026-09-05 used a fresh progressive MCP process.
Spline APIs require the scene node, including beneath Extrude; passing its bare
`baseobject` fails. Reads/writes use `coordsys local` for exact cage coordinates,
then explicitly apply/invert `objecttransform` for world targeting. Refresh with
`updateShape`; generic `update` attempts a mesh operation and rejects splines.

The owned probe verified creation, nonuniform scale/rotation/object offsets,
parameter updates beneath Extrude, world knot/handle translation, stale tokens,
manual-edit rejection, insertion/reversal/opening and undo reconciliation.
Save/delete/merge retained parameters and the modifier stack, and another update
succeeded afterward. A labeled agent capture and projected knot pick agreed.
Cleanup preserved original objects, selection, working viewport and scene filename.
Evidence, the isolated saved curve and targeting image are ignored under
`local/curve-validation/`. Earlier chair/hand demos exercised swept and lofted
mesh outputs; this spline regression check does not certify surface thickness or
self-intersections.

All 486 Python tests passed, including progressive discovery and spline API
contracts. Tool catalog, native diagnostic registry and smoke metadata were
regenerated. No native rebuild is needed; existing MCP processes must reload to
use the corrected Python code. The live validation used a separate fresh process.

### Existing modeling operations

Full/core and progressive profiles expose `create_mesh`, `inspect_mesh`,
`mesh_edit`, `pick_component`, `loft_mesh`, and `geometry_qa`. Python changes
require a fresh external MCP process. Native changes require the user to reload
Max; a successful build is not installed-runtime validation.

- Mesh inspection and edits address the Editable Poly base, preserving modifiers.
  World positions include object offsets. Tokens cover cage geometry, topology,
  and transform; selection changes alone do not invalidate IDs. Edit batches
  preflight inputs, own one undo hold, and cancel on failure. Instances share
  their base geometry. Surface-operation amounts use local units.
- Visual targeting projects complete edges and polygons into the owned viewport.
  Within screen tolerance, it favors proximity to the evaluated surface hit.
  It returns cage IDs, mesh/view tokens, candidate ambiguity, and truncation.
  Surface correspondence is an inference, not an occlusion certificate. Modifiers
  can move the evaluated surface away from its cage.
- Loft construction joins matched sections with quads and optional n-gon caps.
  Arithmetic expressions reference named numeric parameters; no arbitrary code
  is evaluated. A versioned definition lives on the node in AppData. Updates
  retain vertex IDs, placement, material and stack, and reject manual cage edits
  or instanced bases. Sixteen geometry fingerprints reconcile parameter states
  after undo without assuming AppData itself is undoable. Ambiguous or older
  unmatched states fail explicitly. Caps require sensible simple planar profiles;
  there is no automatic resampling or assembly-wide dependency graph.
- Geometry QA snapshots evaluated world-space triangles, then frees the snapshot.
  It checks boundaries, non-manifold edges, adjacent winding, degenerate/duplicate
  triangles, connected components and isolated vertices. Counts cover the entire
  bounded snapshot; sample IDs are not editable cage IDs. It does not check
  intersections, thickness, vertex manifoldness, UVs, or outward/custom normals.

The viewport lifecycle and remaining isolation limits are documented in
[AGENT-VIEWPORT.md](AGENT-VIEWPORT.md).

Native mutating requests now reject an existing undo hold with retryable
`USER_BUSY` before any write. Read actions on mixed-purpose routes remain
available. The September 5 restarted-Max check verified rejection, reads during
the hold, and resumed writes afterward. MAXScript fallbacks retain their own
per-tool transaction rules.
Preview exemptions match the exact flags honored by each destination handler;
an unrelated `preview` or `dry_run` field cannot bypass the transaction guard.
The pure native policy has 68 executable cases. Release builds passed for Max 2023 through 2027;
only Max 2027 was exercised live.

## Correctness fixes from actual modeling

- Modifier lookup uses the complete `ClassDirectory`, scoped to OSM/WSM, and
  verifies descriptor and created-instance identity. The old unscoped lookup
  selected the Surface position controller (superclass `0x900B`) and cast it to
  Modifier, causing the MassFX `px_modifierClothing.ms:7` access violation.
  The actual Surface modifier is OSM `0x810`, class ID `[321410692,1573287214]`.
- Legacy PB1 modifier properties use the typed MAXScript wrapper API from C++
  with literal values. Surface/Normal parameters are no longer silently ignored;
  invalid creation parameters fail, unused modifiers are freed, and error paths
  restore redraw. Inspection reports actual legacy values.
- Spline edits inspect the base class before conversion, preserving a live
  CrossSection/Surface/Normal/Shell stack. Explicit conversion is required for a
  noneditable base beneath modifiers. Knot coordinates and handles are world-space.
- Fresh Boolean modifiers evaluate their base before adding translated cutters;
  scratch cutters retain material and configurable cylinder tessellation.
- Clone arrays preserve hierarchy offsets and return canonical animation handles.
  Native bounds use `GetObjTMAfterWSM`; newly created meshes store centered local
  vertices. Explicit empty selection clears it. Default mesh selection display
  emits a validated integer subobject level instead of a failing case expression.
- `snapshotAsMesh` already returns evaluated world geometry. Applying the object
  transform again doubled translations in the first QA draft; live comparison
  against a transformed cage caught this and the tool/usage guide were corrected.
- Existing-material assignment accepts a source node name/handle, preflights all
  targets and shares the original material in one undo step. This Python branch
  supports the currently installed GUP; direct native probes retain creation-only
  schemas. Live checks verified material identity, invalid-target rejection and
  retryable `USER_BUSY` without changing the original assignment.
- `edit_vertices` now passes the Editable Poly base to every polyop operation,
  explicitly in world coordinates. Checking the base but passing the evaluated
  node failed beneath TurboSmooth. Live get/set/move retained the 256-vertex cage
  and subdivision; targeted unit coverage exercises all four action routes.

## Live evidence and release boundary

The restarted Max verified Surface threshold `0.01 -> 0.015 -> 0.01` through the
dedicated setter and property readback. The source-driven modeling checks also
verified:

- Image-to-edge pick, exact 0.2 cm cage movement, stale-image rejection, geometry
  restoration, retained TurboSmooth, and unchanged user viewport/selection.
- Loft updates under rotation/nonuniform scale and TurboSmooth; undo/read/update;
  manual-cage rejection; save/delete/merge/read/update with retained parameters.
- QA on the lounge shell, both cushions and a frame rail, plus transformed open
  geometry with independently checked bounds, area and boundary count.
- A 336-vertex plywood loft with saved width, height, curvature and thickness;
  changing width/curvature retained subdivision and passed the topology checks.

Evidence and shaded captures are local, ignored development artifacts under
`local/surface-development/`: `component-pick.json`, `geometry-qa.json`,
`loft-validation.json`, `Astra_Parametric_Shell.max`, and `parametric-shell.png`.
Earlier chair/pavilion studies remain under `local/chair-development/` and
`local/astra-archviz/`. These are modeling studies, not final archviz acceptance.

Focused unit/contract tests remain tracked. Obsolete port-parity and one-off
smoke scripts remain local and ignored; `scripts/run_live_tool_smoke.py` is the
reusable native diagnostic entry point. Standalone chat and the parked builder
have been removed; installer migration removes only verified bridge-owned chat
macros and preserves credentials/user replacements.

The live SDK harness verified minimized start, shaded restore, capture refusal,
projection, exact camera restoration while minimized, same-slot release/reopen,
stale-token rejection and unchanged user viewport state. The restarted installed
GUP subsequently passed lifecycle, user-view preservation and `USER_BUSY` checks.
The dining-chair session produced a 29-part model with 72,128 evaluated triangles;
all parts had one connected component and zero reported topology issues. A visible
seam edge was moved 0.08 cm with stale-token rejection and retained subdivision.
Backrest bow changed 7.5 -> 8.5 -> 7.5 with its saved definition and stack intact;
the initial and regenerated cage fingerprints were not bitwise identical, so this
does not establish bitwise round-trip reproduction. Evidence and the saved model
are under `local/dining-chair/`. Material finish is a shaded study, not render QA.
Private node
isolation, intersection/thickness analysis, robust screen-space cuts and an
assembly-wide parameter graph remain outside this pass. The current environment
does not expose the native computer-use runtime for the proposed UI experiment.
The user completed the fresh-agent modeling demo and authorized preparation of
`1.6.6 Astra Edition`. Release packaging is separate from the live checks above;
newly rebuilt binaries require a developer-managed reload for runtime validation.
