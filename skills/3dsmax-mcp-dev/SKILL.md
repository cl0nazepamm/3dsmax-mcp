---
name: 3dsmax-mcp-dev
description: Conventions and pitfalls for developing 3dsmax-mcp tools. Use when adding new MCP tools, writing MAXScript for the bridge, or debugging communication issues.
user-invocable: true
---

# 3dsmax-mcp Development Guide

## Workflow Rules
- **NEVER use raw `execute_maxscript` for material work** — always use `assign_material`, `set_material_property`, and `set_material_properties` for material creation, assignment, and property setting. Only use `execute_maxscript` for things with no dedicated tool: file I/O, texture map object creation (OSLMap, Bitmaptexture, ai_bump2d), and sub-material slot assignment within Multimaterials.
- **No rendering unless asked** — never render/capture viewport unless user explicitly requests it
- **Screenshot while building** — always capture viewport after completing major build steps so user can see progress. Take a final screenshot when a scene build is done to verify the result visually. Skip screenshots for non-visual work (writing MAXScript functions, code tasks). Take screenshots when debugging or when the user is stuck.
- **No spline viewport render unless asked** — never set `render_displayRenderMesh` or "Enable in Viewport" on splines unless user explicitly requests it. Hide splines used as paths.
- **Screen resolution is 4K (3840x2160)** — always use `width:3840 height:2160` for `capture_screen`. The 1920x1080 default only captures a quarter of the screen.

## Tool Selection Guide — Pick the Right Tool
**ALWAYS prefer dedicated tools over `execute_maxscript`.** Only use `execute_maxscript` when no tool covers your need.

### "I need to understand an object"
- **Quick overview** (class, transform, modifiers, material, mesh stats) → `inspect_object`
- **All properties with types** (before setting a value) → `inspect_properties` with target="baseobject" / "material" / "modifier"
- **Modifier params specifically** → `inspect_modifier_properties`
- **Object's detailed props** (transform, material, modifiers) → `get_object_properties`

### "I need to find things in the scene"
- **List all objects** (with filtering by class/pattern/layer) → `get_scene_info`
- **What materials are assigned to objects?** → `get_materials`
- **What material/modifier/texture types exist scene-wide?** → `find_class_instances` (with superclass for enumeration)
- **Is this object instanced? How many copies?** → `get_instances`
- **Which objects have shadows off / are non-renderable?** → `find_objects_by_property`
- **What does this object depend on?** → `get_dependencies`
- **What fog/volume/lens effects exist?** → `get_effects`
- **What State Sets / camera sequences exist?** → `get_state_sets` / `get_camera_sequence`

### "I need to work with materials"
- **Create & assign a new material** → `assign_material` (Arnold, Physical, Standard, Multi/Sub)
- **Set one material property** (color, float, map, clear map) → `set_material_property`
- **Set many material properties at once** (chrome preset, glass preset) → `set_material_properties`
- **Populate Multi/Sub-Object slots** → `set_sub_material` (create new or share from another slot via source_index)
- **Inspect material params before changing** → `inspect_properties` with target="material"
- **List all assigned materials** → `get_materials`
- **Find all material types scene-wide** → `find_class_instances` with superclass="material"

### "I need to work with texture maps"
- **Auto-create PBR material from a texture folder** → `create_material_from_textures` (scans folder, matches channels by suffix, wires Arnold/Physical/Redshift material in one call)
- **Create any texture map** (OSLMap, Bitmaptexture, ai_bump2d, Noise, etc.) → `create_texture_map` (stores as global variable)
- **Set properties on a texture map** (after creation) → `set_texture_map_properties`
- **Write OSL code + create OSLMap** in one step → `write_osl_shader` (writes file, creates map, sets params)
- **Wire a map into a material slot** → `set_material_property` with value="{global_var_name}"

### "I need to modify things"
- **Set a single object property** → `set_object_property`
- **Add/remove a modifier** → `add_modifier` / `remove_modifier`
- **Toggle modifier on/off** (viewport vs render) → `set_modifier_state`
- **Change a modifier param across the whole scene** → `batch_modify`
- **Collapse modifiers to mesh** → `collapse_modifier_stack`
- **De-instance a shared modifier** → `make_modifier_unique`
- **Move/rotate/scale** → `transform_object`
- **Enable/disable scene effects** → `toggle_effect` / `delete_effect`

### "I need to organize the scene"
- **Parent/unparent objects** → `set_parent`
- **Show/hide/freeze** → `set_visibility`
- **Select objects** → `select_objects`
- **Clone/instance** → `clone_objects`
- **Rename in batch** → `batch_rename_objects`

### "I need to build geometry"
- **Primitives** → `create_object`
- **Structures** (house, tower, bridge, etc.) → `build_structure`
- **Grid/array placement** → `place_grid_array` / `place_on_grid`
- **Circular placement** → `place_circle`
- **Floor plans** → `build_floor_plan`

### "I need to work with State Sets / Camera Sequencing"
- **Get all State Sets** (cameras, frame ranges, animation ranges, lock flags) → `get_state_sets`
- **Get camera switch timeline** (only camera-assigned state sets, sorted by start frame) → `get_camera_sequence`
- Use `get_camera_sequence` when building USD camera switch exports or Unreal Sequencer Camera Cut Tracks
- Use `get_state_sets` for a full overview of all state sets regardless of camera assignment

### "I need to see the scene"
- **Viewport screenshot** → `capture_viewport`
- **Full screen / UI panels** → `capture_screen`
- **Render** → `render_scene`
- **Identify objects visually** → `isolate_and_capture_selected`

### When to use `execute_maxscript`
Only when NO dedicated tool covers your need — e.g. animation keyframing, custom scripted operations, render settings, environment setup, or anything not listed above.

## Core Rule: Inspect Before Acting
**NEVER guess property names, class names, operator types, or parameter values.** Use `inspect_properties` or `inspect_modifier_properties` instead of raw MAXScript inspection. Only fall back to raw commands for interfaces/methods:
- **Object/modifier/material properties** → `inspect_properties` (preferred)
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

## MAXScript Pitfalls
- **Material/texturemap constructors** — NO parentheses after class name. Use `ai_standard_surface name:"Mat1" metalness:1.0` NOT `ai_standard_surface() name:"Mat1"`. The `()` causes a syntax error when followed by keyword params.
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
- **Reserved variable names** — `output`, `result`, `bmp`, `foliage`, `floor`, `osl`, and `OSLMap` are reserved/read-only in MAXScript global scope. Use alternatives like `outStr`, `msg`, `screenBmp`, `treeTop`, `hFloor`, `myOsl`.
- **`by` is a reserved keyword** — cannot be used as a function parameter or variable name (it's a keyword in `for i = 1 to N by step` loops). Use `bY` won't help (case-insensitive). Use `posY`, `boxY`, etc.
- **Noise modifier class** — `Noise` in MAXScript resolves to the Noise texture map, NOT the modifier. The modifier class is `Noisemodifier`. Use `addModifier obj (Noisemodifier scale:20 strength:[1,1,1])`.
- **`(getDir #temp)` is NOT system temp** — it returns Max's app-specific temp dir. Use `(dotNetClass "System.IO.Path").GetTempPath()` to match Python's `tempfile.gettempdir()`
- **String escaping** — use `_safe_name()` from `objects.py` before embedding user strings in MAXScript. Handles backslashes and quotes.
- **JSON building** — no JSON library in MAXScript. Build manually with string concatenation. Always escape with `escapeJsonString()` from `mcp_server.ms`.
- **f-strings with braces** — when using Python f-strings containing MAXScript curly braces, double them `{{` `}}` or use raw strings `r"""..."""`
- **.NET string to MAXScript** — when reading .NET strings (e.g. from StreamReader.ReadLine()), convert to MAXScript string with `str as string` before using `.count` or other MAXScript string methods. `.Length` on .NET strings can fail.
- **`numKeys` on SubAnims** — `numKeys` is NOT a function you can call on a SubAnim (e.g. `obj.pos.controller[3]`). Access keys through the actual controller: `obj.pos.controller.Z_Position.controller.keys.count` and iterate with `ctrl.keys[k]` to set tangent types.

## Communication Pitfalls
- Timeout default is 120s. Long operations (render) need explicit timeout: `client.send_command(maxscript, timeout=300)`
- TCP polling has 50ms interval — faster than file-based but still not instant
- ConnectionRefusedError means MAXScript TCP listener isn't running
- After editing Python files, restart MCP server process to pick up changes

## Viewport Capture
- **OSL viewport preview** — OSL maps ONLY render in viewport in **High Quality** mode (not Standard, not Default Shading). Switch via `actionMan.executeAction -844228238 "40"` for High Quality. Never use Standard mode for OSL — it will show black.
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
- **Don't Dummy on first build** — when creating a single object (e.g. one house), just build the parts loose. Only organize under Dummies when grouping/reusing multiples (e.g. a neighbourhood scene).
- **Dummy setup order**: (1) finish creating objects, (2) compute combined bounding box, (3) create Dummy, (4) set Dummy boxsize to bbox, (5) position Dummy at bbox center XY, (6) set pivot to minimum Z, (7) THEN parent objects into Dummy. Never parent first then reposition — that moves children.
- When attaching objects with different materials, assign different Material IDs before attaching so a Multi/Sub-Object material can distinguish them
- Convert to editable poly first (`convertToPoly obj`) before attaching

## Cloning & Instancing
- `instance` function doesn't work on group heads — use `maxOps.cloneNodes` with `cloneType:#instance` instead
- `maxOps.cloneNodes sourceArr cloneType:#instance newNodes:&cloneArr` — pass `&cloneArr` as reference to get the cloned nodes back
- After cloning, offset positions with a loop: `for c in clones do c.pos += [0, offset, 0]`
- **Instancing hierarchies** — `maxOps.cloneNodes` on a single Dummy does NOT bring children. To instance a hierarchy: attach objects into one mesh, use Groups (clone as unit), or pass the full hierarchy (dummy + all descendants) to cloneNodes.

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

## Arnold Layer RGBA
- **`ai_layer_rgba` properties are per-layer numbered** — use `input1_shader`, `input2_shader`, `operation2`, `enable2`, NOT `operation` or `input_shader`. Layer 1 is enabled by default; layer 2+ need `enable2 = true`. Multiply operation = 5.

## Arnold / OSL Material Inspection
- `getClassInstances Bitmaptexture target:mat` does NOT find Arnold or OSL maps — they use `OSLMap` class wrapped in `MultiOutputChannelTexmapToTexmap`
- To reach underlying OSL bitmap: `mat.base_color_shader.sourceMap` (goes through MultiOutputChannelTexmapToTexmap → OSLMap)
- OSL Uber Bitmap UDIM: set `.filename = "path/MapName.<UDIM>.ext"` and `.udim = 1`
- `Filename_UDIMList` can be left empty — OIIO auto-detects tiles from filesystem
- Arnold `ai_normal_map` chains through `RNMNormalBlend` OSL nodes — drill through `.input_shader`, `.NormalA_map`, `.NormalB_map` to find leaf bitmaps

## Shell Pitfalls
- **PowerShell `$` vars from bash** — `$var` in PowerShell gets eaten by bash. Write a `.ps1` file and run with `powershell -ExecutionPolicy Bypass -File script.ps1` instead.

## Advanced Reflection & Introspection
- **`getPropNames obj`** + **`getProperty obj propName`** + **`setProperty obj propName val`** — universal reflection API for reading/writing properties on any MAXScript-compatible object (modifiers, materials, controllers, base objects)
- **`showProperties obj to:stringstream`** — redirect property listing to a StringStream, then parse line-by-line to get **declared types** (e.g. `"worldUnits"`, `"texturemap"`, `"material"`, `"float array"`). `classof (getproperty ...)` only gives the runtime type of the current value — `showProperties` gives the declared/expected type, which is far more useful for empty slots
- **Property blacklist** — skip these properties to avoid crashes: `#adTextureLock` (duplicate, skip second occurrence), `#notused`, `#thelist`, `#geometryOrientationLookAtNode`, `#target_distance`
- **`exprForMAXObject obj`** — returns a MAXScript expression string that evaluates to the same object (e.g. `"$Box001.modifiers[#Bend]"`). Guard against invalid results: `matchpattern result pattern:"*(null)*"` or `matchpattern result pattern:"<<*"`
- **CATParent / Unwrap_UVW guard** — always skip these when recursively traversing subanims, they cause crashes or infinite loops

## Scene-Wide Queries
- **`getclassinstances ClassName`** — finds ALL instances of any class across the entire scene (modifiers, materials, controllers, textures, etc.). The single most powerful scene query function
- **`refs.dependentnodes target`** — from a reference target (modifier, material, controller) to the scene nodes that use it. "Which objects use this material?"
- **`refs.dependents obj`** — from a scene node to all the reference targets it depends on. "What materials/controllers/modifiers does this object use?"
- **Pattern: intersection of `getclassinstances` + `refs.dependentnodes`** — the gold standard for "find which objects use which instances of class X"
- **`SuperClass.classes`** — `material.classes`, `modifier.classes`, `Shadow.classes`, `Atmospheric.classes` etc. Enumerate all registered concrete classes under a superclass

## Instance Management
- **`InstanceMgr.GetInstances obj &instArr`** — get all instances (by-reference output with `&`)
- **`InstanceMgr.CanMakeObjectsUnique obj`** — check if object participates in instancing
- **`InstanceMgr.MakeObjectsUnique objArr #individual`** — de-instance objects
- **`InstanceMgr.makemodifiersunique obj mod #individual`** — de-instance a specific modifier
- **`InstanceMgr.MakeControllersUnique objArr ctrlArr #individual`** — de-instance controllers
- **`instancereplace obj sourceObj`** — replace obj geometry with instance of sourceObj
- **`obj.baseobject = sourceObj`** — share base object (reference). `obj.baseobject = copy obj.baseobject` to break reference

## Modifier Stack Advanced
- **Three enable flags**: `mod.enabled` (master), `mod.enabledInViews` (viewport only), `mod.enabledInRenders` (render only) — independent booleans
- **`maxOps.CollapseNodeTo obj modIndex off`** — collapse stack to a specific modifier. `off` = don't prompt
- **`maxOps.CollapseNode obj off`** — collapse entire stack
- **`addModifier obj theMod before:(index-1)`** — insert modifier at specific stack position (zero-based `before:`)
- **`addModifier obj theMod`** = instance (shared reference), **`addModifier obj (copy theMod)`** = independent copy

## Atmospherics & Render Effects
- **`numAtmospherics`** / **`getAtmospheric i`** — scene-level atmospheric effects (Fog, Volume Light, Fire, etc.)
- **`numEffects`** / **`getEffect i`** — scene-level render effects (Lens Effects, Blur, etc.)
- **`setActive atm true/false`** — enable/disable an atmospheric or effect
- **`isActive atm`** — read active state. NOT `getActive` — `getActive` is undefined; the read function is `isActive`
- **Delete in reverse order**: `for i = arr.count to 1 by -1 do deleteAtmospheric arr[i]` — prevents index shifting

## Batch Operation Safety
- **`disableSceneRedraw()` / `enableSceneRedraw()` / `redrawViews()`** — always wrap batch operations to prevent viewport thrashing
- **`undo "Description" on ( ... )`** — wrap destructive operations in named undo blocks for atomicity
- **`keyboard.escPressed`** — check inside long loops to allow user abort
- **Preserve selection**: `local tmpsel = selection as array` before operations, `select tmpsel` after

## Property Type Handling
- **Compound types** must be handled component-by-component:
  - `Point3`: `.x`, `.y`, `.z`
  - `color`: `.r`, `.g`, `.b` (0-255 integer range)
  - `Quat`: `.w`, `.x`, `.y`, `.z` — use `quattoeuler` to read Euler angles, `eulerAngles` constructor to set
  - `SubAnim`: `.Pos.x`, `.Rotation.x`, `.Scale.x` for 9-channel decomposition
- **Light intensity fallback chain** — different lights use different property names: try `.multiplier`, `.intensity`, `.skymult`, `.globals.multiplier`, `.intensity_multiplier`, `.power` with cascading try/catch

## Testing
- `execute_maxscript` is the escape hatch — use it to test raw MAXScript without writing a full tool
- Check comms dir exists to verify MAXScript listener is running
- After editing Python tool files, the MCP server process must restart to pick up changes
