# Builder Mode — Blueprint -> Build -> Prove

Builder mode reconstructs one asset as a measured 3ds Max assembly. It is **external-MCP-only**; standalone MCP Chat
does not run the Python gates. The MCP guide resource is `resource://3dsmax-mcp/builder-guide`.

Use `builder_session` for the ledger, `builder_gate` for progression, and dedicated modeling tools for scene work.
Never edit builder AppData, fake proof, skip a pass, or bypass a gate with raw MAXScript. Names locate proof; captures
prove quality.

## 1. Blueprint

Start once. This creates or resumes `BLD_<name>` on `_builder`; it never resets an existing session.

```text
builder_session(action="start", name="radio", object_desc="1970s table radio", units="cm", complexity="simple")
```

Inventory macro masses, identity details, material zones, and assumptions for hidden views before modeling. Submit the
complete spatial blueprint; this small example is valid at `simple` complexity:

```text
builder_session(
  action="spec", name="radio",
  spec={
    "components":[
      {"name":"body","dims":[24,10,12],"center":[0,0,6],"material":"enamel","symmetry":"x"},
      {"name":"grille","dims":[10,1,8],"center":[-4,-5,6],"material":"cloth","parent":"body"},
      {"name":"dial","dims":[3,2,3],"center":[7,-5.5,7],"material":"metal","touches":["body"]}
    ],
    "materials":[
      {"name":"enamel","class":"PhysicalMaterial"},
      {"name":"cloth","class":"PhysicalMaterial"},
      {"name":"metal","class":"PhysicalMaterial"}
    ],
    "details":[{"id":"speaker_holes","on":"grille","via":"boolean","count":8,
      "description":"eight aligned speaker perforations","priority":"critical"}],
    "budget":{"tris":12000}
  }
)
```

Blueprint contract:

- Every geometry component needs a unique `name`, positive `dims:[a,b,c]`, bounding-box `center:[x,y,z]` relative to
  the builder root, and a material. Node names match component names case-insensitively.
- `kind` defaults to `geometry`. `shape`/`helper` may omit dims, center, and material; supplied dims must be finite,
  non-negative, and have some extent, while a supplied center must be finite. The live node must match its declared kind.
- `dims` are compared sorted and tolerate rotation. Add ordered `axis_dims:[x,y,z]` when world-axis orientation matters.
- Spec `center` is bbox center, but `create_object` defaults to `pos_mode="ground"` (bottom-center). For an axis-aligned
  primitive use `pos=[cx,cy,cz-worldZHeight/2]`, or `pos_mode="center"` when supported. Box maps `width=X`, `length=Y`,
  `height=Z`.
- Keep the builder root at identity rotation and scale; translation is supported, while components carry the modeled
  transforms. This keeps all relative centers and world-unit measurements in one stable frame.
- `parent` may be `"root"` or a component; every node must descend from the builder root. Declare containment with
  `nested_in:"container"`; `nested:true` alone is invalid, and the live bbox must be a distinct contained mass.
  `floating:true` permits intended isolation.
- Relations are `ratios`, `symmetry`, `mirror_of`/`mirror_axis`, `ground`, `touches`, `parent`, and `nested_in`. At
  moderate/complex every geometry component needs at least one.
- Units are `generic|mm|cm|m|in|ft`. Named units convert to Max system units with `units.decodeValue`; `generic` stays
  raw. Do not manually rescale values. A supplied `reference` must be a real local image file.
- Complexity floors are simple `3 components / 0 details`, moderate `6/6`, and complex `10/12`.
- At moderate/complex every detail needs a feature-specific `id`, `on`, `via`, positive integer `count`, an observed
  target `description` of at least eight trimmed characters, and `priority:critical|important|support`. At least one
  detail must be critical; generic ids such as `detail1` are rejected.
- `primitive:true` exempts a genuinely bare primitive from form proof. At moderate/complex the cap is
  `max(1, floor(geometry_count * 0.35))`; identity masses must be shaped.
- Material `class` is the real material class (`PhysicalMaterial`, not the `Physical` camera). `budget.tris` is required;
  optional `min_tris` catches underbuild.

A valid spec locks. To repair it, first record `refine-spec` from the latest check. A dirty check's `check_id` is enough;
a clean capture's `review_id` is also accepted:

```text
builder_gate(action="record", name="radio", verdict="refine-spec", check_id="c1-2-a1b2c3d4",
             evidence="Measured body target is one centimeter wider than specified.")
```

Then update the smallest surface. Whole top-level sections replace that section. `spec.patch` shallow-upserts
components/materials by `name` and details by `id`; `remove` deletes those keys:

```text
builder_session(action="spec", name="radio", spec={
  "patch":{"components":[{"name":"body","dims":[25,10,12]}],
           "details":[{"id":"speaker_holes","count":10}]},
  "remove":{"details":["old_badge"]}
})
```

Component/reference/complexity/tolerance/units edits can reopen blockout; material, detail, and budget edits reopen
their owning pass when needed. Never change a locked spec to excuse a scene mismatch.

## 2. Build

Build exactly five passes. Parent and name work as it is created; keep mutations sequential.

| Pass | Required work | New proof |
|---|---|---|
| `blockout` | Declared masses at measured centers/dimensions; raw primitive seeds only | silhouette, proportions, placement, relations |
| `form` | Shape every non-exempt mass | change relative to the accepted blockout baseline |
| `material` | Create and directly assign declared classes/zones/params | assignment and material read |
| `detail` | Build every inventory item on its owner through its declared route | anchors plus visual review of every id |
| `finish` | Clean hierarchy, names, `_builder` layer, and budget | no unspecced nodes or new scene-root litter |

Accepting blockout records each raw primitive's evaluated surface area/volume, enabled modifier classes, and Boolean
operands. Form proof is a new Boolean, a new enabled shaping modifier, or a greater-than-0.5% evaluated area/volume
change relative to that baseline. Class/topology conversion alone and disabled modifiers are not proof.
Declared components stay visible throughout proof, and declared geometry stays renderable.

Prefer `boolean_operation` for cuts/insets, `draw_spline` plus Extrude/Lathe/Sweep for identity profiles, and
`edit_vertices` for silhouette fitting. Form and later passes also reject node-scale leftovers (`[1,1,1]` required).

Detail ids match whole alphanumeric tokens: `speaker_holes` matches `speaker_holes_01`, not `speaker_holesish`. `count`
must equal the exact number of route matches. Each route searches only the declared `on` component's ownership scope:

| `via` | Accepted anchor on/under the owner |
|---|---|
| `modifier` | enabled modifier named with the id token |
| `editpoly` | enabled Edit Poly modifier named with the id token |
| `boolean` | operand on an enabled Boolean, or enabled Boolean modifier, named with the token |
| `map` | reachable map in the owner's assigned material |
| `projection` | camera-map-class map in the owner's assigned material |
| `geometry` | mesh-producing geometry descendant of the owner |
| `spline` | shape descendant of the owner |

Name cutters before non-live Boolean consumption; the operand name survives. Any retained projection camera or live
Boolean cutter is a support node: its name must contain the detail id, it must descend from the `on` owner, and support
nodes may not exceed that detail's exact `count`. Hide support nodes so cameras/cutters cannot pollute the proof grid.
Support nodes never replace route anchors. Anchors prove inventory, not likeness: a named box still needs honest review.

## 3. Prove

At each pass end, make one hard-gate call. It runs all cumulative deterministic gates in one census; do not preamble
with status, scene discovery, or a separate capture.

```text
builder_gate(action="check", name="radio")
```

Default `report="compact"` returns counts, tris, units, violations, and only dirty-component metrics. Use
`report="full"` briefly to diagnose a hard failure. Fix violations and check again.

Every check returns a snapshot-bound `check_id`. A clean default check frames the builder root in exactly four views,
requires the returned view list to match, validates a real non-empty PNG, then returns `review_id`, `capture`,
`review_targets`, and the threshold. Keep the canonical defaults:

- blockout: `front, right, top, back`
- form/detail: `front, right, left, top`
- material/finish: `front, right, back, top`

Override `views` only for a real visibility problem; custom review still requires four distinct views including front,
top, and a side. `capture=false` cannot produce review proof. Detail must list exactly every spec id with no extras, and
the evidence must name every id as a whole token:

```text
builder_gate(action="record", name="radio", verdict="continue", review_id="r1-3-a1b2c3d4", visual_score=0.88,
             reviewed=["speaker_holes"],
             evidence="speaker_holes: all eight perforations are aligned and match the reference.")
```

`visual_score` is `0..1`; `review.threshold` is restricted to `0.8..1.0` and defaults to `0.8`. Evidence is capture truth, 10–1200
characters. `continue` rejects mismatches, hedge words, a low score, hard-gate regression, or an incomplete detail
`reviewed` list. Use `refine-scene` with `review_id` for a visual miss; use `refine-spec` with `check_id` for a blueprint
miss.

`check_id` binds pass, spec revision, and scene fingerprint; `review_id` additionally binds the capture digest. Before
accepting a review-bound record, the gate makes a token-free root-framed recapture and requires the same valid PNG hash.
Any changed scene/spec/appearance, missing capture, or stale id requires a new check and visual review.

The gate reports total attempts and a no-progress defect streak. Three same/oscillating dirty results block the session.
Record `request-input`, save its returned `resume_token`, ask the user, then resume only after new input:

```text
builder_gate(action="record", name="radio", verdict="request-input",
             evidence="The same center conflict remains after three corrections; target intent is ambiguous.")
builder_gate(action="check", name="radio", resume=true, resume_token="q_returned_token")
```

Accepted intermediate, superseded, and freshness captures are cleaned automatically. Finish keeps the proof image and
persists `final_review` (review id, file, views, score) in status. Recover lost context once with
`builder_session(action="status", name="radio", include_spec=true)`. To abandon without deleting work,
`builder_session(action="abandon", name="radio")` cleans proof files, strips the ledger, and renames the root to a unique
`ABANDONED_<old-name>_...`; only `delete_nodes=true` deletes the assembly.
