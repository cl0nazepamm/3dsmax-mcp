---
name: 3dsmax-mcp-dev
description: Conventions and gotchas for developing 3dsmax-mcp tools. Use when adding new MCP tools, writing MAXScript for the bridge, or debugging communication issues.
user-invocable: true
---

# 3dsmax-mcp Development Guide

## Core Rule: Inspect Before Acting
**NEVER guess property names, class names, operator types, or parameter values.** Always query first:
- `showProperties obj` — list all properties of an object/modifier/material
- `showInterfaces obj` — list all interfaces and their methods
- `showMethods obj` — list methods on an interface
- `getPropNames obj` — get property names as array
- `classOf obj` / `superClassOf obj` — identify what you're working with
- `for c in <superclass>.classes do print c` — discover available classes (e.g. `modifier.classes`, `GeometryClass.classes`)
- `showProperties obj.modifiers[1]` — inspect a specific modifier's params
- `showProperties obj.material` — inspect material params
- Scene hierarchy: `for obj in objects collect #(obj.name, obj.parent, classOf obj)` — understand parent/child relationships
- Modifier stack: `for m in obj.modifiers collect #(m.name, classOf m)` — list all modifiers on an object
- Material tree: `showProperties obj.material` then drill into sub-materials/texmaps

Call these inspection commands BEFORE writing any manipulation code. This avoids wasted attempts with wrong property names or invalid values.

## Architecture
- Python MCP server (FastMCP) communicates with 3ds Max via TCP socket
- Port: `127.0.0.1:8765`
- Protocol: JSON + newline delimiter, one request-response per connection
- MAXScript uses .NET `TcpListener` with timer-based polling (50ms) to stay non-blocking

## Adding a New Tool
1. Create `src/tools/<name>.py`
2. Import `from ..server import mcp, client`
3. Decorate with `@mcp.tool()`
4. Build MAXScript string, send via `client.send_command(maxscript)`
5. Return `response.get("result", "")` or appropriate default

## MAXScript Gotchas
- **Case-insensitive variables** — MAXScript is case-insensitive. `R` and `r` are the SAME variable. Always use distinct descriptive names (e.g. `ringRadius` / `tubeRadius`). This applies to ALL identifiers: variables, function names, properties.
- **No `local` at top level** — `execute()` runs at global scope; using `local` outside a function/block causes a compile error. Use bare variable names instead.
- **`viewport.setType` values** — use `#view_persp_user` (not `#view_persp`). Valid values include `#view_left`, `#view_front`, `#view_top`, `#view_iso_user`, `#view_persp_user`, etc.
- **No `stringJoin`** — MAXScript has no built-in array-to-string join. Use manual loop:
  ```maxscript
  local result = "["
  for i = 1 to arr.count do (
      if i > 1 do result += ","
      result += arr[i]
  )
  result += "]"
  ```
- **Reserved variable names** — `output`, `result`, `bmp`, `foliage`, and `floor` are reserved/read-only in MAXScript global scope. Use alternatives like `outStr`, `msg`, `screenBmp`, `treeTop`, `hFloor`.
- **Noise modifier class** — `Noise` in MAXScript resolves to the Noise texture map, NOT the modifier. The modifier class is `Noisemodifier`. Use `addModifier obj (Noisemodifier scale:20 strength:[1,1,1])`.
- **`(getDir #temp)` is NOT system temp** — it returns Max's app-specific temp dir. Use `(dotNetClass "System.IO.Path").GetTempPath()` to match Python's `tempfile.gettempdir()`
- **String escaping** — use `_safe_name()` from `objects.py` before embedding user strings in MAXScript. Handles backslashes and quotes.
- **JSON building** — no JSON library in MAXScript. Build manually with string concatenation. Always escape with `escapeJsonString()` from `mcp_server.ms`.
- **f-strings with braces** — when using Python f-strings containing MAXScript curly braces, double them `{{` `}}` or use raw strings `r"""..."""`
- **.NET string to MAXScript** — when reading .NET strings (e.g. from StreamReader.ReadLine()), convert to MAXScript string with `str as string` before using `.count` or other MAXScript string methods. `.Length` on .NET strings can fail.

## Communication Pitfalls
- Timeout default is 120s. Long operations (render) need explicit timeout: `client.send_command(maxscript, timeout=300)`
- TCP polling has 50ms interval — faster than file-based but still not instant
- ConnectionRefusedError means MAXScript TCP listener isn't running
- After editing Python files, restart MCP server process to pick up changes

## Viewport Capture
- **OSL viewport preview** — OSL maps render in viewport when using **Standard** or **High Quality** mode (not Default Shading). Switch via `actionMan.executeAction -844228238 "13"` for Standard mode. No need to render for basic OSL preview.
- `gw.getViewportDib()` captures viewport as bitmap — much faster than render
- Save to comms dir: `(dotNetClass "System.IO.Path").GetTempPath() + "3dsmax-mcp\\viewport.png"`
- Call `completeredraw()` before capture to ensure display is current
- Wireframe mode shows sub-object selections clearly (red = selected, blue = unselected)
- **ALWAYS frame before capturing**: select what you want to see, run `max zoomext sel`, then capture. Never manipulate viewport TM directly — it's unreliable. Use view type switches (`#view_front` then `#view_persp_user`) to reset stuck perspectives.
- Don't try to set viewport camera position manually with `viewport.setTM` — results are unpredictable. Use `max zoomext sel` on selected objects instead.

## Scattering
- When user says "scatter", use **procedural scatter tools** — NOT manual placement with loops
- Manual placement via scripted loops is only for when the user specifically requests it
- **Prioritize tyFlow over PFlow** — tyFlow is more capable and preferred
- tyFlow setup via MAXScript: create `tyFlow()`, use `tf.tyFlow.addEvent()`, then `evt.Event.addOperator "OperatorName" index`
- Operators become accessible as event sub-properties (e.g. `evt.Birth`, `evt.Position_Object`, `evt.Shape`)
- tyFlow operator names for addOperator: "Birth", "Position Object", "Shape", "Display" (note: "Shape" not "Shape Instance")
- tyFlow Shape operator: many properties are read-only (`shapeMode`, `shapeMode3D`). Use tab arrays instead: `instancedGeo_tab = #(node)`, `shape_type_tab = #(3)`
- `ScatterGeometry` class exists in Max 2025+ but `Geo_Array` is fixed-size and cannot be set via MAXScript — it's UI-only
- Legacy `Scatter` compound object is not creatable via MAXScript in Max 2026
- **Mesh surface sampling workaround** — when built-in scatter tools fail via MAXScript, use `snapshotAsMesh` on the distribution surface, filter faces by normal/position, then instance objects at `meshOp.getFaceCenter` oriented to `getFaceNormal`. This is the reliable fallback for surface scattering.
- `getFaceNormal` is a **global function** (`getFaceNormal mesh faceIdx`), NOT a `meshOp` method — `meshOp.getFaceNormal` does not exist
- `fn` is a **reserved keyword** in MAXScript (short for `function`) — use `fNorm` or similar for face normal variables
- Cloned instances inherit hidden state from the source — if the template is hidden, `unhide` clones after `maxOps.cloneNodes`

## Object Organization
- **Prefer Dummy hierarchy** — parent related objects under a `Dummy` node to keep the outliner clean. Groups are a secondary option. Attach is for when objects truly need to be a single mesh.
- **Dummy setup rule**: create Dummy → size `boxsize` to children's combined bounding box → center pos on bbox center → parent children → set `pivot` to `[center.x, center.y, bbMin.z]` (snap pivot to min Z)
- When attaching objects with different materials, assign different Material IDs before attaching so a Multi/Sub-Object material can distinguish them
- Convert to editable poly first (`convertToPoly obj`) before attaching

## Cloning & Instancing
- `instance` function doesn't work on group heads — use `maxOps.cloneNodes` with `cloneType:#instance` instead
- `maxOps.cloneNodes sourceArr cloneType:#instance newNodes:&cloneArr` — pass `&cloneArr` as reference to get the cloned nodes back
- After cloning, offset positions with a loop: `for c in clones do c.pos += [0, offset, 0]`

## Splines
- Renderable spline properties: `render_displayRenderMesh`, `render_displayRenderSettings`, `render_thickness`
- NOT `render_viewport` — that property doesn't exist on SplineShape
- Use `#smooth` knot type with `#curve` for catenary/drooping wires
- `addNewSpline`, `addKnot`, `updateShape` — always call `updateShape` after adding knots

## Sub-Object / Edit Poly
- Object pivot and position affect vertex world positions — don't assume Z=0 is the midpoint
- `polyOp.getVert` returns world-space positions
- Set `subObjectLevel = 1` for verts, `2` for edges, `4` for faces before setting selections

## Boolean Operations
- Use the modern `BooleanMod` modifier — NOT `ProBoolean` (outdated, only use if user specifically requests it)
- Class name is `BooleanMod()`, added via `addModifier obj (BooleanMod())`
- Access interface: `bm.BooleanModifier` to call methods
- Add operands: `bmInterface.AppendOperand #single operandNode:theNode operationType:#subtraction`
- Operation types: `#union`, `#intersection`, `#subtraction`, `#merge`, `#attach`, `#insert`, `#split`
- Operation options: `#none`, `#imprint`, `#cookie`
- Can add multiple operands to a single BooleanMod — no need to collapse between operations
- **Operand B must fully intersect operand A** — thin/shallow cutters won't cut through. Make boolean operands thicker than the wall they're cutting (e.g. if wall is 2 units deep, cutter should be at least 4+ units deep to ensure clean cut)

## Scene Safety
- **Hold/Fetch over Undo** — for critical operations (advanced scripts, destructive edits, scene-wide manipulation), call `holdMaxFile()` before and `fetchMaxFile quiet:true` to revert. Always use `quiet:true` on fetch — without it, Max shows a confirmation dialog that blocks the script and causes a timeout.

## Primitives & Parameters
- **Prism parameters** — `side1Length`, `side2Length`, `side3Length` (NOT `side1`, `side2`, `side3`). The prism is oriented with the base on XY and height along Z.
- **Gable roof pattern** — two Box panels meeting at ridge. Set pivot on the ridge edge (`in coordsys local obj.pivot = [0, -panelW/2, 0]`), rotate around X axis (`eulerAngles pitch 0 0`), position both at ridge height. Ridge runs along X when rotating around X.

## Testing
- `execute_maxscript` is the escape hatch — use it to test raw MAXScript without writing a full tool
- Check comms dir exists to verify MAXScript listener is running
- After editing Python tool files, the MCP server process must restart to pick up changes
