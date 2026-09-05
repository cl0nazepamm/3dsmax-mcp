# Curves and persistent construction

Use `curve_model` when a shape is easiest to describe as a profile, path, or
sequence of sections. Use `draw_spline` for a quick explicit world-point outline.
Use `inspect_curve` / `edit_curve` for precise edits to an existing spline cage.

## Construction loop

1. Define meaningful numeric `parameters` and named `curves` in a `definition`.
2. `curve_model(action="preview", definition=..., parameters=...)` checks the
   recipe locally. Read dimensions, tangent breaks and sampled intersections.
3. `action="create", name=...` creates one editable spline or quad mesh. Its
   recipe is saved in the `.max` file. No extra path/profile nodes are created.
4. Frame and capture it in AGENT VIEWPORT. Inspect the silhouette and highlights.
5. `action="read", name=...` returns controls and a `model_token`. Update named
   parameters with `action="update", expected_model=token, parameters={...}`.

Length values use scene units. Plane points use local coordinates; omitted plane
means XY. `xz` maps the second coordinate to world Z; `yz` maps the first to Y
and second to Z. Custom planes accept `{origin,x_axis,normal}` with perpendicular
directions. Expressions support parameter names, arithmetic, sin/cos (radians),
sqrt, abs, min/max and pi. Arc angles and sweep twist use degrees.

## A tapered armrest

Pass this definition and parameters to `curve_model`, first with `preview`, then
with `create` and a name:

```json
{
  "parameters": {"width":4,"depth":2,"radius":0.4,"height":20,"bow":4},
  "definition": {
    "curves": {
      "section": {"kind":"rounded_rectangle","width":"width","depth":"depth","radius":"radius"},
      "rail": {"kind":"spline","plane":"xz","points":[[0,0],["bow","height/2"],[0,"height"]]}
    },
    "output": {
      "kind":"sweep","profile":"section","path":"rail","up":[0,1,0],
      "path_samples":48,"profile_samples":32,"scale":[1,0.7],"twist":0,"caps":true
    }
  }
}
```

Changing `bow` or `width` recomputes both source curves and the same output cage.
The mesh retains its material, placement and modifiers. Recipe updates preserve
connectivity; changing resolution or a parameter that changes knot/segment count
requires a separate construction. Manual cage edits block parameter updates.
Geometry undo is recognized within 16 retained parameter states; ambiguous or
older states are reported, never guessed. Instanced bases require making unique.

## Curve vocabulary

- `polyline`: `points`, optional `closed`, `fillet` radius, and outward `offset`.
  Fillet and offset operate on local XY polygons. Offsets use miter joins;
  fillets that overlap adjacent edges are rejected rather than reduced.
- `spline`: a cubic interpolating curve through `points`; `tension` is 0..1
  excluding 1. Inspect for overshoot when tracing tight corners.
- `bezier`: exactly four control points, open. For convenient tangent directions
  and lengths, use a `path` with Bézier segments instead.
- `circle`: `radius`, optional `center` and `start_angle`.
- `arc`: `radius`, `center`, `start_angle`, signed `sweep` in degrees.
- `rounded_rectangle`: centered `width`, `depth`, `radius`. Radius must be
  positive and strictly less than half the smaller dimension.
- `path`: `start`, ordered `segments`, optional `closed`. Each segment has a
  `kind`, endpoint `to`, and optional descriptive `label`.

Path segments:

- `line`: endpoint only.
- `arc`: endpoint, `radius`, optional `clockwise`; chooses the minor arc.
- `tangent_arc`: endpoint and initial `tangent`, or inherits the previous
  segment's outgoing tangent.
- `bezier`: endpoint, `start_tangent` (or inherited), `end_tangent`, optional
  positive `start_length` and `end_length`. Tangents point in the direction of
  travel; lengths are handle lengths, not distances along the finished curve.

Circular arcs use cubic approximations suitable for Max splines, not exact CAD
circles. `tolerance` in the definition controls polyline sampling and QA (default
0.01 scene units), not exact arc approximation error.

## Outputs and correspondence

- `{kind:"curve",curve:"outline"}` produces an editable SplineShape. Add
  Extrude/Lathe/Surface/etc. through the usual modifier tools if needed.
- A sweep uses `path` and `profile` names. Profile points are CCW world XY at
  z=0; profile x/y become section offsets. `up` defines initial section Y and
  must not parallel the path tangent. Frames follow the path with controlled
  `twist` and linear `scale:[start,end]`. Closed paths need `caps:false`, equal
  end scales and whole-turn twist. The output is a quad cage with saved source
  curves, not a live Max Sweep modifier.
- A loft uses `sections:["lower","middle","upper"]`. Curves can have
  different knot counts; `profile_samples` gives them matching sampled counts.
  Keep winding consistent. `align:"start"` preserves authored starting points;
  `align:"auto"` chooses nearest cyclic correspondence at creation and locks
  those seams through subsequent parameter changes. Semantic feature matching
  still requires thoughtfully placed section starts and curves.

Surface intersections and thickness are not certified. Tight sweeps can fold
over themselves; visually inspect them and run `geometry_qa` on the output.
Sampled curve QA reports crossings, planarity and tangent breaks, not exact
curve intersection proofs. A corner can be intentional.

## Inspect, target, edit

`inspect_curve(name=..., spline=1, capture=true)` returns world positions,
incoming/outgoing handles, a `curve_token`, and an AGENT VIEWPORT image with
K/I/O labels. Narrow `knot_ids` for legible captures. Use `action="pick"`, image
`x/y`, and the capture's `expected_view` to rank nearby knots/handles. Check
ambiguity: overlapping front/back projections do not establish visibility.

`edit_curve(expected_curve=token, name=..., edits=[...])` preflights every edit
and applies it in one undo step. A position move carries existing handles;
explicit handles are world positions. Use `type:"bezierCorner"` when taking
manual control of a smooth knot's handles. Selection changes don't invalidate
the token; geometry/topology/transform changes do.

Use one `insert`, `delete`, `reverse`, `open`, or `close` operation per call.
Then inspect again: knot IDs can change. Batched `set` edits may target many
distinct knots, each once, without converting or collapsing the modifier stack.
