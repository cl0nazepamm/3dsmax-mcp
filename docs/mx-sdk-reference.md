# sdk (_mx) reference

3ds Max for agents, bound from the SDK: the scene's nodes, parameters, modifiers, materials and animation, and modeling, lighting, viewport, file and plugin work on them.

Build `b20261109192948`, 3ds Max SDK 2027.

## Functions

### `action_tables(pattern: str | None = None) -> list[_mx.ActionTable]`

Action tables by wildcard name: action_tables('Main*').

### `actions(pattern: str | None = None, table: str | None = None) -> list[_mx.Action]`

Actions by wildcard menu text, description or category across tables: actions('*Isolate*').

### `agent_viewport(width: int = 1000, height: int = 740, minimized: bool = False) -> _mx.AgentViewport`

Open the agent's own floating viewport (or restore it) and return it; the size applies when it first opens: av = agent_viewport().

### `animate(on: bool = True) -> _mx.AnimateMode`

with animate(): edits create keys, like the Auto Key button.

### `at(frame: float) -> _mx.AtTime`

with at(frame): reads and edits happen at that frame; the time slider does not move.

### `backup_material_library(source: str = 'all', folder: str = '', path: str = '', prefix: str = '') -> object`

Save library, editor slots and both combined ('all') to timestamped .mat files; returns what was saved where.

### `camera_sequence() -> list[_mx.StateSet]`

The State Sets that have a camera, by render start frame (sets without a render range last).

### `capabilities() -> dict`

Report: max_version, renderer, renderers, known plugins installed (tyflow, forest_pack, ...) and class counts.

### `capture_isolated(nodes: object | None = None) -> list[_mx.Capture]`

Capture each top-level hierarchy of the selection (or of nodes) alone, framed in the active view; instances share one image, and visibility and selection come back.

### `capture_screen(target: str = 'screen', crop: object | None = None, max_width: int = 1600, max_height: int = 0, desktop: bool = False) -> _mx.Capture`

Save an open frame buffer or editor window (vray_vfb, corona_vfb, fstorm_vfb, tyflow_editor) to a JPEG, or the primary monitor with desktop=True (only when the user asked: it shows other applications); crop=(x, y, w, h) in its pixels: capture_screen('vray_vfb').

### `changes(since: object) -> list[_mx.Change]`

Persistent scene edits after a Mark (or its seq), oldest first; selection changes are left out.

### `class_info(name: str, kind: str | None = None) -> _mx.ClassInfo`

A class by MAXScript or UI name: class_info('Bend'); kind narrows a name several kinds share.

### `class_relationships(pattern: str | None = None, superclass: str | None = None) -> object`

Classes whose parameters take nodes, materials, texmaps or other references: class_relationships('*Scatter*', superclass='geometry').

### `classes(kind: str | None = None, pattern: str | None = None, plugin: str | None = None) -> list[_mx.ClassInfo]`

Registered classes by kind, wildcard name and plugin file: classes('modifier', '*Bend*'), classes(plugin='tyFlow*').

### `contacts(nodes: object, against: object | None = None, tolerance: float = 0.0, near_gap: float = 0.0, include_separate: bool = False) -> dict`

Evaluated meshes that penetrate, intersect, touch or nearly touch, worst first: contacts(legs, against=[floor, seat]) checks each leg against those only. tolerance and near_gap default to 0.1 mm and 10 mm in scene units; depth is a lower and gap an upper bound, both from vertices. Nodes without a mesh are listed in 'skipped'.

### `create(cls: str, name: str = '', pos: object | None = None, **params) -> _mx.Node`

Create an object (geometry, shape, helper, light, camera) with parameters: create('Sphere', 'Ball', pos=(0, 0, 10), radius=10, segs=32).

### `create_mesh(name: str, verts: object, faces: object) -> _mx.Node`

Create an Editable Poly from world vertices and polygons of 1-based vertex IDs, counterclockwise seen from outside, as one undo step: create_mesh('Tri', [(0, 0, 0), (10, 0, 0), (0, 10, 0)], [(1, 2, 3)]).

### `curve_create(definition: object, name: str = '', parameters: object | None = None) -> _mx.Node`

Build a curve_model definition as an editable spline or quad cage centered on its bounds, saving the recipe for node.curve_update.

### `curve_preview(definition: object, parameters: object | None = None) -> dict`

Evaluate a curve_model definition without touching the scene: counts, bounds and per-curve QA. definition={'curves': {name: curve}, 'output': {'kind': 'curve' | 'sweep' | 'loft', ...}}.

### `delete(nodes: object) -> None`

Delete a node or a list of nodes; an empty list deletes nothing.

### `effects(kind: str | None = None) -> list[_mx.Effect]`

Atmospherics then render effects, or one kind: effects('atmospheric').

### `environment_light(*, intensity: float, unit: str, hdri: str | None = None, map: object | None = None, rotation: float = 0.0, color_space: str | None = None, replace: bool = False, renderer: str | None = None) -> _mx.Light`

Light the scene from an HDRI (.exr/.hdr) or an existing map handle: environment_light(intensity=1, unit='renderer', hdri='C:/sky.exr'). replace=True rebinds an occupied environment slot.

### `fetch() -> None`

Restore what hold() held (Edit > Fetch), dropping the changes since; refuses a hold made elsewhere or for another scene.

### `file_info(path: str | os.PathLike | collections.abc.Sequence[str | os.PathLike], objects: bool = False, classes: bool = False) -> object`

A .max file's metadata without opening it: {'path', 'size', 'created', 'modified' (UTC), 'title', 'author', ...}, plus 'objects' (names) and 'classes' (plugin classes by kind) when asked. A list of paths gives a list of reports; an unreadable file reports its 'error'.

### `find(pattern: str | None = None, cls: str | None = None, layer: str | None = None, roots_only: bool = False, uses: str | None = None, **props) -> list[_mx.Node]`

Nodes matching every filter: find('Wall*', cls='Box', layer='Walls', uses='TurboSmooth', hidden=False, height=50). Keywords compare node flags (hidden, frozen, renderable, cast_shadows, material, ...) or base object parameters; numbers within 1e-4, text ignoring case.

### `frame() -> float`

Current frame.

### `group(nodes: object, name: str = '') -> _mx.Group`

Group nodes under a new group head: group(mx.nodes('Chair*'), 'Chairs').

### `groups() -> list[_mx.Group]`

Every group in the scene, nested ones included.

### `hold() -> None`

Save the scene to the hold buffer (Edit > Hold) for fetch().

### `import_file(path: str | os.PathLike) -> list[_mx.Node]`

Import a file (FBX, OBJ, USD, ...) with its importer's defaults, no dialogs, and return the new nodes.

### `interface(name: str) -> _mx.Interface`

A core or plugin-class static interface by name: interface('LayerManager').

### `interfaces(pattern: str | None = None) -> list[_mx.Interface]`

Core interfaces (MAXScript's global ones), by wildcard name: interfaces('*Layer*').

### `layer(name: str | None = None, create: bool = False) -> _mx.Layer`

Layer by name, ignoring case; no name: the current layer. layer('Walls', create=True) makes a missing one.

### `layers() -> list[_mx.Layer]`

Every layer, in the layer manager's order.

### `light(kind: str, *, intensity: float, unit: str, name: str = '', shape: str | None = None, width: float | None = None, height: float | None = None, radius: float | None = None, length: float | None = None, pos: object | None = None, aim_at: object | None = None, direction: object | None = None, up: object | None = None, kelvin: float | None = None, color: object | None = None, enabled: bool = True, cast_shadows: bool = True, body: str | None = None, solar: object | None = None, renderer: str | None = None) -> _mx.Light`

Create a light for the renderer's provider in one undo step: light('area', intensity=1500, unit='lm', shape='rectangle', width=120, height=60, pos=(0, -200, 250), aim_at=(0, 0, 50), kelvin=4500). Kinds, shapes and units: light_capabilities(); default color 6500 K.

### `light_capabilities(renderer: str | None = None) -> dict`

What the current (or named) renderer's lighting provider supports: kinds, shapes, units, colors, notes.

### `lights() -> list[_mx.Light]`

Every light node, plus the environment map when a provider decodes it.

### `loft_mesh(name: str, sections: object, parameters: object | None = None, *, profile_closed: bool = True, close_path: bool = False, caps: bool = False, reverse: bool = False) -> _mx.Node`

Create a parametric quad loft (Editable Poly) through matched cross sections and return its node: loft_mesh('Rail', [[(0, 0, 0), ('w', 0, 0), ('w', 'd', 0)], [(0, 0, 'h'), ('w', 0, 'h'), ('w', 'd', 'h')]], {'w': 10, 'd': 4, 'h': 50}). Sections have the same point count in corresponding order, in world space; coordinates are numbers or expressions over parameters (+ - * / % **, sin/cos in radians, sqrt, abs, min, max, pi). profile_closed wraps each section, close_path joins the last section to the first, caps adds end faces (closed profiles, open path), reverse flips the winding.

### `macro(name: str, category: str | None = None) -> _mx.Macro`

A macroscript by name: macro('SmartIsolate', 'Tools').

### `macros(pattern: str | None = None, category: str | None = None) -> list[_mx.Macro]`

Macroscripts by wildcard name, tooltip or button text and category: macros('*isolate*').

### `mark() -> _mx.Mark`

Mark the change journal: m = mark(); later, changes(m) lists the edits since and patch(ops, expected=m) refuses to run if there were any.

### `material(cls: str = 'PhysicalMaterial', name: str = '', **params) -> _mx.Material`

Create a material with parameters: material('PhysicalMaterial', 'Red', base_color=(0.8, 0.1, 0.1)).

### `material_classes(pattern: str | None = None) -> list[str]`

Material class names material() takes: material_classes('*PBR*').

### `material_library() -> list`

The current material library (currentMaterialLibrary): Materials and Texmaps.

### `materials(pattern: str | None = None) -> list[_mx.Material]`

Materials assigned to scene nodes, optionally filtered by a wildcard name: materials('Brick*').

### `maxscript(code: str) -> str`

Evaluate MAXScript; returns the result as text.

### `medit_materials() -> list`

The 24 Compact Material Editor slots (meditMaterials), None where empty.

### `merge(path: str | os.PathLike, names: str | collections.abc.Sequence[str] | None = None, duplicates: str = 'rename', materials: str = 'rename') -> list[_mx.Node]`

Merge a .max file's objects (all, or names) and return the new nodes. Name clashes: duplicates='rename', 'skip', 'merge' (keep both) or 'delete_old'; materials='rename', 'scene' or 'merged'.

### `native(type: str, **args) -> object`

Run one of the bridge's handlers in-process: native('scene_delta', ...). Returns its reply as Python values.

### `node(key: object) -> _mx.Node`

Node by name, handle or Node; NotFound or Ambiguous otherwise.

### `nodes(pattern: str | None = None) -> list[_mx.Node]`

Scene nodes, optionally filtered by a wildcard name: nodes('Wall*').

### `osl(code: str, name: str = '', **params) -> _mx.Texmap`

Compile OSL code into an OSLMap and set its shader parameters; name defaults to the shader's.

### `osl_info(shader: str) -> dict`

Compile an OSL shader (a .osl path or a name in 3ds Max's OSL folder) in a temporary map: its parameters and outputs.

### `overview() -> dict`

Scene summary: object, hidden, frozen and root counts, nodes per class, material and modifier counts, layers in use.

### `patch(ops: object, expected: object | None = None, dry_run: bool = False, label: str = 'MCP Scene Patch') -> object`

Validate, then apply node edits as one undo step, all or nothing: patch([{'op': 'rename', 'target': n, 'name': 'Wall'}, {'op': 'transform', 'target': 'Box001', 'move': (0, 0, 10)}]). Ops: rename (name); transform (relative move, rotate in degrees, scale; coordinate_system 'world' or 'local'); set_flags (hidden, frozen, renderable, cast_shadows, receive_shadows); set_parent (parent: a node or None, keep_transform). expected=mark() taken earlier (or its scene_seq) refuses a scene changed since; dry_run=True validates only. Returns a report.

### `plugin_patch(edits: object, all_instances: bool = False) -> dict`

Patch several objects in one guarded undo step: plugin_patch([(s1, {'multiplier': 2}), (s2, {'size': 5})]).

### `plugins(pattern: str | None = None) -> list[_mx.Plugin]`

Plugin DLLs by wildcard file name or description: plugins('*corona*').

### `redraw() -> None`

Redraw the viewports.

### `rename(renames: dict) -> list[_mx.Node]`

Rename nodes as one undo step, all or nothing: rename({'Box001': 'Wall_A', node: 'Wall_B'}); refuses names that would be shared.

### `render(width: int | None = None, height: int | None = None, output: str = '') -> _mx.RenderJob`

Queue a production render of the active view at the time slider's frame; it starts when this script returns, or now with .wait(): mx.render(output='C:/r/a.png').wait(). Size defaults to Render Setup's.

### `render_jobs() -> list[_mx.RenderJob]`

Render jobs queued in this Max session, oldest first.

### `reset() -> None`

Reset to an empty scene (File > Reset); refuses while the scene has work that was never saved.

### `resolve(ref: object) -> _mx.Node`

Node by '/Parent/Child' path, name, handle or Node, or by a {'handle', 'name', 'path'} dict whose parts must agree.

### `save() -> str`

Save the scene to its own file and return the path; an untitled scene raises (never Save As).

### `scene_file() -> str | None`

Path of the open .max file, or None while the scene is untitled.

### `scene_info() -> dict`

The open scene: {'file', 'unsaved', 'objects', 'faces'}, faces of the evaluated meshes, triangulated.

### `scene_patterns() -> dict`

How the scene combines classes, as {pattern: count}: classes, modifier stacks and material, modifier and texmap pairings, by 3ds Max's display names.

### `scene_qa(checks: object | None = None, nodes: object | None = None, fix: object | None = None, dry_run: bool = False, expected: object | None = None, far_from_origin: float | None = None) -> object`

Scan scene hygiene (names, transforms, hierarchy, groups, timeline; no mesh checks) or repair it: scene_qa(fix=['name_collisions', 'empty_names']). nodes= limits the scope; expected= guards a fix as in patch().

### `search_max_files(folder: str | os.PathLike, pattern: str = '*', recursive: bool = True) -> list`

The .max files under folder with objects named like pattern: [{'path', 'objects', 'names'}], search_max_files('D:/lib', 'chair*'); then merge(hit['path'], hit['names']).

### `select(nodes: object, add: bool = False) -> None`

Select nodes, replacing the selection unless add=True; select([]) clears it.

### `selection() -> list[_mx.Node]`

Selected nodes.

### `selection_set(name: str, nodes: object | None = None) -> _mx.SelectionSet`

Named selection set by name; with nodes, create or replace it: selection_set('Lights', mx.nodes('Light*')).

### `selection_sets() -> list[_mx.SelectionSet]`

Every named selection set.

### `set_frame(frame: float) -> None`

Move the time slider.

### `smart_import(folder: str | os.PathLike, texture_folder: str | os.PathLike | None = None, recursive: bool = True, grid_spacing: float = 200.0, lod_filter: str = 'lod0', name_pattern: str = '', exclude_pattern: str = '') -> dict`

Import the mesh files under folder onto an XY grid, pairing each with its textures by name: {'imported': [{'path', 'name', 'nodes', 'textures': {channel: path}, 'material_name', 'mtlx'}], 'skipped', 'failed', 'lod_skipped', 'filtered_out'}. Skips a file when an object named like its stem is in the scene.

### `spline(points: object, name: str = '', closed: bool = False, knot: str = 'smooth', center_pivot: bool = False) -> _mx.Node`

Create an editable spline from world points, dicts or Knots: spline([(0, 0, 0), (40, 0, 0), (40, 30, 0)], 'Rail', knot='corner'). knot: corner, smooth, bezier, bezier_corner or bounded (short handles that do not overshoot).

### `state_sets() -> list[_mx.StateSet]`

The top-level State Sets with their cameras and ranges (snapshots).

### `texmap(cls: str, name: str = '', **params) -> _mx.Texmap`

Create a texture map with parameters: texmap('Bitmaptexture', 'Wood', filename='C:/tex/wood.png').

### `texmap_classes(pattern: str | None = None) -> list[str]`

Texture map class names texmap() takes: texmap_classes('*bitmap*').

### `timeline(start: float | None = None, end: float | None = None, fps: int | None = None) -> tuple`

Animation range and frame rate: (start, end, fps); pass any to change it.

### `undo() -> str | None`

Undo the last scene operation; returns the undo entry's name ('' if unnamed), or None when nothing was undone. In a run_python script, what the script changed so far becomes its own undo step first, so undo() undoes that, or the step before the script when it has changed nothing yet.

### `viewport(source: str = 'auto') -> object`

The AGENT VIEWPORT while it is open, else the user's active view; source='active' or 'agent' picks one: viewport().capture().

## Classes

### Action

An action item (menu command, shortcut target): text, description, run().

- `.category` (str)
- `.description` (str)
- `.id` (int)
- `.macro` (_mx.Macro | None): The macroscript it runs, or None.
- `.run() -> None`: Run it, like actionMan.executeAction.
- `.table` (_mx.ActionTable)
- `.text` (str): Menu text.

### ActionTable

An action table: name, id, actions(pattern).

- `.actions(pattern: str | None = None) -> list[_mx.Action]`: Its actions, by wildcard menu text, description or category.
- `.context_id` (int)
- `.id` (int)
- `.name` (str)

### AgentViewport

The agent's floating AGENT VIEWPORT: navigate, pick and capture without touching the user's views.

- `.capture(crop: object | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: Save this view, or the VFB its V-Ray/Corona preview renders into, to an image; crop=(x, y, w, h) in VFB client pixels.
- `.frame(nodes: object | None = None, padding: float = 1.15) -> None`: Fit nodes and their descendants (default: visible geometry, or the lights and cameras of a rig-only scene).
- `.minimize() -> None`: Park the window; restore() it before navigating or capturing.
- `.orbit(yaw: float = 0.0, pitch: float = 0.0) -> None`: Orbit the target by yaw and pitch degrees: orbit(30, 10).
- `.pan(x: float = 0.0, y: float = 0.0) -> None`: Move the view and its target by x, y scene units in the view plane.
- `.pick(x: float, y: float, capture: _mx.Capture | None = None) -> _mx.Hit | None`: The nearest surface under image point x, y (0-1 from the top-left) as a Hit, or None; capture refuses a view that changed since: pick(0.5, 0.4, cap).
- `.project(points: object, capture: _mx.Capture | None = None) -> list`: Image positions (x, y, depth) of world points, x and y 0-1 from the top-left as pick takes them; None behind the eye; no occlusion test: project([(0, 0, 50)]...
- `.ray(x: float, y: float, capture: _mx.Capture | None = None) -> tuple`: World ray (origin, direction) through image point x, y (0-1 from the top-left).
- `.release() -> None`: Stop any preview, hide the window and give the panel back; agent_viewport() opens it again.
- `.render(mode: str, renderer: str = 'activeshade') -> None`: Interactive preview here: activeshade, vray_ipr, vray_vfb, corona_vfb, or shaded to stop; previews are progressive and block pick/ray/project until shaded: r...
- `.render_mode` (str): The preview: shaded, activeshade, vray_ipr, vray_vfb or corona_vfb.
- `.restore() -> None`: Show the minimized window again and redraw it.
- `.window_state` (str): visible, minimized, hidden, offscreen, unavailable, or closed once released.
- `.zoom(factor: float) -> None`: Move toward the target (factor < 1) or away (> 1); orthographic views scale.

### AnimateMode

(no description)


### AtTime

(no description)


### Boolean

A Boolean modifier (BooleanMod), a Modifier with an operand stack: b.operands, b.set_operand(2, operation='union').

- `.extract_operand(index: int) -> _mx.Node`: Copy operand `index` back out as a new scene node and return it; the operand stays.
- `.operands` (list[_mx.Operand]): Operands in stack order (the base object left out); index is what set_operand and friends take.
- `.remove_operand(index: int) -> None`: Delete operand `index` from the stack; later indices shift down.
- `.set_operand(index: int, *, operation: str | None = None, option: str | None = None, name: str | None = None, disabled: bool | None = None) -> None`: Retune operand `index`: b.set_operand(2, operation='union', option='imprint', name='Door', disabled=False).

### Capture

A saved image: open(capture.path) or os.fspath(capture); width, height, and what it shows.

- `.frames` (list[int]): The frames of a capture_frames strip, else [].
- `.grid` (tuple[int, int]): (columns, rows) of the tiles.
- `.height` (int): Image height in pixels.
- `.nodes` (list[_mx.Node]): capture_isolated: the instances shown, the captured hierarchy's top node first.
- `.path` (str): The image file: PNG, or JPEG from capture_screen.
- `.source` (str): What it shows: 'agent', 'active', or the capture_screen target.
- `.views` (object): The tiles' views in order: names or (x, y, z) directions; [] for one plain view.
- `.width` (int): Image width in pixels.

### Change

A journal entry (a snapshot): seq, scene_seq, type ('added', 'name_changed', 'deleted', ...), node, name (when it happened), details.

- `.details` (object)
- `.name` (str)
- `.node` (_mx.Node | None)
- `.scene_seq` (int)
- `.seq` (int)
- `.type` (str)

### ClassInfo

A plugin class: name, kind, plugin, describe(), interfaces, schema(), nodes().

- `.category` (str): Create-panel category.
- `.class_id` (tuple[int, int]): Class_ID as (a, b), like MAXScript's classID.
- `.creatable` (bool): Whether scripts can create it (public and not marked no-create).
- `.describe() -> list[str]`: Header plus one line per parameter: 'radius: distance = 25.0 [0.0, 1e+30] +anim' (MAXScript units).
- `.interfaces` (list[_mx.Interface]): Interfaces the class publishes; static ones are callable, mixins need an object.
- `.kind` (str): 'geometry', 'modifier', 'material', ... or the MAXScript superclass name.
- `.label` (str): Localized UI name.
- `.name` (str): MAXScript name, as create() and classOf take it.
- `.nodes() -> list[_mx.Node]`: Scene nodes whose object, modifiers, material or controllers use the class.
- `.plugin` (_mx.Plugin | None): The plugin DLL that defines the class, or None.
- `.schema(*fields, query: str = '', offset: int = 0, limit: int = 256) -> object`: Plugin inspector metadata: property refs, labels, units, domains, coverage: schema('radius').
- `.superclass` (str): MAXScript superclass name: GeometryClass, Modifier, ...
- `.superclass_id` (int)

### Controller

A track's controller: track.controller. c.params holds its parameters (Noise frequency, constraint relative, ...).

- `.add_target(target: object, weight: float | None = None, frame: float | None = None, name: str | None = None) -> None`: Add a target: a node with weight= (position, orientation, look-at, path constraints), with frame= (link), the attachment node, or a named input of a script (...
- `.class_name` (str): MAXScript class name: Bezier_Float, Float_Script, Position_Constraint, ...
- `.expression` (object, settable): Script or expression text, None for other controllers. Setting it is unchecked; track.apply_script() validates first.
- `.kind` (str): float, point3, point4, position, rotation, scale or transform.
- `.layers` (list[_mx.Controller]): A list controller's layers, first to last; [] for other controllers.
- `.params` (_mx.Params): The controller's parameters and MAXScript properties.
- `.targets` (list): Constraint targets: [(node, weight)], [(node, frame)] for a link constraint, [node] for attachment.
- `.variables` (object): Script inputs [{name, kind, value, offset}] (offset in frames), or expression inputs [{name, kind}].

### Effect

An atmospheric or render effect: fx.active = False; fx.params.density = 20.

- `.active` (bool, settable): Whether the effect renders.
- `.class_name` (str): MAXScript class: VolumeFog, Blur, Lens_Effects, ...
- `.delete() -> None`: Remove the effect from the scene.
- `.index` (int): 1-based position in its list.
- `.kind` (str): 'atmospheric' (Rendering > Environment) or 'render' (Rendering > Effects).
- `.name` (str): Name in the Environment or Effects list.
- `.nodes` (list[_mx.Node]): Gizmo nodes the effect uses (atmospheric apparatus); empty for most render effects.
- `.params` (_mx.Params): The effect's parameters.

### Group

A group: a Node whose members move with it. Group(node) wraps an existing group head (Node, name or handle).

- `.add(nodes: object) -> None`: Add nodes to the group: group.add(mx.node('Lamp')).
- `.delete() -> None`: Delete the group with all its members, nested groups included; ungroup() keeps them.
- `.members` (list): Member nodes; a nested group is one member, as a Group.
- `.open` (bool, settable): Whether the group is open, so its members can be picked and edited one by one.
- `.remove(nodes: object) -> None`: Take nodes out of the group; they move to the scene root.
- `.ungroup() -> list`: Dissolve the group (its head is deleted); returns the former members.

### Hit

A pick on the evaluated surface (after modifiers, not the editable base): node, point, normal, distance.

- `.distance` (float): Distance from the eye along the ray.
- `.node` (_mx.Node): The node hit.
- `.normal` (object): World surface normal (x, y, z).
- `.point` (object): World hit point (x, y, z).

### Interface

A function-publishing interface: iface.fn(args) calls a function, iface.prop reads or sets a property. Arguments follow MAXScript: degrees, 1-based indices, enums by name.

- `.call(function: str, *args, **kwargs) -> object`: Call a function by name: iface.call('getLayer', 1).
- `.describe() -> list[str]`: Its properties and function signatures, one per line.
- `.kind` (str): 'core', 'class' (a class's static interface) or 'mixin' (on an object).
- `.name` (str)

### Key

A key: frame, value, in_tangent, out_tangent (a snapshot).

- `.frame` (float)
- `.in_tangent` (str)
- `.out_tangent` (str)
- `.value` (object)

### Knot

A spline knot value: Knot(pos, type, in_vec, out_vec, seg), world space. Handles are absolute positions; seg is the segment leaving the knot.

- `.in_vec` (object, settable): Incoming handle position; kept by bezier and bezier_corner knots, computed for the others.
- `.out_vec` (object, settable): Outgoing handle position; kept by bezier and bezier_corner knots, computed for the others.
- `.pos` (object, settable): World position (x, y, z).
- `.seg` (str | None, settable): line or curve: the segment leaving this knot; None on the last knot of an open spline.
- `.type` (str | None, settable): corner, smooth, bezier or bezier_corner (bounded when creating); None takes the call's default.

### Layer

A scene layer: layer.add(nodes), layer.hidden = True, layer.parent = 'Building'.

- `.add(nodes: object) -> None`: Move nodes onto this layer: layer.add(mx.nodes('Wall*')).
- `.all_edges` (bool, settable): Show every edge, hidden ones included.
- `.backface_cull` (bool, settable): Hide back faces in the viewports.
- `.box_mode` (bool, settable): Display its objects as bounding boxes.
- `.cast_shadows` (bool, settable): Its objects cast shadows.
- `.children` (list[_mx.Layer]): Child layers.
- `.color` (object, settable): Wire color (r, g, b), 0-1.
- `.current` (bool, settable): Whether new objects go on this layer; assign True to make it current.
- `.delete() -> None`: Delete the layer; it must hold no nodes and not be current.
- `.frozen` (bool, settable): Frozen; a frozen parent layer also freezes it.
- `.hidden` (bool, settable): Hidden; a hidden parent layer also hides it.
- `.name` (str, settable): The layer's name; assigning renames it (names are unique, ignoring case).
- `.nodes` (list[_mx.Node]): Nodes on this layer, not on its child layers; mx.select(layer.nodes) selects them.
- `.parent` (_mx.Layer | None, settable): Parent layer or None; assign a Layer, a layer name or None (top level).
- `.primary_visibility` (bool, settable): Visible to the camera.
- `.receive_shadows` (bool, settable): Its objects receive shadows.
- `.renderable` (bool, settable): Whether its objects render.
- `.secondary_visibility` (bool, settable): Visible to reflections and refractions.
- `.trajectory` (bool, settable): Show motion trajectories.
- `.vertex_ticks` (bool, settable): Show vertices as ticks.
- `.xray` (bool, settable): See-through display.

### Light

A light through its renderer's verified provider: light.intensity = (1500, 'lm'), light.kelvin = 4500. Lists: mx.lights(); a node's: node.light.

- `.cast_shadows` (bool | None, settable): Shadow casting; Corona lights and FStorm suns always cast shadows.
- `.color` (object, settable): RGB color (0-1, scene-linear) when color_mode is 'rgb', else None; setting it switches to RGB.
- `.color_mode` (object, settable): 'rgb', 'kelvin', 'texmap', 'realistic' (CoronaSun) or 'physical' (FStorm sun); set only 'realistic' or 'physical'.
- `.enabled` (bool, settable): Whether the light is on (for an environment map: whether it is the active environment).
- `.height` (object, settable): Area size in scene units (rectangle width/height are full extents, disk/sphere/cylinder radius, cylinder length); None when the shape has no such size.
- `.info() -> dict`: Everything decoded about the light, with provider extras and any unreadable parts, as a dict.
- `.intensity` (tuple, settable): (value, unit), set with an explicit unit: light.intensity = (1500, 'lm'). Units per renderer: mx.light_capabilities().
- `.kelvin` (object, settable): Color temperature (500-30000 K) when color_mode is 'kelvin', else None; setting it switches to Kelvin.
- `.kind` (object): 'area', 'point', 'directional' or 'environment'; None when undecoded.
- `.length` (object, settable): Area size in scene units (rectangle width/height are full extents, disk/sphere/cylinder radius, cylinder length); None when the shape has no such size.
- `.node` (_mx.Node | None): The light's node, or None for an environment map.
- `.params` (_mx.Params): Every parameter of the light (or environment map), for what the semantic properties do not cover.
- `.provider` (object): 'photometric', 'vray', 'octane', 'corona' or 'fstorm'; None when no verified provider decodes this class.
- `.radius` (object, settable): Area size in scene units (rectangle width/height are full extents, disk/sphere/cylinder radius, cylinder length); None when the shape has no such size.
- `.shape` (object): Area shape: 'rectangle', 'disk', 'sphere', 'cylinder' (V-Ray also 'mesh'); None otherwise.
- `.width` (object, settable): Area size in scene units (rectangle width/height are full extents, disk/sphere/cylinder radius, cylinder length); None when the shape has no such size.

### LoftMesh

A parametric quad loft's definition, stored on its node; node.loft_mesh.update(width=20) rebuilds the cage.

- `.definition` (object): sections as written (numbers and expressions) and the profile_closed, close_path, caps, reverse options.
- `.node` (_mx.Node)
- `.origin` (tuple): World point the initial sections were centered on; the cage follows the node's transform since.
- `.parameters` (object): The parameters that built the current cage, or None when the cage was edited by hand (update refuses then).
- `.update(**parameters) -> dict`: Rebuild the cage for new parameter values, keeping vertex IDs, transform, modifiers and materials; returns {name: (before, after)} of what changed.

### Macro

A macroscript: name, category, tooltip, text, file, run().

- `.category` (str)
- `.file` (str): Source file.
- `.id` (int)
- `.name` (str)
- `.run() -> object`: Run it, like macros.run; returns its result.
- `.text` (str): Button text.
- `.tooltip` (str)

### Mark

A point in the scene's change journal: seq counts every event, scene_seq only persistent edits.

- `.scene_seq` (int)
- `.seq` (int)

### Material

A material: mat.params.base_color = (1, 0, 0); node.material = mat.

- `.class_name` (str): MAXScript class: PhysicalMaterial, Multimaterial, ...
- `.handle` (int)
- `.maps` (dict): Map slots by parameter name: {'base_color_map': <Texmap>, 'bump_map': None, ...}; set them through params.
- `.name` (str, settable): The material's name.
- `.network(depth: int = 6, max_nodes: int = 80) -> dict`: The map graph: {'tree': lines, 'links': [(parent, slot, child)], 'files', 'issues', 'complete'}; print('\n'.join(net['tree'])).
- `.nodes` (list[_mx.Node]): Scene nodes whose material is this one or contains it.
- `.params` (_mx.Params): The material's parameters.
- `.remap_files(texture_folder: str = '', path_map: object | None = None, allow_missing: bool = False, preview: bool = False) -> list`: Repoint texture files in place, by file name in texture_folder or by path_map {old: new}; returns the changes (preview=True only plans).
- `.replace_with(new: _mx.Material, sub_materials: bool = True) -> dict`: Put new wherever this material is: on nodes and in sub-material slots; returns {'nodes', 'slots', 'skipped'}.
- `.replicate(texture_folder: str = '', path_map: object | None = None, name: str = '', nodes: object | None = None, allow_missing: bool = False) -> _mx.Material`: Copy the material, its texture files repointed to texture_folder or path_map {old: new}, onto nodes: mat.replicate('D:/tex/oak', name='Oak', nodes=['Table']).
- `.set_sub_material(index: int, material: _mx.Material | None) -> None`: Put a material (None empties) in sub-material index, as sub_materials counts: multi.set_sub_material(0, brick) sets ID 1.
- `.sub_materials` (list[_mx.Material | None]): Sub-materials, None where empty; for a Multi/Sub-Object entry i is material ID i+1.

### Modifier

A modifier on a node's stack: m.params.angle = 30, m.enabled = False.

- `.class_name` (str)
- `.delete() -> None`: Remove the modifier from the stack.
- `.enabled` (bool, settable)
- `.name` (str, settable)
- `.node` (_mx.Node)
- `.params` (_mx.Params): The modifier's parameters.

### Node

A scene node, held by handle; NotFound once deleted.

- `.add_modifier(cls: str, name: str = '', **params) -> _mx.Modifier`: Add a modifier on top of the stack: add_modifier('Bend', angle=30, direction=90).
- `.add_spline(points: object, closed: bool = False, knot: str = 'smooth', *, expected: str | None = None) -> _mx.Spline`: Add a spline from world points or Knots to an editable shape (a hole, another outline); returns it.
- `.bbox` (object): World bounding box ((min), (max)) of the evaluated geometry, or None.
- `.boolean(operands: object | None = None, operation: str = 'subtract', *, cutters: object | None = None, repeat: object | None = None, operations: collections.abc.Sequence[str] | None = None, option: str = '', method: str | None = None, voxel_size: float | None = None, live: bool = False, use_operand_material: bool = True, new_modifier: bool = False, modifier_name: str = '') -> _mx.Boolean`: Cut or fuse with a Boolean modifier and return it: wall.boolean(['Door', 'Window']) subtracts both. operation: union, subtract, intersect, merge, attach, ins...
- `.boolean_modifier` (_mx.Boolean | None): The topmost Boolean modifier on the stack, or None.
- `.children` (list[_mx.Node])
- `.class_name` (str): Class of the base object: Sphere, Editable_Poly, ...
- `.clone(mode: str = 'copy', name: str = '', offset: object | None = None) -> _mx.Node`: Copy, instance or reference the node (and its children), moved by offset.
- `.conform_verts(target: object, indices: collections.abc.Sequence[int] | None = None, *, axes: str = 'xyz', axis: str = '', strength: float = 1.0, spline: int = 1, convert: bool = False) -> tuple[int, int]`: Pull base vertices onto a spline (closest point on spline number `spline`) or a surface (ray along axis: x/y/z both ways, +x/-x/... one way, default z); axes...
- `.convert_to_poly() -> _mx.Poly`: Collapse the modifier stack into an Editable Poly (MAXScript convertToPoly) as one undo step; returns node.poly.
- `.convert_to_spline() -> None`: Collapse a shape to an editable spline (convertToSplineShape), discarding its modifier stack.
- `.curve_model(include_definition: bool = False) -> dict`: The node's curve_model recipe: kind, parameters, model_token, whether the geometry still matches it.
- `.curve_token` (str): Token of the base knots, topology and object transform; pass it as expected= so edits refuse a changed shape.
- `.curve_update(parameters: object, *, expected: str | None = None) -> dict`: Change curve_model parameters in place, keeping placement, materials and modifiers: node.curve_update({'bow': 6}, expected=node.curve_model()['model_token']).
- `.delete() -> None`: Delete the node.
- `.dependents` (list[_mx.Node]): Nodes whose references reach this one directly: constraint, look-at and skin users.
- `.exists` (bool): False once the node is deleted.
- `.faces() -> list`: Triangles of the evaluated mesh as 1-based vertex IDs into verts(): mx.create_mesh('Copy', n.verts(), n.faces()) copies it.
- `.get_verts(indices: collections.abc.Sequence[int] | None = None, *, bbox: object | None = None, near: object | None = None, radius: float = 0.0, convert: bool = False) -> dict`: World positions of the Editable Poly base's vertices as {index: (x, y, z)}, 1-based; filter by indices, bbox=((min), (max)) and near=(x, y, z) with radius. c...
- `.group` (_mx.Group | None): The group this node is in, or None.
- `.handle` (int)
- `.hidden` (bool, settable)
- `.inspect() -> dict`: Report: classes, transform, hierarchy, visibility and render flags, wirecolor, instances (self included), mesh, bbox, modifiers, material and the plugin file...
- `.instances` (list[_mx.Node]): Nodes sharing this node's object (instances and references), itself included: instanced when len > 1.
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the node by name: node.interface('INodeBakeProperties').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces of the node itself.
- `.layer` (_mx.Layer, settable): The node's layer; assign a Layer or a layer name to move it: node.layer = 'Walls'.
- `.light` (_mx.Light | None): The node's Light (lights only), or None: node.light.intensity.
- `.loft_mesh` (_mx.LoftMesh | None): The node's parametric loft definition (LoftMesh), or None.
- `.material` (_mx.Material | None, settable): The node's material, or None.
- `.mesh_counts` (object): (vertices, faces) of the evaluated, triangulated mesh, or None.
- `.mesh_qa(limit: int = 12, area_epsilon: float = 0.0) -> dict`: Topology report of the evaluated mesh: boundary, non-manifold and winding-conflict edges, degenerate and duplicate triangles, isolated vertices and edge-conn...
- `.modifiers` (list[_mx.Modifier]): Modifier stack, top first.
- `.move_verts(offset: object, indices: collections.abc.Sequence[int] | None = None, *, falloff: float = 0.0, convert: bool = False) -> tuple[int, int]`: Move base vertices (all by default) by a world offset; falloff > 0 drags neighbours within that distance with (1 - d/falloff)^2 weight. Returns (moved, soft_...
- `.name` (str, settable)
- `.orientation() -> dict`: World orientation: pivot, unit local axes (x, y, z), matrix rows, bbox center and size, pivot_to_center.
- `.params` (_mx.Params): The base object's parameters: node.params.radius = 10.
- `.parent` (_mx.Node | None, settable): Parent node or None; reparenting keeps the world transform.
- `.path` (str): Hierarchy path from the scene root, '/Rig/Camera'; '/' in a name is written '~1' and '~' is '~0'.
- `.poly` (_mx.Poly): The Editable Poly base cage beneath the modifiers (Poly), for component IDs and edits; BadParam for other bases.
- `.pos` (object, settable): World position of the pivot (x, y, z).
- `.references(depth: int = 3) -> dict`: Reference tree below the node: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; 'seen' marks a target listed above, and other nodes are not e...
- `.rot` (object, settable): Rotation track as Euler degrees (x, y, z), relative to the parent; keeps winding past 360.
- `.scale` (object, settable): Scale track (x, y, z), or one number for all axes.
- `.selected` (bool, settable): Whether the node is selected; setting it adds to or removes from the selection.
- `.set_verts(positions: dict, *, convert: bool = False) -> int`: Place base vertices at world positions: node.set_verts({1: (0, 0, 10), 4: (5, 0, 10)}); returns how many.
- `.splines` (list[_mx.Spline]): The splines of an editable spline base (SplineShape or Line).
- `.track(path: str) -> _mx.Track`: Transform track: 'pos', 'pos.x'..'pos.z', 'rot.x'..'rot.z' (degrees), 'scale'.
- `.tracks(filter: str | None = None, depth: int = 6) -> list[_mx.Track]`: Every animatable track under the node as sub-anim path tracks, filtered by a substring of the path or controller class: node.tracks('radius').
- `.verts(world: bool = True) -> list`: Vertex positions of the evaluated, triangulated mesh (after modifiers), world space unless world=False; its IDs are not the cage's.
- `.wires(depth: int = 8) -> list[dict]`: The node's wired tracks: [{track, controller, driven, driver, two_way, links: [{param, node, expression}]}].

### Operand

A Boolean operand (a snapshot): index, name, kind, operation, option, disabled.

- `.disabled` (bool)
- `.index` (int): Flat index in the operand stack; the base object is 1.
- `.kind` (str): 'single' or 'folder'.
- `.name` (str)
- `.operation` (str): union, subtraction, intersection, merge, attach, insert or split.
- `.option` (str): none, imprint (edges only) or cookie (cut without adding faces).

### Params

Parameters by name: p.radius, p['radius'] = 10. Angles in degrees, percentages 0-100, colors 0-1.

- `.describe() -> list[str]`: One line per parameter: name, type, value, +anim when animatable.
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.params.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces of the object these parameters belong to.
- `.keys() -> list[str]`: Parameter names, then MAXScript properties that are not parameters.
- `.patch(schema: dict, all_instances: bool = False, **values) -> dict`: Guarded edit, one undo step, refused if anything inspected changed: p.patch(s, multiplier=2.0). Enums take their names; all_instances=True allows editing a s...
- `.schema(*fields, query: str = '', offset: int = 0, limit: int = 256) -> object`: Plugin inspector report with live values and the schema and state tokens patch() needs: p.schema('multiplier').
- `.to_dict() -> dict`: Every parameter and its value.
- `.track(name: str) -> _mx.Track`: The parameter's animation track.
- `.update(**values) -> None`: Set several parameters: p.update(radius=10, segs=32).

### Plugin

A loaded plugin DLL: name, path, description, classes, nodes().

- `.classes` (list[_mx.ClassInfo]): Classes the plugin defines.
- `.description` (str)
- `.loaded` (bool): False while 3ds Max defers loading it.
- `.name` (str): File name: 'prim.dlo'.
- `.nodes() -> list[_mx.Node]`: Scene nodes that use any of the plugin's classes.
- `.path` (str)

### Poly

An Editable Poly base cage beneath the modifiers: 1-based vertex, edge and face IDs as polyop numbers them; positions are world space unless world=False.

- `.capture(level: str, ids: collections.abc.Sequence[int] | None = None) -> dict`: Capture the AGENT VIEWPORT with up to 100 components labeled at their centers (F12, E3, V7); returns {'file', 'view', 'width', 'height', 'labels'}.
- `.centers(level: str, ids: collections.abc.Sequence[int] | None = None, world: bool = True) -> list`: Centers (mean vertex position) of 'vertex', 'edge' or 'face' components, all or of the given IDs.
- `.counts` (tuple): (vertices, edges, faces), counting deleted slots not yet collapsed, as polyop does.
- `.edges(ids: collections.abc.Sequence[int] | None = None) -> list`: (v1, v2) vertex IDs of each edge, all or of the given IDs.
- `.edit(ops: object, expected: str = '', show: bool = False) -> dict`: Run ops as one undo step, rolled back if one fails: edit([{'op': 'extrude', 'selection': ids, 'amount': 10}], expected=token). Each op has op, level ('vertex...
- `.faces(ids: collections.abc.Sequence[int] | None = None) -> list`: Vertex IDs of each face in winding order, all or of the given IDs.
- `.find(level: str, ids: collections.abc.Sequence[int] | None = None, current: bool = False, bbox: object | None = None, near: object | None = None, radius: float | None = None, normal: object | None = None, angle: float | None = None, boundary: bool | None = None, sharp: float | None = None) -> list`: IDs passing every filter given: among ids, selected (current), world center in bbox or within radius of near, face normal within angle (default 10) of normal...
- `.instances` (list[_mx.Node]): Nodes sharing this base, this one first; an edit changes them all.
- `.node` (_mx.Node): The node whose base this is.
- `.normals(ids: collections.abc.Sequence[int] | None = None, world: bool = True) -> list`: Unit face normals (Newell; (0, 0, 0) for a degenerate face), all or of the given face IDs.
- `.pick(x: float, y: float, view: str, level: str = 'edge', ids: collections.abc.Sequence[int] | None = None, tolerance: float = 0.025, limit: int = 5, prefer_surface: bool = True) -> dict`: Components near image point x, y (0-1, top-left) of the agent viewport capture whose token is view, within tolerance (fraction of the shorter side); ranked b...
- `.token` (str): Hash of the cage and its transform: edit(ops, expected=token) refuses IDs read before the mesh changed.
- `.verts(ids: collections.abc.Sequence[int] | None = None, world: bool = True) -> list`: Vertex positions, all or of the given IDs; None for a deleted slot.

### RenderJob

A production render queued by mx.render(): job.wait() renders it now; else it starts when the script returns.

- `.cancel() -> bool`: Drop the job if it has not started; returns whether it was dropped.
- `.done` (bool): True once the job ended (done, failed or cancelled).
- `.error` (object): Why the render failed, else None.
- `.id` (str)
- `.result` (object): {'status', 'output', 'width', 'height', 'seconds'} once done, else None.
- `.signal_path` (str): Done file written when the job ends; render_automations(action='status', job_id=job.id) reads it outside Max.
- `.status` (str): 'queued', 'rendering', 'done', 'failed' or 'cancelled'.
- `.wait() -> dict`: Render now if still queued and return the result; raises if it failed, was cancelled, or already went to Max.

### Segment

A spline segment snapshot: type (line or curve), world length, and its cubic's control points.

- `.length` (float)
- `.points` (tuple): (start, start handle, end handle, end) in world space; a line's handles lie on its chord.
- `.type` (str)

### SelectionSet

A named selection set, held by name: s.nodes = nodes replaces it; mx.select(s.nodes) selects it.

- `.delete() -> None`: Delete the set; its nodes stay in the scene.
- `.name` (str, settable): The set's name; assigning renames it.
- `.nodes` (list[_mx.Node], settable): Nodes in the set; assigning replaces them.

### Spline

One spline of an editable shape (node.splines[i]). World space, 0-based indices; every edit takes expected=node.curve_token to refuse a shape that changed.

- `.capture(knots: collections.abc.Sequence[int] | None = None) -> dict`: Capture the AGENT VIEWPORT with knots labeled K<i> and handles I<i>/O<i>; returns {'file', 'view', 'width', 'height', 'labels', 'labels_truncated'}; pass 'vi...
- `.close(*, expected: str | None = None) -> None`: Close the spline with a segment from the last knot to the first.
- `.closed` (bool): Whether the spline is closed; change it with close() or open().
- `.delete(*, expected: str | None = None) -> None`: Delete the spline from its shape; later splines move down an index.
- `.delete_knot(index: int, *, expected: str | None = None) -> None`: Delete a knot.
- `.index` (int)
- `.insert(segment: int, param: float = 0.5, *, expected: str | None = None) -> int`: Split a segment at param (0-1, exclusive) with a new knot; returns its index.
- `.knots` (list[_mx.Knot]): The knots as Knot values; edit them with set_knot or set_knots.
- `.length` (float): World length of the spline.
- `.node` (_mx.Node)
- `.open(*, expected: str | None = None) -> None`: Open the spline between its last and first knots.
- `.pick(x: float, y: float, view: str, tolerance: float = 0.025, knots: collections.abc.Sequence[int] | None = None) -> list`: Knots and handles near image point (x, y) of a capture (fractions 0-1), nearest first; view is the capture's view_token. Overlapping front and back handles s...
- `.qa(tolerance: float = 0.01) -> dict`: Sampled QA report: length, planarity, self-intersections, tangent breaks and segments that backtrack.
- `.reverse(*, expected: str | None = None) -> None`: Reverse the knot order, keeping the first knot first.
- `.sample(count: int) -> list[object]`: count points evenly spaced by length along the spline, world space (closed: no repeated end).
- `.segments` (list[_mx.Segment]): The segments as Segment snapshots, segment i leaving knot i.
- `.set_knot(index: int, *, pos: object | None = None, in_vec: object | None = None, out_vec: object | None = None, type: str | None = None, seg: str | None = None, expected: str | None = None) -> None`: Change one knot; a moved knot carries its handles: set_knot(2, pos=(10, 0, 0)), set_knot(2, type='bezier_corner', out_vec=(14, 3, 0)).
- `.set_knots(knots: object, *, expected: str | None = None) -> None`: Rewrite every knot at once from Knots, dicts or points (one per knot); checked before anything is written. ks = s.knots; ks[1].pos = (5, 2, 0); s.set_knots(ks).

### StateSet

A State Set (a snapshot): name, camera, render_range, anim_range, lock_camera_to_range.

- `.anim_range` (tuple[float, float] | None): (start, end) animation frames, or None.
- `.camera` (_mx.Node | None): The state set's camera, or None.
- `.lock_camera_to_range` (bool): Whether the camera's animation is locked to the range.
- `.name` (str)
- `.render_range` (tuple[float, float] | None): (start, end) render frames, or None.

### Texmap

A texture map: tex.params.filename = 'C:/tex/wood.png'; mat.params.base_color_map = tex.

- `.class_name` (str): MAXScript class: Bitmaptexture, Noise, OSLMap, ...
- `.handle` (int)
- `.maps` (dict): Sub-map slots by parameter name: {'map1': <Texmap>, 'mask': None}; set them through params.
- `.name` (str, settable): The map's name.
- `.nodes` (list[_mx.Node]): Scene nodes whose material uses this map.
- `.params` (_mx.Params): The map's parameters.

### Track

An animation track: node.track('pos.z'), params.track('radius'), node.tracks()[i]. Node paths: 'pos', 'pos.x'..'pos.z', 'rot' and 'rot.x'..'rot.z' (degrees), 'scale', 'transform', a sub-anim path '[#Transform][#Position]', or a path plus steps: 'pos[2]' is a list controller's 2nd layer. Key edits take frames=[...] and/or start=/end=.

- `.apply_script(source: str, expected: str, bindings: object | None = None, frames: collections.abc.Sequence[float] | None = None) -> dict`: Validate, then replace the controller with the script controller, sampling it in place; restores the old controller on failure. expected is expected_controll...
- `.assign(cls: object, layer: bool = False, **params) -> _mx.Controller`: Give the track a new controller of class cls (a name within the track's kind, or a Controller to share) and set its parameters; layer=True adds it on top of...
- `.bake(start: float | None = None, end: float | None = None, step: float = 1.0) -> int`: Replace start..end (default: the timeline) with a key every step frames of the evaluated value; a script, constraint, wire, noise or list controller becomes...
- `.clear() -> None`: Delete every key; the value at the current frame stays.
- `.controller` (_mx.Controller | None): The track's Controller, or None for a parameter that is not animated.
- `.delete_keys(frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Delete the keys at frames or in start..end; returns how many: delete_keys(start=10, end=20).
- `.inspect_script(frames: collections.abc.Sequence[float] | None = None) -> dict`: The track's script controller: {track, controller, expected_controller, script, variables, throw_on_error}, and samples [(frame, value)] when frames is given.
- `.key(frame: float, value: object | None = None, tangent: str | None = None, in_tangent: str | None = None, out_tangent: str | None = None) -> None`: Set a key at frame (value None keys the current value). tangent: smooth, linear, step, fast, slow, auto.
- `.keys` (list[_mx.Key]): The keys, in time order.
- `.loop(start: float | None = None, end: float | None = None) -> None`: Close a cycle: key at end the value at start (default first and last key) and repeat it outside (ort cycle).
- `.match(source: float | None = None, target: float | None = None) -> object`: Key at target the value at source (default: the first key onto the last); returns the new value.
- `.move_keys(offset: float, frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Shift keys by offset frames (all keys by default); refuses to land on another key. Returns how many moved.
- `.ort` (object, settable): Out-of-range types (before, after), None if not animated; set one name or a pair: constant, cycle, loop, ping_pong, linear, relative_repeat.
- `.path` (str): The track's path, or its parameter's name.
- `.resample(start: float | None = None, end: float | None = None, step: float = 1.0) -> int`: Add a key every step frames in start..end (default: the timeline), keeping the curve; returns keys per axis.
- `.scale_keys(factor: float, pivot: float | None = None, frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Scale key times around pivot (default start, else the first key): scale_keys(2.0) doubles the timing.
- `.tangents(tangent: str | None = None, in_tangent: str | None = None, out_tangent: str | None = None, frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Set Bezier tangent types (all keys by default); returns how many keys changed: tangents('smooth', start=0, end=50).
- `.tcb(tension: float | None = None, continuity: float | None = None, bias: float | None = None, ease_to: float | None = None, ease_from: float | None = None, frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Set TCB key settings, 0-50 as in MAXScript (all keys by default); returns how many keys changed.
- `.unwire() -> None`: Disconnect this driven track's wire; it keeps a plain controller holding its current value.
- `.validate_script(source: str, bindings: object | None = None, frames: collections.abc.Sequence[float] | None = None) -> dict`: Check a script on a copy of the controller, off the track, at frames (default the current one): {expected_controller, output_type, samples, applied=False}. b...
- `.value(frame: float | None = None) -> object`: The track's value at frame (default: the current one).
- `.wire(driver: _mx.Track, expression: str, reverse: str | None = None) -> _mx.Controller`: Drive this track from driver (a Track) through expression, which names the driver by its sub-anim name: angle.wire(radius, 'Radius * 2'). reverse makes it tw...

### Viewport

A viewport, resolved on every call: the user's active view, or the AGENT VIEWPORT.

- `.capture(method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: Save the view as it is to a PNG; method='window' grabs the window instead of the Nitrous buffer.
- `.capture_frames(frames: object, view: object = 'current', subject: object | None = None, shading: str | None = None, edges: bool | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: One view over time in one strip: a frame count spread over the range or up to 12 frame numbers; a named view is framed over all of them; the time slider come...
- `.capture_views(views: object | None = None, subject: object | None = None, shading: str | None = None, edges: bool | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: Up to 8 framed views in one grid (default front, right, back, top; also perspective, three_quarter, current, (x, y, z) eye directions); subject frames one hi...
- `.edges` (bool): Whether edged faces show.
- `.eye` (object): Eye position (x, y, z).
- `.fov` (float | None): Horizontal field of view in degrees; None for orthographic views.
- `.set(view: str = 'perspective', eye: object | None = None, target: object | None = None, frame: object | None = None, fov: float = 45.0, padding: float = 1.15, shading: str = 'smooth', edges: bool = False, grid: bool = False) -> None`: Set the whole view, defaults included: view (perspective, orthographic, front, back, left, right, top, bottom), eye/target, frame (nodes to fit), fov, shadin...
- `.shading` (str): Visual style: smooth, flat, wireframe, clay, realistic, facets, hidden_line, ...
- `.size` (tuple[int, int]): (width, height) in pixels.
- `.target` (object): The point the view looks at and orbits: (x, y, z).
- `.type` (str): perspective, orthographic, front, back, left, right, top, bottom, camera, light or other.

## Errors

- `Ambiguous`: A name matches several nodes or classes; pass a handle.
- `BadParam`: A value has the wrong type or 3ds Max refused it.
- `MaxError`: Base of every _mx error.
- `NotFound`: A node, class or object does not exist.
- `UnknownParam`: No parameter of that name.
- `WrongThread`: Called off 3ds Max's main thread.

## Values

- `BUILD` = 'b20261109192948'
- `MAX_VERSION` = 2027
