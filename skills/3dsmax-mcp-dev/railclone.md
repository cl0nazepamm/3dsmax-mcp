# RailClone MAXScript reference

Condensed from iToo's [MAXScript reference](https://docs.itoosoft.com/railclone/maxscript-reference).

`rc` is a RailClone scene object (`rc = $`). Object methods are on `rc.railclone`. RailClone Tools
methods are on `RailClone_Pro.global` and act on the selected RailClone objects. Properties use dot
syntax. Availability varies by version: inspect with `showProperties rc`, `showInterfaces rc` and
`showInterfaces RailClone_Pro`, and guard a read with `isProperty rc #paname`.

In MCP, read and replace the style graph with `get_railclone_style` / `set_railclone_style` and read
the generated items with `get_railclone_output`, instead of calling the XML methods directly.

## Object properties

| Property | Type | Meaning |
| --- | --- | --- |
| `Disabled` | Boolean | `true` disables the object |
| `autoUpdate` | Boolean | Automatic rebuilding; `false` is manual updating |
| `style` | String | Library Browser route of the loaded library item |
| `seed` | Integer | Random seed |
| `iconSize` | Float | Viewport icon size |
| `gscale` | Float | Global scale |
| `curvesteps` | Integer | Curve Steps override for spline deformation |
| `surfinterp` | Float | Surface interpolation distance when conforming paths to a surface |
| `simpleoffset` | Boolean | Simple Y Offset calculation |
| `nodemat` | Boolean | Use Segment Material |
| `freeobject` | Boolean | Free Object mode |
| `stylelink` | Object or undefined | Master RailClone object linked to this object's style |
| `stylelinkmat` | Boolean | Copy the material from the linked Master Style object |
| `vmesh` | Integer | Viewport display: 0 Mesh, 1 Boxes, 2 Adaptive, 3 Points Cloud, 4 Quick Mesh |
| `adaptfaces` | Integer | Face threshold for Adaptive display |
| `cloudens` | Integer | Local point-cloud density |
| `rmesh` | Integer | Render display: 0 Mesh, 1 Boxes |
| `rendermode` | Boolean | Render instancing engine |
| `maxseg` | Integer | Maximum generated Segments; 0 removes the limit |
| `maxfaces` | Float | Maximum generated faces; 0 removes the limit |
| `showinst` | Boolean | Render debug display for instanced Segments |
| `debugmatid` | Integer | Material ID used by the instance debug display |
| `savedversion` | String | RailClone version that last saved the style |
| `createdversion` | Integer | RailClone version that created the style |
| `surfedgetolerance` | Float | Surface-edge tolerance |
| `nongeomatid` | Integer | Material ID given to non-geometry objects for debugging |

## Exported parameters

Parallel arrays: one index reaches a parameter's name, type, description and value. Names and indices
depend on the style, so find the parameter on each object instead of reusing an index.

| Array | Holds |
| --- | --- |
| `paid` | Identifiers (strings) |
| `paname` | Names |
| `padesc` | Tooltip descriptions |
| `palimit` | Limit Values toggle (Boolean) |
| `pamodified` | Whether the parameter was modified (Boolean) |
| `paretain` | Retain Value Mode (Integer) |
| `patype` | Type code (Integer) |

| `patype` | Type | Value | Minimum | Maximum |
| --- | --- | --- | --- | --- |
| 0 | Integer | `paintval` | `paintmin` | `paintmax` |
| 1 | Float | `pafloatval` | `pafloatmin` | `pafloatmax` |
| 2 | Percentage | `pafloatval` | `pafloatmin` | `pafloatmax` |
| 3 | Scene Units | `paunitval` | `paunitmin` | `paunitmax` |
| 4 | Boolean | `paboolval` | | |
| 5 | Selector | `paselector` | | |
| 6 | String | `pastrval` | | |
| 7 | Label | | | |

RailClone can allocate every value array for every parameter; `patype` says which one is active at
an index.

```maxscript
index = findItem rc.paname "Y Offset"
if index > 0 and rc.patype[index] == 3 do rc.paunitval[index] = 10
```

## Base object arrays

The splines and surfaces a style takes as inputs. Segments have their own arrays.

| Array | Holds |
| --- | --- |
| `baid` | Identifiers |
| `batype` | 0 for a spline, 1 for a surface |
| `baname` | Input names |
| `banode` | Assigned scene nodes |
| `bafull` | Full Length state |
| `bastart`, `balength` | Start and Length, used when Full Length is off |
| `badesc` | Tooltip descriptions |

## Segment arrays

Segment geometry and its source scene object are separate references. Segment indices are local to an
object: a slot does not name the same Segment in another style, nor follow the order of nodes in the
graph XML. Check both array lengths before reading a slot.

| Array | Holds |
| --- | --- |
| `sid` | Identifiers |
| `sname` | Names used in the graph |
| `sobjref` | Geometry object references |
| `sobjnode` | Source scene nodes |
| `sobjoffset` | Geometry offset transforms (matrix3) |
| `sobjnodetm` | Captured source-node transforms (matrix3) |
| `sobjmtl` | Materials, or `undefined` |

| Group | Arrays |
| --- | --- |
| RC Slice | `slicesrc` |
| Transform | `spos`, `srot`, `ssca` |
| Alignment | `sxalign`, `syalign`, `szalign` |
| Padding and size | `spadin`, `spadout`, `spadtop`, `spadbottom`, `sfixedsize`, `sSizeScale` |
| Deform settings | `sinstance`, `sbend`, `sslice`, `snesting`, `srenderable`, `sexportforest`, `szdeform`, `ssurfconform`, `ssurfnormal`, `sslopefix`, `sflattop`, `sflatbottom` |
| Randomization | `srandtrans`, `srandrot`, `srandscale`, `srandmat`, `srt1`, `srt2`, `srr1`, `srr2`, `srs1`, `srs2` |
| Materials | `smaterial`, `smatrange` |
| Mapping | `smapping`, `smapreal`, `smapseamless`, `smapchans`, `smapsize`, `smapoff`, `smaprotx`, `smaproty`, `smaprotz` |

`slicesrc` picks the slice a Segment outputs under an RCSlice modifier: 0 Modifier Output, 1 Default,
2 Start, 3 End, 4 Top, 5 Bottom, 6 X Corner, 7 X Corner Top, 8 X Corner Bottom, 9 Start Top,
10 End Top, 11 Start Bottom, 12 End Bottom, 13 X Evenly, 14 X Evenly Top, 15 X Evenly Bottom,
16 Y Evenly, 17 Y Evenly Start, 18 Y Evenly End, 19 XY Evenly Intersection,
20 Y Evenly Corner Intersection.

| Value | `sxalign` | `syalign` | `szalign` |
| --- | --- | --- | --- |
| 0 | Automatic | Automatic | Automatic |
| 1 | Pivot | Pivot | Pivot |
| 2 | Left | Bottom | Bottom |
| 3 | Center | Center | Center |
| 4 | Right | Top | Top |

## Object methods (`rc.railclone`)

| Call | Returns | Does |
| --- | --- | --- |
| `segmentsUpdate 0 0` | void | Reloads Segment geometry from its source objects |
| `getStyleDesc()` | String | The style description |
| `setStyleDesc description` | | Sets the style description |
| `getXMLStyle()` | String | The style graph as XML (7.3.5+) |
| `setXMLStyle style` | String | Assigns a style graph from XML (7.3.5+). `Ok` means the XML was accepted, not that the result is as intended: check the output |
| `getXMLOutput()` | String | The generated items as XML: source Segment, transform matrix, bounding box, tags (7.3.5+) |
| `setProxyMode mode proxyfile` | String | Sets the Proxy Cache mode, creating a cache when required |
| `loadLibraryItemByPath path` | Integer | Loads a library item by its Library Browser route (6+); 0 success, 1 error |
| `resetCreatedVersion()` | void | Moves the style to current behaviour; can change styles that keep older algorithms |

- Style XML and Segment object references are separate parts of a RailClone object.
- `setProxyMode`: mode 0 disables the cache (returns an empty string), 1 creates an embedded cache
  (returns its size), 2 creates and saves an external cache at `proxyfile`, overwriting an existing
  file. Pass `""` as `proxyfile` for modes 0 and 1. A failed external cache returns the error text.
- To use a cached RailClone object as a Segment of another, turn off Display > Use Instancing Engine
  before enabling its Proxy Cache.
- `loadLibraryItemByPath` takes a route such as
  `@"\RailClone Library\Architecture\Exterior\Railings\Vynil Handrail 1"`, never a filesystem path.
  `rc.style` reads the route of the loaded item.

## RailClone Tools (`RailClone_Pro.global`, RailClone Pro)

| Call | Returns | Does |
| --- | --- | --- |
| `Instantiate mode layer autoDelete separatedMeshes forceInstances disableAtEnd` | void | Converts generated geometry to standalone objects |
| `InstantiateDelete()` | void | Deletes the instances generated before for the selected objects |
| `InstantiateEnable()` | void | Enables the selected source RailClone objects |
| `exportData filename fieldlist format` | Integer | Exports data from the selected objects |

- `Instantiate`: `mode` 0 individual objects, 1 a group, 2 a layer named by `layer`. `autoDelete`
  deletes earlier instances first, `separatedMeshes` makes separate meshes for non-instanced
  Segments, `forceInstances` instances where possible and ignores Material and UVW operators,
  `disableAtEnd` disables the source objects afterwards.
- `InstantiateDelete` goes by the names of the generated objects and of the source object: renaming
  them can prevent the deletion.
- `exportData`: `filename` is the base name without extension for the XML and FBX files, `fieldlist`
  the comma-separated fields of the XML, `format` 0 standard or 1 Unity transformation matrices.
