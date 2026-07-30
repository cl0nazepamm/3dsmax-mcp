# Builder Mode — Spec-Gated Asset Construction

Reconstruct an object from a reference image (or description) as a parametric
Max assembly, one gated pass at a time. `builder_gate` measures the real scene
against your spec deterministically; your vision judges the capture grid; a
recorded verdict is the only way forward. You cannot skip passes and you cannot
record `continue` while violations remain — do not try to work around the gate
with raw MAXScript; the discipline is the point.

**Pass order:** `spec → blockout → form → material → detail → finish → complete`

**Tools:** `builder_session` (start | spec | status | abandon),
`builder_gate` (check | record). Everything else is the normal toolset:
`create_object`, modifier tools, material tools, `set_parent` — plus the
shaping trio: `boolean_operation` (cuts/insets/fusions), `draw_spline`
(profiles and paths), `edit_vertices` (silhouette fitting). Primitives plus
Bend/Taper alone produce primitive-looking output; the shaping trio is how a
form pass earns its capture.

## Loop shape (every pass)

1. Do the pass's work with normal tools. Parent every node under the root
   (`BLD_<name>`). Keep modifier stacks live — collapse only at finish, and
   only if the budget demands it.
2. `builder_gate action=check` — fix every violation it reports. Metrics show
   the measured numbers; violations name the component and the delta.
3. When clean, check fires `capture_multi_view` and returns the grid path plus
   the reference path. **Read both images** and judge per the pass rubric below.
4. `builder_gate action=record` with exactly one verdict:
   - `continue` — pass done (gate re-checks; refuses if anything regressed)
   - `refine-scene` — spec is right, scene doesn't match it yet
   - `refine-spec` — the spec itself is wrong (then `builder_session action=spec`)
   - `request-input` — stuck; ask the user. **Mandatory after 3 failed checks
     on one pass** — do not keep burning attempts.

`evidence` is required: what you compared and what you saw. When refining, pass
`changes` with exact deltas (`"blade.height 40.0 -> 30.0"`). Never claim done
when only improved — name what still doesn't match and why (single views hide
sides; 2D grids miss depth errors).

**Honest evidence, not self-absolving evidence.** `continue` is refused if the
evidence hedges — "stylized", "proxy", "placeholder", "chunky", "acceptable
for", "good enough", "for now". If you find yourself reaching for one of those
words, you have written the case for `refine-scene`: record that instead and go
fix the thing. Describing a mismatch and then continuing past it is the one
failure this harness exists to prevent. Honest *remaining* limits belong in the
final finish evidence (things a single reference view cannot resolve), not as
cover for work you chose to skip.

## Intake: detail-first, before any spec

Study the reference and enumerate before you author:

- **Identity**: what object, what family? What single feature would make
  someone say "that's *that* one"?
- **Macro → meso → micro**: major masses and their proportions; secondary
  forms (guards, grips, mounts); identity details (bevels, fasteners, grooves,
  seams, wear zones, gloss breaks). List every identity detail — each becomes
  a spec `details` entry mapped to a real component.
- **Materials in PBR terms** per zone: base color, roughness, metalness.
- **What the view hides**: note hidden sides; state your assumption for them.

Shallow specs are rejected by validation (component/detail floors scale with
declared `complexity`: simple 3/0, moderate 6/6, complex 10/12).

## Spec reference

```json
{
  "complexity": "moderate",
  "reference": "C:/refs/knife.png",
  "tolerance_pct": 8,
  "components": [
    {"name": "blade", "dims": [3, 0.5, 30], "material": "steel",
     "symmetry": "x", "ratios": {"handle": 3.0}},
    {"name": "guard", "dims": [5, 1.5, 1], "material": "steel",
     "touches": ["blade", "handle"]},
    {"name": "handle", "dims": [3, 3, 10], "material": "rubber", "ground": true}
  ],
  "materials": [
    {"name": "steel", "class": "PhysicalMaterial", "params": {"roughness": 0.25}}
  ],
  "details": [{"id": "fuller", "on": "blade", "via": "modifier"}],
  "budget": {"tris": 20000, "min_tris": 4000}
}
```

- `dims` are in spec units, compared **sorted** (rotation-tolerant), so block
  out in a canonical orientation and rotate only at finish if needed.
- `ratios` compare longest bbox dims. At moderate+ every geometry component
  needs at least one relational constraint
  (`ratios` / `symmetry` / `mirror_of` / `ground` / `touches`).
- `floating: true` opts a component out of the must-touch-something check.
- `primitive: true` declares a component intentionally a bare primitive
  (a simple pin, a flat spacer) — the only way past the form-pass shaping
  gate without shaping work. Declaring it on identity-carrying masses is
  self-absolution with extra steps; the vision rubric still judges them.
- `kind`: `geometry` (default) | `helper` | `shape`. A spline with mesh output
  (Extrude/Lathe/Sweep/Bevel_Profile on it) satisfies `geometry`; a bare
  profile spline does not — declare construction curves as `kind: shape`.
- detail `via`: `modifier` | `editpoly` | `map` | `geometry` | `projection` |
  `boolean` (a Boolean operand or modifier named for the id on the component) |
  `spline` (a spline shape named for the id under the root).
- Names: letters, digits, space, `_`, `-` only. Node names must match
  component names exactly (case-insensitive).
- Sending only some sections (`components`, `materials`, `details`, `budget`)
  patches those sections; the rest is kept.
- `class` is the material's **own** class name: `PhysicalMaterial`, not
  `Physical` — `Physical` is the Physical *Camera*, and passing it used to
  build a camera and hand it to the material pass (see docs/CRASH_LOG.md).
  The gate now compares the class you declare against what is actually
  assigned, so a wrong token surfaces at the material pass either way.
- `params` keys must be real property names of that material class — check
  with `get_material_slots` or `inspect_properties` before declaring them.
  Material "exists" = assigned to a node under the root; direct assignment
  only (Multi/Sub containers are outside the v1 contract).
- `budget.min_tris` (optional, below `tris`) is an **underbuild floor** checked
  at finish. Set it when the object's detail level implies real geometry — it
  is the cheapest defense against a spec whose details all got built as boxes.
  Estimate honestly from the inventory, don't reverse-engineer it from what you
  happened to build.

## Detail anchors — name your work

A detail `id` resolves iff something is **named after it**: a node under root,
a modifier on its component (`renameModifier` / set `.name` when adding), a
**Boolean operand** on its component (operands keep their node name when
consumed — name the cutter after the detail id *before* `boolean_operation`),
or a top-level texture map in the component's material. Anchor as you build —
unnamed work does not count, and unspecced geometry that matches no component
or detail id hard-fails at finish.

## Pass rubrics

**blockout** — primitives only, correct dims/placement/pivots, parented under
root. Gates: coverage, proportion, relation. Vision: silhouette and
proportions vs reference from every grid view; part placement.

**form** — shape each mass with the strongest tool for the silhouette, not the
cheapest one (see the shaping toolkit below): spline profiles for curved
masses, `boolean_operation` for holes/insets/steps, `edit_vertices` conform
for silhouette fixes, Bend/Taper/FFD/Chamfer/TurboSmooth where a uniform
deformation truly is the shape. Gates add: degenerate (collapsed dims, baked
node scale — model at real size; if you scaled a node, reset xform and
re-check) and **shaping** — a geometry component whose base object is still a
raw primitive with no shaping modifiers and no Boolean operands hard-fails;
the gate does not advance box-and-cylinder blockouts. Parts that truly are
bare primitives must say so in the spec (`primitive: true`). Vision: does each
form read as the reference's form, not a primitive? Curves where curves
belong, hard edges where hard? Metrics report how each component cleared the
gate (`shaped`: base | boolean | modifier | declared-primitive).

**material** — build and assign per spec zone. Gates add: assignment matches
spec, class matches, declared params within tolerance. Vision: value/roughness
zones read like the reference under viewport lighting; gloss breaks in the
right places. If the viewport is too dark to judge, create review lights —
delete them before finish.

**detail** — every inventory item, anchored. Gates add: detail anchors (and
`via: projection` requires a Camera Map on the component).

Vision here is **per detail, not per grid**. `check` returns
`details_to_review`, and `record continue` is refused unless your evidence
names every id in it. One wide grid cannot resolve sixteen small features —
`select_objects` the owning component, `isolate_and_capture_selected`, zoom on
the region, and judge that detail against the reference: present? placed right?
scaled right? shaped like the reference's version of it, or a box standing in
for one? A named box that satisfies the anchor gate is not the detail — the
anchor gate proves you built *something*; only your eyes prove you built *it*.
Work through the components in order and write one clause per id.

**finish** — naming, `_builder` layer for all nodes, pivots sane, collapse
only if over budget. Gates add: tri budget (and `min_tris` floor if declared),
no unspecced geometry, layer hygiene, and **session litter**: any node that
appeared at scene root since `start` fails the gate. Everything you create —
including projection cameras and review lights — is parented under the root and
lives on `_builder`, or is deleted before finish. Vision: final grid vs
reference — state remaining mismatches honestly in the evidence; they go in the
record for the user.

## Shaping toolkit (form and detail passes)

**Boolean cuts** — holes, sockets, insets, panel steps, vents, trigger guards:
create the cutter as a scratch primitive (or extruded spline), **name it after
the detail id**, then `boolean_operation action=apply` with `subtract` /
`intersect` / `union`. Non-live operands are consumed — no litter, and the
operand name stays in the modifier as a detail anchor (`via: boolean`).
`operation_option="imprint"` scores panel lines without removing volume.
Never consume a *component* node as an operand — its name leaves the scene and
coverage fails; cutters are scratch objects, or pass `live=true`.

**Spline profiles** — any mass whose identity is a curve (bottle, blade, grip,
fender, vase): read the silhouette off the reference, `draw_spline` the
profile (smooth knots first; switch problem knots to bezier and place handles),
then Lathe (revolved), Extrude (prismatic), Bevel_Profile or Sweep (rails).
The result counts as geometry once it has mesh output. Iterate: capture,
compare the outline against the reference, `set_knots` the offenders — this
loop is cheap and is where curved forms come from.

**Vertex conform** — when a blocked mass is close but the silhouette is wrong:
`draw_spline` the target outline from the reference, then `edit_vertices
action=conform` with an `axes` mask (e.g. `"xz"` fits the side profile while
preserving width) and `strength` under 1.0 for gradual pulls. `action=move`
with `falloff` gives soft regional pushes. Verts are edited on the poly base
beneath the stack, so TurboSmooth/Shell above stay live.

## Projection recipe (patterned surfaces)

When a detail's identity is a pattern (skin, decal, label, painted wear),
project the reference's own pixels instead of imitating them procedurally —
declare `"via": "projection"` and:

1. Create a Free Camera named `<detail_id>_cam`, **parent it under the root**
   and put it on `_builder` (unparented cameras fail the litter gate at
   finish). Match it to the reference viewpoint: point it at the component,
   then loop — `capture_viewport` through the camera, compare against the
   reference, nudge position/FOV — until the component's silhouette in the
   camera view lines up with the reference. This is a vision loop; expect 3–5
   iterations, and it is the whole value of the technique.

   **Acceptance test before you wire anything:** the camera view and the
   reference must show the component at the same angle and framing, with the
   painted region occupying the same relative area. If they don't line up,
   projection will smear the pixels across the wrong surfaces — a camera left
   pointing down at the origin projects nothing but garbage. If you cannot get
   the match, do not wire the map: switch the detail's `via` to `map` or
   `geometry` and say so in the evidence.
2. Add a `Camera_Map_Per_Pixel` map named after the detail id to the
   component's material, with the matched camera and the reference bitmap.
   Blend it over the procedural PBR base (composite/mix), masked to the
   painted region.
3. The reference is a lit photo, not albedo — kill specular contribution in
   the projected zone or drop the map's output level to compensate.
4. Off-camera surfaces get no projection: fall back to procedural there and
   say so in the evidence. If you later bake the projection to a bitmap,
   switch the detail's `via` to `map`.

## Gotchas

- The census walks **descendants of the root** — a component built at scene
  root level shows as missing until parented.
- `create_object` default `pos_mode="ground"`: `pos` is bottom-center. Box:
  `width=X, length=Y, height=Z`.
- The spec ledger lives in AppData on the root node — it survives save/load;
  `builder_session action=start` on an existing session resumes it.
- Deterministic gates run before capture: a dirty check returns no image.
  That ordering is intentional — geometry first, judgment second.
- Cylinder `create_object` is Z-up; after rotating 90° onto the barrel axis,
  recenter with `n.pos += targetCenter - n.center` — end-pivoted tubes fail
  placement even when `pos_mode=center` looked correct at create time.
- Form modifiers that expand AABB (Bend/Taper/node rotate) need a
  `refine-spec` dims patch or they fail proportion at form — do not keep
  re-checking the same out-of-tol stack past attempt 2.
- A misfired tool call can leave a stray node named after what you *meant* to
  make (a Physical Camera named `polymer` from a fumbled material call). The
  litter gate catches these at finish, but check `query_scene(action=overview)`
  `rootCount` after a suspicious tripback rather than discovering it five
  passes later.
- An extruded/lathed spline satisfies geometry coverage only once it has mesh
  output (tris > 0) — a bare profile spline named as a component fails with a
  "no mesh output" hint until the Extrude/Lathe lands.
- Mesh-producing shapes under root are held to the same unspecced-geometry
  gate as real geometry at finish; zero-tris construction splines are exempt,
  but name them after a detail id or delete them anyway.
- Boolean cutters must be parented under the root *before* apply if live, or
  simply consumed if not — a consumed operand cannot litter, which makes
  non-live subtract the cleanest detail-cutting primitive in builder mode.
