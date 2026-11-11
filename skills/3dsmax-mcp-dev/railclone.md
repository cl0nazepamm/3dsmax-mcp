# RailClone modeling

Use RailClone's graph to assemble the requested model. Prefer shared base curves,
reusable segments, and generator rules over creating repeated scene objects.
The recipes below were verified in 3ds Max 2027 with RailClone Pro 7.3.5.
They are working examples, not a complete XML schema or universal architectural rules.

## Tools and edit loop

Use [SKILL.md](SKILL.md) for bridge routing, general scene operations,
transactions, and Agent Viewport controls. Call the tools below inside a
`run_python` script as `mx.tools.<name>(...)`, or directly where your tool list
shows them.

1. Inspect the target and relevant scene objects. Resolve names/handles again after
   scene changes; do not reuse identifiers from another scene.
2. Read `get_railclone_style(name=... or handle=...)` for complete XML and
   `style_token`.
3. Build or edit the XML, preserving unrelated graph nodes, connections, and fields.
4. Apply `set_railclone_style(xml=..., expected_style=style_token, ...)`.
5. Inspect `get_railclone_output(...)` and capture the Agent Viewport. Check actual
   coverage, alignment, materials, corners, and bounds before cleanup.

The native methods behind these tools are `railclone.getXMLStyle()`,
`railclone.setXMLStyle xml`, and `railclone.getXMLOutput()`.

- Prefer the MCP setter: it provides guarded replacement and an undo step.
  Direct native `setXMLStyle` is not itself undoable.
- `Ok` means XML acceptance; it does not establish valid connections or geometry.
  Unknown classes or wrong IDs can leave an accepted graph with missing output.
- `STALE_STYLE`: reread and reconcile the current graph. `USER_BUSY`: wait for the
  user's operation to finish; do not bypass the hold with MAXScript.
- Shared bases and linked master/slave styles require deliberate handling; the
  setter refuses them. Do not silently make user instances unique.
- Output items expose native `source_segment`, `tm`, `box`, `tags`, and other
  attributes as strings. Preserve their conventions. Pagination reevaluates the
  scene on each call; it is not a frozen snapshot.
- XML contains graph data, not a portable geometry/material package. Save the
  `.max` scene with its embedded sources as well as XML when delivering an asset.

## Design a building from one footprint

For a repeated multi-storey facade, use **A2S**, not a separate spline per floor.
One closed footprint can feed multiple generators in the same RailClone object:

| Component | Graph pattern |
| --- | --- |
| Facade floors | Footprint into A2S X Spline; numeric Y Size defines total height |
| Upper setback | Another A2S using the same footprint, with Y and Z offsets |
| Floor slabs and roof finish | Flat A2S clipped to the footprint, with Extend X/Y Size to Area |
| Slab overhang, podium, inset roof | Offset operator derives another outline inside the graph |
| Parapets and terrace guards | L1S follows the footprint with lateral and vertical offsets |
| Entrance or another selected-side feature | Material ID on a footprint edge limits an L1S generator; one Evenly item places a module |

Orient vertical A2S source bays in the **XY plane**, with height along local Y.
Use generator **X Rotation = 90 degrees** to stand the array upright. An existing
Z-up bay can be reoriented geometrically with `[x,y,z] -> [x,z,-y]`; inspect its
pivot, object offsets, and resulting depth alignment after doing so. Do not apply
this rotation again to a source already oriented for A2S.

The verified rectangular example used a 2400 x 1600 footprint, a 400-high lobby
at Z=20, five 340-high floors at Z=420, and two 340-high floors at Z=2120 with
Y Offset=200. These are scene units, not assumed meters. Positive generator
Y Offset inset this counterclockwise footprint; verify the sign on another winding.

Use graph values for height, spacing, setbacks, and overhangs. Do not reproduce
these controls as stacks of scene splines or independent floor boxes. A feature
anchored to one edge is not automatically centered within every possible footprint;
choose an anchoring rule appropriate to the requested shape.

## XML vocabulary verified in 7.3.5

Keep `version` and `masterScale` from live readback. The example scene used
`version="400"`; do not copy its masterScale into a differently scaled scene.

| Node | `baseClass` | `class` |
| --- | --- | --- |
| Base spline | `Object` | `Spline` |
| Source segment | `Object` | `Segment` |
| Linear generator | `Generator` | `Linear1S` |
| 2D generator | `Generator` | `Array2S` |
| Derived spline offset | `Operator` | `Offset` |

Give every node a unique `id`, including across segment and generator names.
Set `on="1"` and the `_paronoff` parameter to `1` for enabled nodes. Use readable
names and distinct editor `x`/`y` positions so the graph is usable by a person.

Connections belong to the **receiving** node:

```xml
<connector id="spline_x" target_id="footprint"
           target_outid="0" target_outsubid="0"/>
<connector id="default" target_id="officeBay"
           target_outid="0" target_outsubid="0"/>
<parameter id="size_y" value="1700"/>
<parameter id="rotation_x" value="90"/>
<parameter id="offset_z" value="420"/>
```

Useful receiver inputs and parameters:

- A2S: `spline_x`, `spline_y`, `default`, `clipping_area`; `size_y`, `rotation_x`,
  `offset_y`, `offset_z`.
- Flat clipped A2S: connect `clipping_area`, set `clipping_getsize=1`,
  `clipping_autoalign=1`, `clipping_axis=2`, `clipping_mode=0` for the verified
  XY include-area recipe. No X/Y spline is required. `clipping_expand=10` enlarges
  the array coverage by a percentage before clipping; it does **not** enlarge the
  footprint or create an overhang.
- Offset operator: input connector `node`; `parallels=1`, `distance=0`,
  `offset=<distance>`, `keep_original=0`, `mode=0`. In the tested counterclockwise
  rectangle, positive offset expanded the outline and negative offset inset it.
  Feed its output to the slab generator's `clipping_area`.
- L1S: inputs `spline`, `default`, `dist` (Evenly); `offset_y`, `offset_z`.
  The selected-side module recipe used `evmode=1`, `evcount=1`, `limit_matid=1`,
  `cond_matid=0`, `matid=2`, and the module connected to `dist`.
- A footprint edge can be marked using native
  `setMaterialID footprint splineIndex segmentIndex materialID`, then `updateShape`.
  All three indices/IDs in that call are explicit; do not retag unrelated edges.

These enum values are observed recipes. For other modes, inspect a known working
native style and consult the official reference rather than extrapolating numbers.

## Source geometry and binding

Creating Segment and Spline XML nodes does not assign their scene geometry.
Use existing scene/modeling/material tools to author sources. Use MAXScript only
for bindings or custom assembly not covered by a dedicated tool.

For a newly authored graph, the observed base-object binding arrays are:

- Spline: `baid` (matching XML node ID), `batype` (`0` for the tested spline),
  `baname`, `banode`, `bafull`, `bastart`, `balength`.
- Segment: `sid` (matching XML node ID), `sname`, `sobjnode` (scene node),
  `sobjref` (its base object), `sobjmtl`.
- Segment transforms: `spos=[0,0,0]`, `srot=[0,0,0]`, `ssca=[100,100,100]`
  for the untransformed recipe. These are parallel arrays indexed per segment.

Inspect existing IDs and slots before extending these arrays. Preserve all other
segment settings and source bindings. After binding or changing source geometry,
call `n.railclone.segmentsUpdate firstIndex lastIndex`, then refresh the style token
and reapply through the MCP setter if a graph rebuild is needed. Verify output.

**`sinstance` is Force Instance.** It is not a harmless performance preference.
For geometry that must be clipped or deformed, use `sinstance=false` and
`sslice=true`. In the verified slab example, Force Instance prevented boundary
tiles from being sliced and left gaps even though Slice was enabled. Turning Force
Instance off restored the exact clipped slab boundary. Inspect source flags before
changing the generator or shrinking tiles to work around missing boundary pieces.

Keep source face Material IDs consistent with the RailClone material palette.
When assembling custom multi-part sources, preserve face material IDs and normals.
For hard-edged box assemblies, avoid smoothing all faces into one smoothing group.
World-space mesh snapshots and local source geometry require deliberate coordinate
handling; do not apply the node transform twice.

To construct a RailClone node through MAXScript, the verified form was:

```maxscript
local n = RailClone_Pro()
n.name = "Building_RailClone"
```

Passing `name:` into this plugin's constructor can address its segment-name array.
For a handle returned by these XML tools, use `getAnimByHandle` if raw MAXScript
targeting is necessary; it is not a `maxOps.getNodeByHandle` node handle.

## Embed and clean up

RailClone retains an internal reference to assigned segment geometry. Once output
is verified, source **mesh nodes** can be deleted; their segments become embedded
and can later be extracted through the Style Editor. Groups cannot be embedded
this way. Keep the driving footprint node in the scene.

For a compact self-contained building, aim for one RailClone node plus one
footprint when the requested design permits it. Embed reusable geometry rather
than merely hiding dozens of source objects. Do not achieve a small object count
by baking the entire building into a static segment and presenting it as procedural.

Before bulk cleanup, inspect ownership and save a backup. Delete only authorized
superseded objects and verified embedded source nodes. Remove empty owned layers.
After deletion, verify the scene object count, remaining base references, generated
output, and a fresh viewport capture. Save the editable scene to the task's output
location. Do not reset the user's scene or render unless asked.

## Official references

Read the relevant page when authoring an unfamiliar rule:

- [A2S facade orientation and numeric height](https://docs.itoosoft.com/railclone/getting-started-with-railclone/9-create-your-first-2d-array)
- [A2S generator reference](https://docs.itoosoft.com/railclone/style-editor/2d-arrays-generator-a2s)
- [Clipping and automatic area sizing](https://docs.itoosoft.com/railclone/next-steps-with-railclone/6-how-to-use-clipping-splines)
- [Offset operator](https://docs.itoosoft.com/railclone/style-editor/operators/offset)
- [L1S placement and material-ID limits](https://docs.itoosoft.com/railclone/style-editor/1d-arrays-generator-l1s)
- [Segments, embedding, and extraction](https://docs.itoosoft.com/railclone/style-editor/segments)
