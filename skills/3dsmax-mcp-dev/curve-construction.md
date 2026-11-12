# Curves and persistent construction

Use `curve_model` when a shape is easiest to describe as a profile, path, or
sequence of sections. Use `draw_spline` for a quick explicit world-point outline.
Use `inspect_curve` / `edit_curve` for precise edits to an existing spline cage.
Their manuals list every curve kind, output and edit; this holds what they do not say.

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
Changing resolution, or a parameter that changes the knot or segment count, needs a
separate construction. Geometry undo is recognized within 16 retained parameter
states; ambiguous or older states are reported, never guessed.

## Planes and expressions

- Plane points are local; an omitted plane means XY. `xz` maps the second coordinate
  to world Z; `yz` maps the first to Y and the second to Z.
- Expressions take parameter names, arithmetic, sin/cos (radians), sqrt, abs, min/max
  and pi. Arc angles and sweep twist are in degrees.

## Curve details

- `polyline`: fillet and offset work on local XY polygons, and a fillet that overlaps
  adjacent edges is rejected rather than reduced.
- `spline`: `tension` is 0..1 excluding 1. Inspect for overshoot when tracing tight corners.
- `bezier`: for convenient tangent directions and lengths, use a `path` with Bézier
  segments instead.
- `rounded_rectangle`: the radius must be positive and strictly less than half the
  smaller dimension.
- A path `arc` segment chooses the minor arc. Bézier segment lengths are handle
  lengths, not distances along the finished curve.
- Circular arcs use cubic approximations suitable for Max splines, not exact CAD
  circles. `tolerance` controls polyline sampling and QA (default 0.01 scene units),
  not the arc approximation error.

## Outputs

- Sweep: profile x/y become section offsets, and `scale:[start,end]` is linear along
  the path.
- Loft: sections can have different knot counts; `profile_samples` gives them matching
  sampled counts. `align:"start"` keeps the authored starting points. Semantic feature
  matching still needs thoughtfully placed section starts and curves.
- Tight sweeps can fold over themselves: inspect them visually and run `geometry_qa`
  on the output. A reported corner can be intentional.
