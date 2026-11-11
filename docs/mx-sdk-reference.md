# sdk (_mx) reference

3ds Max for agents, bound from the SDK: the scene's nodes, parameters, modifiers, materials and animation, and modeling, viewport, file and plugin work on them. Lights, cameras and renders are mx.render_setup, the same on every renderer.

Build `b20261111215429`, 3ds Max SDK 2027.

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

What this 3ds Max has: its version, the renderer in use and those installed, which known plugins are installed (plugins: forest_pack, forest_lite, tyflow, railclone, phoenix_fd, as bools) and class counts. Keys: {'max_version', 'renderer', 'renderers', 'plugins', 'material_classes', 'geometry_classes', 'modifier_classes'}.

### `capture_isolated(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None) -> list[_mx.Capture]`

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

### `contacts(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str], against: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None, tolerance: float = 0.0, near_gap: float = 0.0, include_separate: bool = False) -> dict`

Evaluated meshes that penetrate, intersect, touch or nearly touch, worst first: contacts(legs, against=[floor, seat]) checks each leg against those only. tolerance and near_gap default to 0.1 mm and 10 mm in scene units; depth is a lower and gap an upper bound, both from vertices. Nodes without a mesh are listed in 'skipped'. pairs are {'a', 'b', 'status', 'depth', 'gap', ...} and summary counts them by status. Keys: {'tolerance', 'near_gap', 'summary', 'pairs', 'open_meshes', 'skipped'}.

### `create(cls: str, name: str = '', pos: _mx.Point3 | None = None, **params) -> _mx.Node`

Create an object (geometry, shape, helper, light, camera) with parameters: create('Sphere', 'Ball', pos=(0, 0, 10), radius=10, segs=32).

### `create_mesh(name: str, verts: object, faces: object) -> _mx.Node`

Create an Editable Poly from world vertices and polygons of 1-based vertex IDs, counterclockwise seen from outside, as one undo step: create_mesh('Tri', [(0, 0, 0), (10, 0, 0), (0, 10, 0)], [(1, 2, 3)]).

### `curve_create(definition: object, name: str = '', parameters: object | None = None) -> _mx.Node`

Build a curve_model definition as an editable spline or quad cage centered on its bounds, saving the recipe for node.curve_update.

### `curve_preview(definition: object, parameters: object | None = None) -> dict`

Evaluate a curve_model definition without touching the scene: counts, bounds and per-curve QA. definition={'curves': {name: curve}, 'output': {'kind': 'curve' | 'sweep' | 'loft', ...}}. Keys: {'kind', 'curves', 'alignment', 'bounds'?, 'notes', 'counts'}.

### `delete(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`

Delete a node or a list of nodes; an empty list deletes nothing.

### `effects(kind: str | None = None) -> list[_mx.Effect]`

Atmospherics then render effects, or one kind: effects('atmospheric').

### `fetch() -> None`

Restore what hold() held (Edit > Fetch), dropping the changes since; refuses a hold made elsewhere or for another scene.

### `file_info(path: str | os.PathLike | collections.abc.Sequence[str | os.PathLike], objects: bool = False, classes: bool = False) -> object`

A .max file's metadata without opening it: {'path', 'size', 'created', 'modified' (UTC), 'title', 'author', ...}, plus 'objects' (names) and 'classes' (plugin classes by kind) when asked. A list of paths gives a list of reports; an unreadable file reports its 'error'.

### `find(pattern: str | None = None, cls: str | None = None, layer: str | None = None, roots_only: bool = False, uses: str | None = None, **props) -> list[_mx.Node]`

Nodes matching every filter: find('Wall*', cls='Box', layer='Walls', uses='TurboSmooth', hidden=False, height=50). Keywords compare node flags (hidden, frozen, renderable, cast_shadows, material, ...) or base object parameters; numbers within 1e-4, text ignoring case.

### `frame() -> float`

Current frame.

### `group(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str], name: str = '') -> _mx.Group`

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

### `material_library() -> list[_mx.Material | _mx.Texmap]`

The current material library (currentMaterialLibrary): Materials and Texmaps.

### `materials(pattern: str | None = None) -> list[_mx.Material]`

Materials assigned to scene nodes, optionally filtered by a wildcard name: materials('Brick*').

### `maxscript(code: str) -> str`

Evaluate MAXScript; returns the result as text.

### `medit_materials() -> list[_mx.Material | _mx.Texmap | None]`

The 24 Compact Material Editor slots (meditMaterials), None where empty.

### `merge(path: str | os.PathLike, names: str | collections.abc.Sequence[str] | None = None, duplicates: str = 'rename', materials: str = 'rename') -> list[_mx.Node]`

Merge a .max file's objects (all, or names) and return the new nodes. Name clashes: duplicates='rename', 'skip', 'merge' (keep both) or 'delete_old'; materials='rename', 'scene' or 'merged'.

### `native(type: str, **args) -> object`

Run one of the bridge's handlers in-process: native('scene_delta', ...). Returns its reply as Python values.

### `node(key: _mx.Node | str | int) -> _mx.Node`

Node by name, handle or Node; NotFound or Ambiguous otherwise.

### `nodes(pattern: str | None = None) -> list[_mx.Node]`

Scene nodes, optionally filtered by a wildcard name: nodes('Wall*').

### `osl(code: str, name: str = '', **params) -> _mx.Texmap`

Compile OSL code into an OSLMap and set its shader parameters; name defaults to the shader's.

### `osl_info(shader: str) -> dict`

Compile an OSL shader (a .osl path or a name in 3ds Max's OSL folder) in a temporary map: params as describe() lines and outputs as (name, type). Keys: {'shader', 'path', 'params', 'outputs'}.

### `overview() -> dict`

Scene summary: object, hidden, frozen and root counts, nodes per class, material and modifier counts, layers in use. class_counts, materials and modifiers are {name: count}. Keys: {'object_count', 'hidden_count', 'frozen_count', 'root_count', 'class_counts', 'materials', 'modifiers', 'layers'}.

### `patch(ops: object, expected: object | None = None, dry_run: bool = False, label: str = 'MCP Scene Patch') -> object`

Validate, then apply node edits as one undo step, all or nothing: patch([{'op': 'rename', 'target': n, 'name': 'Wall'}, {'op': 'transform', 'target': 'Box001', 'move': (0, 0, 10)}]). Ops: rename (name); transform (relative move, rotate in degrees, scale; coordinate_system 'world' or 'local'); set_flags (hidden, frozen, renderable, cast_shadows, receive_shadows); set_parent (parent: a node or None, keep_transform). expected=mark() taken earlier (or its scene_seq) refuses a scene changed since; dry_run=True validates only. Returns a report.

### `plugin_patch(edits: object, all_instances: bool = False) -> dict`

Patch several objects in one guarded undo step: plugin_patch([(s1, {'multiplier': 2}), (s2, {'size': 5})]). properties lists {'owner', 'name', 'before', 'value'} per edit. Keys: {'status', 'verification', 'transaction', 'properties'}.

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

The open scene: its file (None while untitled), whether it has unsaved changes, its object count and the faces of the evaluated meshes, triangulated. Keys: {'file', 'unsaved', 'objects', 'faces'}.

### `scene_patterns() -> dict`

How the scene combines classes: classes, modifier stacks and material, modifier and texmap pairings, by 3ds Max's display names. Every entry but total_nodes is {pattern: count}, most common first. Keys: {'total_nodes', 'geometry_classes', 'material_classes', 'modifier_classes', 'texmap_classes', 'modifier_stacks', 'material_on_geometry', 'modifier_on_geometry', 'texmap_in_material'}.

### `scene_qa(checks: object | None = None, nodes: object | None = None, fix: object | None = None, dry_run: bool = False, expected: object | None = None, far_from_origin: float | None = None) -> object`

Scan scene hygiene (names, transforms, hierarchy, groups, timeline; no mesh checks) or repair it: scene_qa(fix=['name_collisions', 'empty_names']). nodes= limits the scope; expected= guards a fix as in patch().

### `search_max_files(folder: str | os.PathLike, pattern: str = '*', recursive: bool = True) -> list`

The .max files under folder with objects named like pattern: [{'path', 'objects', 'names'}], search_max_files('D:/lib', 'chair*'); then merge(hit['path'], hit['names']).

### `select(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str], add: bool = False) -> None`

Select nodes, replacing the selection unless add=True; select([]) clears it.

### `selection() -> list[_mx.Node]`

Selected nodes.

### `selection_set(name: str, nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None) -> _mx.SelectionSet`

Named selection set by name; with nodes, create or replace it: selection_set('Lights', mx.nodes('Light*')).

### `selection_sets() -> list[_mx.SelectionSet]`

Every named selection set.

### `set_frame(frame: float) -> None`

Move the time slider.

### `smart_import(folder: str | os.PathLike, texture_folder: str | os.PathLike | None = None, recursive: bool = True, grid_spacing: float = 200.0, lod_filter: str = 'lod0', name_pattern: str = '', exclude_pattern: str = '') -> dict`

Import the mesh files under folder onto an XY grid, pairing each with its textures by name: {'imported': [{'path', 'name', 'nodes', 'textures': {channel: path}, 'material_name', 'mtlx'}], 'skipped', 'failed', 'lod_skipped', 'filtered_out'}. Skips a file when an object named like its stem is in the scene. Keys: {'imported', 'skipped', 'failed', 'lod_skipped', 'filtered_out'}.

### `spline(points: object, name: str = '', closed: bool = False, knot: str = 'smooth', center_pivot: bool = False) -> _mx.Node`

Create an editable spline from world points, dicts or Knots: spline([(0, 0, 0), (40, 0, 0), (40, 30, 0)], 'Rail', knot='corner'). knot: corner, smooth, bezier, bezier_corner or bounded (short handles that do not overshoot).

### `state_sets() -> list[_mx.StateSet]`

The top-level State Sets with their cameras and ranges (snapshots).

### `texmap(cls: str, name: str = '', **params) -> _mx.Texmap`

Create a texture map with parameters: texmap('Bitmaptexture', 'Wood', filename='C:/tex/wood.png').

### `texmap_classes(pattern: str | None = None) -> list[str]`

Texture map class names texmap() takes: texmap_classes('*bitmap*').

### `timeline(start: float | None = None, end: float | None = None, fps: int | None = None) -> tuple[float, float, int]`

Animation range and frame rate: (start, end, fps); pass any to change it.

### `undo() -> str | None`

Undo the last scene operation; returns the undo entry's name ('' if unnamed), or None when nothing was undone. In a run_python script, what the script changed so far becomes its own undo step first, so undo() undoes that, or the step before the script when it has changed nothing yet.

### `viewport(source: str = 'auto') -> object`

The AGENT VIEWPORT while it is open, else the user's active view; source='active' or 'agent' picks one: viewport().capture().

## Classes

### Action

An action item (menu command, shortcut target): text, description, run().

- `.category` (str): Category text, as the Customize User Interface dialog groups it.
- `.description` (str): Description text, as the Customize User Interface dialog shows it.
- `.id` (int): The action's command ID within its table.
- `.macro` (_mx.Macro | None): The macroscript it runs, or None.
- `.run() -> None`: Run it, like actionMan.executeAction.
- `.table` (_mx.ActionTable): The ActionTable it belongs to.
- `.text` (str): Menu text.

### ActionTable

An action table: name, id, actions(pattern).

- `.actions(pattern: str | None = None) -> list[_mx.Action]`: Its actions, by wildcard menu text, description or category.
- `.context_id` (int): The ActionContextId the table belongs to.
- `.id` (int): The table's ActionTableId.
- `.name` (str): The table's name, as action_tables(pattern) matches it.

### AgentViewport

The agent's floating AGENT VIEWPORT: navigate, pick and capture without touching the user's views.

- `.capture(crop: object | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: Save this view, or the VFB its V-Ray/Corona preview renders into, to an image; crop=(x, y, w, h) in VFB client pixels.
- `.frame(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None, padding: float = 1.15) -> None`: Fit nodes and their descendants (default: visible geometry, or the lights and cameras of a rig-only scene).
- `.minimize() -> None`: Park the window; restore() it before navigating or capturing.
- `.orbit(yaw: float = 0.0, pitch: float = 0.0) -> None`: Orbit the target by yaw and pitch degrees: orbit(30, 10).
- `.pan(x: float = 0.0, y: float = 0.0) -> None`: Move the view and its target by x, y scene units in the view plane.
- `.pick(x: float, y: float, capture: _mx.Capture | None = None) -> _mx.Hit | None`: The nearest surface under image point x, y (0-1 from the top-left) as a Hit, or None; capture refuses a view that changed since: pick(0.5, 0.4, cap).
- `.project(points: object, capture: _mx.Capture | None = None) -> list`: Image positions (x, y, depth) of world points, x and y 0-1 from the top-left as pick takes them; None behind the eye; no occlusion test: project([(0, 0, 50)]...
- `.ray(x: float, y: float, capture: _mx.Capture | None = None) -> tuple[_mx.Point3, _mx.Point3]`: World ray (origin, direction) through image point x, y (0-1 from the top-left).
- `.release() -> None`: Stop any preview, hide the window and give the panel back; agent_viewport() opens it again.
- `.render(mode: str, renderer: str = 'activeshade') -> None`: Interactive preview here: activeshade, vray_ipr, vray_vfb, corona_vfb, or shaded to stop; previews are progressive and block pick/ray/project until shaded: r...
- `.render_mode` (str): The preview: shaded, activeshade, vray_ipr, vray_vfb or corona_vfb.
- `.restore() -> None`: Show the minimized window again and redraw it.
- `.window_state` (str): visible, minimized, hidden, offscreen, unavailable, or closed once released.
- `.zoom(factor: float) -> None`: Move toward the target (factor < 1) or away (> 1); orthographic views scale.

### AnimateMode

What animate(on) returns: inside `with sdk.animate():`, edits set keys (Auto Key); animate(False) turns keying off for the block. The mode before the block comes back after it.


### AtTime

What at(frame) returns: inside `with sdk.at(10):`, reads and edits happen at that frame instead of the time slider's.


### Boolean

A Boolean modifier (BooleanMod), a Modifier with an operand stack: b.operands, b.set_operand(2, operation='union').

- `.extract_operand(index: int) -> _mx.Node`: Copy operand `index` back out as a new scene node and return it; the operand stays.
- `.operands` (list[_mx.Operand]): Operands in stack order (the base object left out); index is what set_operand and friends take.
- `.remove_operand(index: int) -> None`: Delete operand `index` from the stack; later indices shift down.
- `.set_operand(index: int, *, operation: str | None = None, option: str | None = None, name: str | None = None, disabled: bool | None = None) -> None`: Retune operand `index`: b.set_operand(2, operation='union', option='imprint', name='Door', disabled=False).

### Box

An axis-aligned box as (min, max), two Point3: node.bbox. It is a tuple, so low, high = node.bbox unpacks it.

- `.center` (_mx.Point3): The middle of the box.
- `.contains(point: _mx.Point3) -> bool`: Whether a point lies inside the box or on its faces.
- `.max` (_mx.Point3): The corner with the largest x, y and z.
- `.min` (_mx.Point3): The corner with the smallest x, y and z.
- `.size` (_mx.Point3): Its extent along x, y and z.

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

- `.details` (object): What else the event recorded, as a dict, or None.
- `.name` (str): The node's name when it happened; '' for an event without a node.
- `.node` (_mx.Node | None): The node the event is about, or None; a deleted node's raises NotFound when used.
- `.scene_seq` (int): The scene's edit count once this event had happened.
- `.seq` (int): The event's number in the journal; pass a Mark or this to changes(since).
- `.type` (str): What happened: 'added', 'deleted', 'name_changed', 'selection_changed', ...

### ClassInfo

A plugin class: name, kind, plugin, describe(), interfaces, schema(), nodes.

- `.category` (str): Create-panel category.
- `.class_id` (tuple[int, int]): Class_ID as (a, b), like MAXScript's classID.
- `.creatable` (bool): Whether scripts can create it (public and not marked no-create).
- `.describe() -> list[str]`: Header plus one line per parameter with its default, range, {named choices}, "UI label" and +anim (MAXScript units); subanims (gizmo, center) and MAXScript p...
- `.interfaces` (list[_mx.Interface]): Interfaces the class publishes; static ones are callable, mixins need an object.
- `.kind` (str): 'geometry', 'modifier', 'material', ... or the MAXScript superclass name.
- `.label` (str): Localized UI name.
- `.name` (str): MAXScript name, as create() and classOf take it.
- `.nodes` (list[_mx.Node]): Scene nodes whose object, modifiers, material or controllers use the class.
- `.plugin` (_mx.Plugin | None): The plugin DLL that defines the class, or None.
- `.schema(*fields, query: str = '', offset: int = 0, limit: int = 256) -> object`: Plugin inspector metadata: property refs, labels, units, domains, coverage: schema('radius').
- `.superclass` (str): MAXScript superclass name: GeometryClass, Modifier, ...
- `.superclass_id` (int): The superclass's SClass_ID, like MAXScript's superClassID.

### Color

A color (r, g, b), each 0-1: layer.color. It is a tuple, and any (r, g, b) passes where a Color is asked for.

- `.b` (float): Blue, 0-1.
- `.g` (float): Green, 0-1.
- `.hex` (str): The color as '#rrggbb', each channel clipped to 0-1.
- `.r` (float): Red, 0-1.

### Controller

A track's controller: track.controller. c.params holds its parameters (Noise frequency, constraint relative, ...).

- `.add_target(target: _mx.Node | _mx.Track | tuple[_mx.Track, float] | _mx.Controller | float | int | bool | str | _mx.Point3 | tuple[float, float, float, float], weight: float | None = None, frame: float | None = None, name: str | None = None) -> None`: Add a target: a node with weight= (position, orientation, look-at, path constraints), with frame= (link), the attachment node, or a named input of a script (...
- `.class_info` (_mx.ClassInfo): The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what the class takes, x.class_info.plugin the DLL it comes from.
- `.class_name` (str): MAXScript class name: Bezier_Float, Float_Script, Position_Constraint, ...
- `.exists` (bool): False once the object is gone: deleted, or off the stack or list it was read from.
- `.expression` (str | None, settable): Script or expression text, None for other controllers. Setting it is unchecked; track.apply_script() validates first.
- `.handle` (int): The object's animatable handle. In pymxs: rt.getAnimByHandle(x.handle).
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces the object publishes.
- `.kind` (str): float, point3, point4, position, rotation, scale or transform.
- `.layers` (list[_mx.Controller]): A list controller's layers, first to last; [] for other controllers.
- `.params` (_mx.Params): The controller's parameters and MAXScript properties.
- `.references(depth: int = 3) -> dict`: Reference tree below the object: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; 'references' holds the entries below while depth lasts and...
- `.targets` (list[tuple[_mx.Node | None, float]] | list[tuple[_mx.Node | None, int]] | list[_mx.Node]): Constraint targets: [(node, weight)], [(node, frame)] for a link constraint, [node] for attachment; [] for other controllers.
- `.variables` (list[dict[str, str | float | int | bool | _mx.Point3 | _mx.Node | _mx.Controller | None]]): Script inputs [{name, kind, value, offset}] (offset in frames), or expression inputs [{name, kind}]; [] for other controllers.

### Effect

An atmospheric or render effect: fx.active = False; fx.params.density = 20.

- `.active` (bool, settable): Whether the effect renders.
- `.class_info` (_mx.ClassInfo): The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what the class takes, x.class_info.plugin the DLL it comes from.
- `.class_name` (str): MAXScript class: VolumeFog, Blur, Lens_Effects, ...
- `.delete() -> None`: Remove the effect from the scene.
- `.exists` (bool): False once the object is gone: deleted, or off the stack or list it was read from.
- `.handle` (int): The object's animatable handle. In pymxs: rt.getAnimByHandle(x.handle).
- `.index` (int): 1-based position in its list.
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces the object publishes.
- `.kind` (str): 'atmospheric' (Rendering > Environment) or 'render' (Rendering > Effects).
- `.name` (str): Name in the Environment or Effects list.
- `.nodes` (list[_mx.Node]): Gizmo nodes the effect uses (atmospheric apparatus); empty for most render effects.
- `.params` (_mx.Params): The effect's parameters.
- `.references(depth: int = 3) -> dict`: Reference tree below the object: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; 'references' holds the entries below while depth lasts and...

### Group

A group: a Node whose members move with it. Group(node) wraps an existing group head (Node, name or handle).

- `.add(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Add nodes to the group: group.add(mx.node('Lamp')).
- `.delete() -> None`: Delete the group with all its members, nested groups included; ungroup() keeps them.
- `.members` (list[_mx.Node]): Member nodes; a nested group is one member, as a Group.
- `.open` (bool, settable): Whether the group is open, so its members can be picked and edited one by one.
- `.remove(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Take nodes out of the group; they move to the scene root.
- `.ungroup() -> list[_mx.Node]`: Dissolve the group (its head is deleted); returns the former members.

### Hit

A pick on the evaluated surface (after modifiers, not the editable base): node, point, normal, distance.

- `.distance` (float): Distance from the eye along the ray.
- `.node` (_mx.Node): The node hit.
- `.normal` (_mx.Point3): World surface normal (x, y, z).
- `.point` (_mx.Point3): World hit point (x, y, z).

### Interface

A function-publishing interface: iface.fn(args) calls a function, iface.prop reads or sets a property. Arguments follow MAXScript: degrees, 1-based indices, enums by name.

- `.call(function: str, *args, **kwargs) -> object`: Call a function by name: iface.call('getLayer', 1).
- `.describe() -> list[str]`: Its properties and function signatures, one per line.
- `.kind` (str): 'core', 'class' (a class's static interface) or 'mixin' (on an object).
- `.name` (str): The interface's name, as interface(name) takes it.

### Key

A key: frame, value, in_tangent, out_tangent (a snapshot).

- `.frame` (float): The key's time, in frames.
- `.in_tangent` (str): Incoming tangent type: smooth, linear, step, fast, slow, auto or custom; '' on a controller without tangents.
- `.out_tangent` (str): Outgoing tangent type, named like in_tangent.
- `.value` (float | _mx.Point3 | tuple[_mx.Point3, _mx.Point3, _mx.Point3, _mx.Point3] | None): The key's value, as the track's value() gives it at the key: a number, a Point3 (a rotation's Euler degrees), four Point3 rows on a transform track, None on...

### Knot

A spline knot value: Knot(pos, type, in_vec, out_vec, seg), world space. Handles are absolute positions; seg is the segment leaving the knot.

- `.in_vec` (_mx.Point3 | None, settable): Incoming handle position; kept by bezier and bezier_corner knots, computed for the others.
- `.out_vec` (_mx.Point3 | None, settable): Outgoing handle position; kept by bezier and bezier_corner knots, computed for the others.
- `.pos` (_mx.Point3, settable): World position (x, y, z).
- `.seg` (str | None, settable): line or curve: the segment leaving this knot; None on the last knot of an open spline.
- `.type` (str | None, settable): corner, smooth, bezier or bezier_corner (bounded when creating); None takes the call's default.

### Layer

A scene layer: layer.add(nodes), layer.hidden = True, layer.parent = 'Building'.

- `.add(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Move nodes onto this layer: layer.add(mx.nodes('Wall*')).
- `.all_edges` (bool, settable): Show every edge, hidden ones included.
- `.backface_cull` (bool, settable): Hide back faces in the viewports.
- `.box_mode` (bool, settable): Display its objects as bounding boxes.
- `.cast_shadows` (bool, settable): Its objects cast shadows.
- `.children` (list[_mx.Layer]): Child layers.
- `.color` (_mx.Color, settable): Wire color (r, g, b), 0-1.
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

### LoftMesh

A parametric quad loft's definition, stored on its node; node.loft_mesh.update(width=20) rebuilds the cage.

- `.definition` (object): sections as written (numbers and expressions) and the profile_closed, close_path, caps, reverse options.
- `.node` (_mx.Node): The node that carries this loft.
- `.origin` (_mx.Point3): World point the initial sections were centered on; the cage follows the node's transform since.
- `.parameters` (object): The parameters that built the current cage, or None when the cage was edited by hand (update refuses then).
- `.update(**parameters) -> dict`: Rebuild the cage for new parameter values, keeping vertex IDs, transform, modifiers and materials; returns {name: (before, after)} of what changed.

### Macro

A macroscript: name, category, tooltip, text, file, run().

- `.category` (str): The category it is filed under.
- `.file` (str): Source file.
- `.id` (int): Its ID in this 3ds Max session's macro directory.
- `.name` (str): The macroscript's name, as macro(name) and macros.run take it.
- `.run() -> object`: Run it, like macros.run; returns its result.
- `.text` (str): Button text.
- `.tooltip` (str): Tooltip text.

### Mark

A point in the scene's change journal: seq counts every event, scene_seq only persistent edits.

- `.scene_seq` (int): Count of persistent edits only; what expected= compares.
- `.seq` (int): Count of every journal event so far, selection changes included.

### Material

A material: mat.params.base_color = (1, 0, 0); node.material = mat.

- `.class_info` (_mx.ClassInfo): The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what the class takes, x.class_info.plugin the DLL it comes from.
- `.class_name` (str): MAXScript class: PhysicalMaterial, Multimaterial, ...
- `.exists` (bool): False once the object is gone: deleted, or off the stack or list it was read from.
- `.handle` (int): The material's animatable handle. In pymxs: rt.getAnimByHandle(mat.handle).
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces the object publishes.
- `.maps` (dict[str, _mx.Texmap | list[_mx.Texmap | None] | None]): Map slots by parameter name: {'base_color_map': <Texmap>, 'bump_map': None, ...}; set them through params.
- `.name` (str, settable): The material's name.
- `.network(depth: int = 6, max_nodes: int = 80) -> dict`: The map graph: tree is lines to print (print('\n'.join(net['tree']))), links [(parent, slot, child)], files [{'map', 'param', 'path', 'exists'}], and truncat...
- `.nodes` (list[_mx.Node]): Scene nodes whose material is this one or contains it.
- `.params` (_mx.Params): The material's parameters.
- `.references(depth: int = 3) -> dict`: Reference tree below the object: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; 'references' holds the entries below while depth lasts and...
- `.remap_files(texture_folder: str = '', path_map: object | None = None, allow_missing: bool = False, preview: bool = False) -> list`: Repoint texture files in place, by file name in texture_folder or by path_map {old: new}; returns the changes (preview=True only plans).
- `.replace_with(new: _mx.Material, sub_materials: bool = True) -> dict`: Put new wherever this material is: on nodes and in sub-material slots. Keys: {'nodes', 'slots', 'skipped'}.
- `.replicate(texture_folder: str = '', path_map: object | None = None, name: str = '', nodes: object | None = None, allow_missing: bool = False) -> _mx.Material`: Copy the material, its texture files repointed to texture_folder or path_map {old: new}, onto nodes: mat.replicate('D:/tex/oak', name='Oak', nodes=['Table']).
- `.set_sub_material(index: int, material: _mx.Material | None) -> None`: Put a material (None empties) in sub-material index, as sub_materials counts: multi.set_sub_material(0, brick) sets ID 1.
- `.sub_materials` (list[_mx.Material | None]): Sub-materials, None where empty; for a Multi/Sub-Object entry i is material ID i+1.

### Matrix

A transform as four rows of Point3, the X, Y and Z axes then the position: node.transform. It is a tuple of its rows, and any four rows of (x, y, z) pass where a Matrix is asked for.

- `.inverse() -> _mx.Matrix`: The transform that undoes this one: node.transform.inverse().transform_point(p) is world point p in the node's own space. ValueError when the axes span no vo...
- `.pos` (_mx.Point3): The position.
- `.transform_point(point: _mx.Point3) -> _mx.Point3`: A point given in this transform's own space, in the space the transform is in: node.transform.transform_point((0, 0, 10)) is 10 units up the node's Z, in wor...
- `.transform_vector(vector: _mx.Point3) -> _mx.Point3`: A direction taken the same way as transform_point(), without the position.
- `.x_axis` (_mx.Point3): The X axis: its direction, and its scale as its length.
- `.y_axis` (_mx.Point3): The Y axis: its direction, and its scale as its length.
- `.z_axis` (_mx.Point3): The Z axis: its direction, and its scale as its length.

### Modifier

A modifier on a node's stack: m.params.angle = 30, m.enabled = False.

- `.class_info` (_mx.ClassInfo): The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what the class takes, x.class_info.plugin the DLL it comes from.
- `.class_name` (str): MAXScript class: Bend, TurboSmooth, Edit_Poly, ...
- `.delete() -> None`: Remove the modifier from the stack.
- `.enabled` (bool, settable): Whether the modifier is on, in the viewports and in renders.
- `.exists` (bool): False once the object is gone: deleted, or off the stack or list it was read from.
- `.handle` (int): The object's animatable handle. In pymxs: rt.getAnimByHandle(x.handle).
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces the object publishes.
- `.name` (str, settable): The modifier's name in the stack; assigning renames it.
- `.node` (_mx.Node): The node whose stack this modifier was read from; an instanced modifier is on others too.
- `.params` (_mx.Params): The modifier's parameters.
- `.references(depth: int = 3) -> dict`: Reference tree below the object: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; 'references' holds the entries below while depth lasts and...

### Node

A scene node, held by handle; NotFound once deleted.

- `.add_modifier(cls: str, name: str = '', **params) -> _mx.Modifier`: Add a modifier on top of the stack: add_modifier('Bend', angle=30, direction=90).
- `.add_spline(points: object, closed: bool = False, knot: str = 'smooth', *, expected: str | None = None) -> _mx.Spline`: Add a spline from world points or Knots to an editable shape (a hole, another outline); returns it.
- `.bbox` (_mx.Box | None): World bounding box ((min), (max)) of the evaluated geometry, or None.
- `.boolean(operands: object | None = None, operation: str = 'subtract', *, cutters: object | None = None, repeat: object | None = None, operations: collections.abc.Sequence[str] | None = None, option: str = '', method: str | None = None, voxel_size: float | None = None, live: bool = False, use_operand_material: bool = True, new_modifier: bool = False, modifier_name: str = '') -> _mx.Boolean`: Cut or fuse with a Boolean modifier and return it: wall.boolean(['Door', 'Window']) subtracts both. operation: union, subtract, intersect, merge, attach, ins...
- `.boolean_modifier` (_mx.Boolean | None): The topmost Boolean modifier on the stack, or None.
- `.children` (list[_mx.Node]): The nodes parented to this one, one level down.
- `.class_info` (_mx.ClassInfo): The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what the class takes, x.class_info.plugin the DLL it comes from.
- `.class_name` (str): Class of the base object: Sphere, Editable_Poly, ...
- `.clone(mode: str = 'copy', name: str = '', offset: _mx.Point3 | None = None) -> _mx.Node`: Copy, instance or reference the node (and its children), moved by offset.
- `.conform_verts(target: object, indices: collections.abc.Sequence[int] | None = None, *, axes: str = 'xyz', axis: str = '', strength: float = 1.0, spline: int = 1, convert: bool = False) -> tuple[int, int]`: Pull base vertices onto a spline (closest point on spline number `spline`) or a surface (ray along axis: x/y/z both ways, +x/-x/... one way, default z); axes...
- `.convert_to_poly() -> _mx.Poly`: Collapse the modifier stack into an Editable Poly (MAXScript convertToPoly) as one undo step; returns node.poly.
- `.convert_to_spline() -> None`: Collapse a shape to an editable spline (convertToSplineShape), discarding its modifier stack.
- `.curve_model(include_definition: bool = False) -> dict`: The node's curve_model recipe: kind, parameters, model_token, whether the geometry still matches it (reason says why not), and its definition with include_de...
- `.curve_token` (str): Token of the base knots, topology and object transform; pass it as expected= so edits refuse a changed shape.
- `.curve_update(parameters: object, *, expected: str | None = None) -> dict`: Change curve_model parameters in place, keeping placement, materials and modifiers: node.curve_update({'bow': 6}, expected=node.curve_model()['model_token'])...
- `.delete() -> None`: Delete the node.
- `.dependents` (list[_mx.Node]): Nodes whose references reach this one directly: constraint, look-at and skin users.
- `.exists` (bool): False once the node is deleted.
- `.faces() -> list[tuple[int, int, int]]`: Triangles of the evaluated mesh as 1-based vertex IDs into verts(): mx.create_mesh('Copy', n.verts(), n.faces()) copies it.
- `.get_verts(indices: collections.abc.Sequence[int] | None = None, *, bbox: _mx.Box | None = None, near: _mx.Point3 | None = None, radius: float = 0.0, convert: bool = False) -> dict[int, _mx.Point3]`: World positions of the Editable Poly base's vertices as {index: (x, y, z)}, 1-based; filter by indices, bbox=((min), (max)) and near=(x, y, z) with radius. c...
- `.group` (_mx.Group | None): The group this node is in, or None.
- `.handle` (int): The node's animatable handle. In pymxs: rt.getAnimByHandle(node.handle), not maxOps.getNodeByHandle.
- `.hidden` (bool, settable): Whether the node is hidden; assign to hide or show it.
- `.inspect() -> dict`: Report: classes, transform, hierarchy, visibility and render flags, wirecolor, instances (self included), mesh, bbox, modifiers, material and the plugin file...
- `.instances` (list[_mx.Node]): Nodes sharing this node's object (instances and references), itself included: instanced when len > 1.
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the node by name: node.interface('INodeBakeProperties').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces of the node itself.
- `.layer` (_mx.Layer, settable): The node's layer; assign a Layer or a layer name to move it: node.layer = 'Walls'.
- `.loft_mesh` (_mx.LoftMesh | None): The node's parametric loft definition (LoftMesh), or None.
- `.look_at(target: _mx.Point3, up: _mx.Point3 | None = None) -> None`: Turn the node so its -Z axis, the way cameras and lights look, points at a world point: cam.look_at((0, 0, 100)). Position and scale stay; up defaults to wor...
- `.material` (_mx.Material | None, settable): The node's material, or None.
- `.mesh_counts` (tuple[int, int] | None): (vertices, faces) of the evaluated, triangulated mesh, or None.
- `.mesh_qa(limit: int = 12, area_epsilon: float = 0.0) -> dict`: Topology report of the evaluated mesh: boundary, non-manifold and winding-conflict edges, degenerate and duplicate triangles, isolated vertices and edge-conn...
- `.modifiers` (list[_mx.Modifier]): Modifier stack, top first.
- `.move_verts(offset: _mx.Point3, indices: collections.abc.Sequence[int] | None = None, *, falloff: float = 0.0, convert: bool = False) -> tuple[int, int]`: Move base vertices (all by default) by a world offset; falloff > 0 drags neighbours within that distance with (1 - d/falloff)^2 weight. Returns (moved, soft_...
- `.name` (str, settable): The node's name; assigning renames it. Names need not be unique: hold the node, or its handle.
- `.orientation() -> dict`: World orientation: pivot, unit local axes (x, y, z), matrix rows, bbox center and size, pivot_to_center. Keys: {'pivot', 'axes', 'matrix', 'center', 'size',...
- `.params` (_mx.Params): The base object's parameters: node.params.radius = 10.
- `.parent` (_mx.Node | None, settable): Parent node or None; reparenting keeps the world transform.
- `.path` (str): Hierarchy path from the scene root, '/Rig/Camera'; '/' in a name is written '~1' and '~' is '~0'.
- `.poly` (_mx.Poly): The Editable Poly base cage beneath the modifiers (Poly), for component IDs and edits; BadParam for other bases.
- `.pos` (_mx.Point3, settable): World position of the pivot (x, y, z).
- `.references(depth: int = 3) -> dict`: Reference tree below the node: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; 'references' holds the entries below while depth lasts and 'r...
- `.rot` (_mx.Point3, settable): Rotation track as Euler degrees (x, y, z), relative to the parent; keeps winding past 360.
- `.scale` (_mx.Point3, settable): Scale track (x, y, z), or one number for all axes.
- `.selected` (bool, settable): Whether the node is selected; setting it adds to or removes from the selection.
- `.set_verts(positions: dict, *, convert: bool = False) -> int`: Place base vertices at world positions: node.set_verts({1: (0, 0, 10), 4: (5, 0, 10)}); returns how many.
- `.splines` (list[_mx.Spline]): The splines of an editable spline base (SplineShape or Line).
- `.track(path: str) -> _mx.Track`: Transform track: 'pos', 'pos.x'..'pos.z', 'rot.x'..'rot.z' (degrees), 'scale'.
- `.tracks(filter: str | None = None, depth: int = 6) -> list[_mx.Track]`: Every animatable track under the node as sub-anim path tracks, filtered by a substring of the path or controller class: node.tracks('radius').
- `.transform` (_mx.Matrix, settable): World matrix as four rows of (x, y, z): the node's X, Y and Z axes, then its position. Setting it raises when a controller keeps part of the transform.
- `.verts(world: bool = True) -> list[_mx.Point3]`: Vertex positions of the evaluated, triangulated mesh (after modifiers), world space unless world=False; its IDs are not the cage's.
- `.wires(depth: int = 8) -> list[dict]`: The node's wired tracks: [{track, controller, driven, driver, two_way, links: [{param, node, expression}]}].

### Object

Any other object a parameter holds, with its own parameters: a map's coords and output, a curve control, ... tex.params.coords.params.describe().

- `.class_info` (_mx.ClassInfo): The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what the class takes, x.class_info.plugin the DLL it comes from.
- `.class_name` (str): MAXScript class: StandardUVGen, StandardXYZGen, StandardTextureOutput, ...
- `.exists` (bool): False once the object is gone: deleted, or off the stack or list it was read from.
- `.handle` (int): The object's animatable handle. In pymxs: rt.getAnimByHandle(x.handle).
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces the object publishes.
- `.params` (_mx.Params): The object's parameters.
- `.references(depth: int = 3) -> dict`: Reference tree below the object: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; 'references' holds the entries below while depth lasts and...

### Operand

A Boolean operand (a snapshot): index, name, kind, operation, option, disabled.

- `.disabled` (bool): Whether the operand is switched off and left out of the result.
- `.index` (int): Flat index in the operand stack; the base object is 1.
- `.kind` (str): 'single' or 'folder'.
- `.name` (str): The operand's name in the Boolean's operand list.
- `.operation` (str): union, subtraction, intersection, merge, attach, insert or split.
- `.option` (str): none, imprint (edges only) or cookie (cut without adding faces).

### Params

Parameters by name: p.radius, p['radius'] = 10. Angles in degrees, percentages 0-100, colors 0-1, named choices by name (p.BendAxis = 'x'), gizmos as {'position', 'rotation', 'scale'}. p.describe() lists them with ranges and choices.

- `.describe(query: str = '') -> list[str]`: One line per parameter: name, type, value, range, {named choices}, "UI label", +anim; subanims (gizmo, center) and MAXScript properties after. query filters...
- `.info(query: str = '') -> object`: describe() as a list of dicts: name, type, value, default, range, choices, label, animatable, source.
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.params.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces of the object these parameters belong to.
- `.keys() -> list[str]`: Parameter names, then transform/point subanims (gizmo, center), then MAXScript properties that are not parameters.
- `.patch(schema: dict, all_instances: bool = False, **values) -> dict`: Guarded edit, one undo step, refused if anything inspected changed: p.patch(s, multiplier=2.0). Enums take their names; all_instances=True allows editing a s...
- `.schema(*fields, query: str = '', offset: int = 0, limit: int = 256) -> object`: Plugin inspector report with live values and the schema and state tokens patch() needs: p.schema('multiplier').
- `.to_dict() -> dict`: Every parameter and its value.
- `.track(name: str) -> _mx.Track`: The parameter's animation track.
- `.update(**values) -> None`: Set several parameters: p.update(radius=10, segs=32).

### Plugin

A loaded plugin DLL: name, path, description, classes, nodes.

- `.classes` (list[_mx.ClassInfo]): Classes the plugin defines.
- `.description` (str): The description the plugin gives of itself.
- `.loaded` (bool): False while 3ds Max defers loading it.
- `.name` (str): File name: 'prim.dlo'.
- `.nodes` (list[_mx.Node]): Scene nodes that use any of the plugin's classes.
- `.path` (str): Full path of the DLL file.

### Point3

A point or vector (x, y, z). It is a tuple, and any (x, y, z) passes where a Point3 is asked for. + and - work per component with another point, * and / with a number: node.pos + (0, 0, 10), (a - b).length.

- `.cross(other: _mx.Point3) -> _mx.Point3`: Cross product with another point or (x, y, z).
- `.distance_to(other: _mx.Point3) -> float`: Distance to another point or (x, y, z).
- `.dot(other: _mx.Point3) -> float`: Dot product with another point or (x, y, z).
- `.length` (float): Its length.
- `.normalized() -> _mx.Point3`: The same direction at length 1; a zero vector stays zero.
- `.x` (float): The x component.
- `.y` (float): The y component.
- `.z` (float): The z component.

### Poly

An Editable Poly base cage beneath the modifiers: 1-based vertex, edge and face IDs as polyop numbers them; positions are world space unless world=False.

- `.capture(level: str, ids: collections.abc.Sequence[int] | None = None) -> dict`: Capture the AGENT VIEWPORT with up to 100 components labeled at their centers (F12, E3, V7); pass 'view' to pick(). Keys: {'file', 'view', 'width', 'height',...
- `.centers(level: str, ids: collections.abc.Sequence[int] | None = None, world: bool = True) -> list[_mx.Point3 | None]`: Centers (mean vertex position) of 'vertex', 'edge' or 'face' components, all or of the given IDs.
- `.counts` (tuple[int, int, int]): (vertices, edges, faces), counting deleted slots not yet collapsed, as polyop does.
- `.edges(ids: collections.abc.Sequence[int] | None = None) -> list[tuple[int, int] | None]`: (v1, v2) vertex IDs of each edge, all or of the given IDs.
- `.edit(ops: object, expected: str = '', show: bool = False) -> dict`: Run ops as one undo step, rolled back if one fails: edit([{'op': 'extrude', 'selection': ids, 'amount': 10}], expected=token). Each op has op, level ('vertex...
- `.faces(ids: collections.abc.Sequence[int] | None = None) -> list[list[int] | None]`: Vertex IDs of each face in winding order, all or of the given IDs.
- `.find(level: str, ids: collections.abc.Sequence[int] | None = None, current: bool = False, bbox: _mx.Box | None = None, near: _mx.Point3 | None = None, radius: float | None = None, normal: _mx.Point3 | None = None, angle: float | None = None, boundary: bool | None = None, sharp: float | None = None) -> list[int]`: IDs passing every filter given: among ids, selected (current), world center in bbox or within radius of near, face normal within angle (default 10) of normal...
- `.instances` (list[_mx.Node]): Nodes sharing this base, this one first; an edit changes them all.
- `.node` (_mx.Node): The node whose base this is.
- `.normals(ids: collections.abc.Sequence[int] | None = None, world: bool = True) -> list[_mx.Point3 | None]`: Unit face normals (Newell; (0, 0, 0) for a degenerate face), all or of the given face IDs.
- `.pick(x: float, y: float, view: str, level: str = 'edge', ids: collections.abc.Sequence[int] | None = None, tolerance: float = 0.025, limit: int = 5, prefer_surface: bool = True) -> dict`: Components near image point x, y (0-1, top-left) of the agent viewport capture whose token is view, within tolerance (fraction of the shorter side); ranked b...
- `.token` (str): Hash of the cage and its transform: edit(ops, expected=token) refuses IDs read before the mesh changed.
- `.verts(ids: collections.abc.Sequence[int] | None = None, world: bool = True) -> list[_mx.Point3 | None]`: Vertex positions, all or of the given IDs; None for a deleted slot.

### RenderJob

A production render queued by mx.render(): job.wait() renders it now; else it starts when the script returns.

- `.cancel() -> bool`: Drop the job if it has not started; returns whether it was dropped.
- `.done` (bool): True once the job ended (done, failed or cancelled).
- `.error` (object): Why the render failed, else None.
- `.id` (str): The job's ID, as render_automations(job_id=...) takes it.
- `.result` (object): {'status', 'output', 'width', 'height', 'seconds'} once done, else None.
- `.signal_path` (str): Done file written when the job ends; render_automations(action='status', job_id=job.id) reads it outside Max.
- `.status` (str): 'queued', 'rendering', 'done', 'failed' or 'cancelled'.
- `.wait() -> dict`: Render now if still queued and return the result; raises if it failed, was cancelled, or already went to Max. Keys: {'status', 'output', 'width', 'height', '...

### Segment

A spline segment snapshot: type (line or curve), world length, and its cubic's control points.

- `.length` (float): World length of the segment.
- `.points` (tuple[_mx.Point3, _mx.Point3, _mx.Point3, _mx.Point3]): (start, start handle, end handle, end) in world space; a line's handles lie on its chord.
- `.type` (str): line or curve.

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
- `.index` (int): The spline's position in node.splines, from 0.
- `.insert(segment: int, param: float = 0.5, *, expected: str | None = None) -> int`: Split a segment at param (0-1, exclusive) with a new knot; returns its index.
- `.knots` (list[_mx.Knot]): The knots as Knot values; edit them with set_knot or set_knots.
- `.length` (float): World length of the spline.
- `.node` (_mx.Node): The node whose shape holds this spline.
- `.open(*, expected: str | None = None) -> None`: Open the spline between its last and first knots.
- `.pick(x: float, y: float, view: str, tolerance: float = 0.025, knots: collections.abc.Sequence[int] | None = None) -> list`: Knots and handles near image point (x, y) of a capture (fractions 0-1), nearest first; view is the capture's view_token. Overlapping front and back handles s...
- `.qa(tolerance: float = 0.01) -> dict`: Sampled QA report: length, planarity, self-intersections, tangent breaks and segments that backtrack. complete is False, with a reason, for a spline it could...
- `.reverse(*, expected: str | None = None) -> None`: Reverse the knot order, keeping the first knot first.
- `.sample(count: int) -> list[_mx.Point3]`: count points evenly spaced by length along the spline, world space (closed: no repeated end).
- `.segments` (list[_mx.Segment]): The segments as Segment snapshots, segment i leaving knot i.
- `.set_knot(index: int, *, pos: _mx.Point3 | None = None, in_vec: _mx.Point3 | None = None, out_vec: _mx.Point3 | None = None, type: str | None = None, seg: str | None = None, expected: str | None = None) -> None`: Change one knot; a moved knot carries its handles: set_knot(2, pos=(10, 0, 0)), set_knot(2, type='bezier_corner', out_vec=(14, 3, 0)).
- `.set_knots(knots: object, *, expected: str | None = None) -> None`: Rewrite every knot at once from Knots, dicts or points (one per knot); checked before anything is written. ks = s.knots; ks[1].pos = (5, 2, 0); s.set_knots(ks).

### StateSet

A State Set (a snapshot): name, camera, render_range, anim_range, lock_camera_to_range.

- `.anim_range` (tuple[float, float] | None): (start, end) animation frames, or None.
- `.camera` (_mx.Node | None): The state set's camera, or None.
- `.lock_camera_to_range` (bool): Whether the camera's animation is locked to the range.
- `.name` (str): The State Set's name.
- `.render_range` (tuple[float, float] | None): (start, end) render frames, or None.

### Texmap

A texture map: tex.params.filename = 'C:/tex/wood.png'; mat.params.base_color_map = tex.

- `.class_info` (_mx.ClassInfo): The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what the class takes, x.class_info.plugin the DLL it comes from.
- `.class_name` (str): MAXScript class: Bitmaptexture, Noise, OSLMap, ...
- `.exists` (bool): False once the object is gone: deleted, or off the stack or list it was read from.
- `.handle` (int): The map's animatable handle. In pymxs: rt.getAnimByHandle(tex.handle).
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces the object publishes.
- `.maps` (dict[str, _mx.Texmap | list[_mx.Texmap | None] | None]): Sub-map slots by parameter name: {'map1': <Texmap>, 'mask': None}; set them through params.
- `.name` (str, settable): The map's name.
- `.nodes` (list[_mx.Node]): Scene nodes whose material uses this map.
- `.params` (_mx.Params): The map's parameters.
- `.references(depth: int = 3) -> dict`: Reference tree below the object: {'class', 'superclass', 'name', 'references': [{'index', ...}]}; 'references' holds the entries below while depth lasts and...

### Track

An animation track: node.track('pos.z'), params.track('radius'), node.tracks()[i]. Node paths: 'pos', 'pos.x'..'pos.z', 'rot' and 'rot.x'..'rot.z' (degrees), 'scale', 'transform', a sub-anim path '[#Transform][#Position]', or a path plus steps: 'pos[2]' is a list controller's 2nd layer. Key edits take frames=[...] and/or start=/end=.

- `.apply_script(source: str, expected: str, bindings: dict[str, _mx.Node | _mx.Track | tuple[_mx.Track, float] | _mx.Controller | float | int | bool | str | _mx.Point3 | tuple[float, float, float, float]] | None = None, frames: collections.abc.Sequence[float] | None = None) -> dict`: Validate, then replace the controller with the script controller, sampling it in place; restores the old controller on failure. expected is expected_controll...
- `.assign(cls: str | _mx.Controller, layer: bool = False, **params) -> _mx.Controller`: Give the track a new controller of class cls (a name within the track's kind, or a Controller to share) and set its parameters; layer=True adds it on top of...
- `.bake(start: float | None = None, end: float | None = None, step: float = 1.0) -> int`: Replace start..end (default: the timeline) with a key every step frames of the evaluated value; a script, constraint, wire, noise or list controller becomes...
- `.clear() -> None`: Delete every key; the value at the current frame stays.
- `.controller` (_mx.Controller | None): The track's Controller, or None for a parameter that is not animated.
- `.delete_keys(frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Delete the keys at frames or in start..end; returns how many: delete_keys(start=10, end=20).
- `.inspect_script(frames: collections.abc.Sequence[float] | None = None) -> dict`: The track's script controller: track is this track and controller its controller's class (None for a parameter that is not animated); script, throw_on_error...
- `.key(frame: float, value: float | int | bool | str | _mx.Point3 | list[float | int | bool | str | _mx.Point3] | None = None, tangent: str | None = None, in_tangent: str | None = None, out_tangent: str | None = None) -> None`: Set a key at frame (value None keys the current value): a number, (x, y, z), a rotation's Euler degrees, or on a parameter track what the parameter takes. ta...
- `.keys` (list[_mx.Key]): The keys, in time order.
- `.loop(start: float | None = None, end: float | None = None) -> None`: Close a cycle: key at end the value at start (default first and last key) and repeat it outside (ort cycle).
- `.match(source: float | None = None, target: float | None = None) -> float | _mx.Point3 | tuple[_mx.Point3, _mx.Point3, _mx.Point3, _mx.Point3] | None`: Key at target the value at source (default: the first key onto the last); returns the new value.
- `.move_keys(offset: float, frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Shift keys by offset frames (all keys by default); refuses to land on another key. Returns how many moved.
- `.ort` (tuple[str, str] | None, settable): Out-of-range types (before, after), None if not animated; set one name or a pair, where None keeps that side: constant, cycle, loop, ping_pong, linear, relat...
- `.path` (str): The track's path, or its parameter's name.
- `.resample(start: float | None = None, end: float | None = None, step: float = 1.0) -> int`: Add a key every step frames in start..end (default: the timeline), keeping the curve; returns keys per axis.
- `.scale_keys(factor: float, pivot: float | None = None, frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Scale key times around pivot (default start, else the first key): scale_keys(2.0) doubles the timing.
- `.tangents(tangent: str | None = None, in_tangent: str | None = None, out_tangent: str | None = None, frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Set Bezier tangent types (all keys by default); returns how many keys changed: tangents('smooth', start=0, end=50).
- `.tcb(tension: float | None = None, continuity: float | None = None, bias: float | None = None, ease_to: float | None = None, ease_from: float | None = None, frames: collections.abc.Sequence[float] | None = None, start: float | None = None, end: float | None = None) -> int`: Set TCB key settings, 0-50 as in MAXScript (all keys by default); returns how many keys changed.
- `.unwire() -> None`: Disconnect this driven track's wire; it keeps a plain controller holding its current value.
- `.validate_script(source: str, bindings: dict[str, _mx.Node | _mx.Track | tuple[_mx.Track, float] | _mx.Controller | float | int | bool | str | _mx.Point3 | tuple[float, float, float, float]] | None = None, frames: collections.abc.Sequence[float] | None = None) -> dict`: Check a script on a copy of the controller, off the track, at frames (default the current one): output_type is float, position, rotation, scale or point3, sa...
- `.value(frame: float | None = None) -> float | int | bool | _mx.Point3 | tuple[_mx.Point3, _mx.Point3, _mx.Point3, _mx.Point3] | list[float | int | bool | _mx.Point3] | None`: The track's value at frame (default: the current one): a number, a Point3 (a rotation as Euler degrees), four Point3 rows for 'transform', None for a kind th...
- `.wire(driver: _mx.Track, expression: str, reverse: str | None = None) -> _mx.Controller`: Drive this track from driver (a Track) through expression, which names the driver by its sub-anim name: angle.wire(radius, 'Radius * 2'). reverse makes it tw...

### Viewport

A viewport, resolved on every call: the user's active view, or the AGENT VIEWPORT.

- `.capture(method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: Save the view as it is to a PNG; method='window' grabs the window instead of the Nitrous buffer.
- `.capture_frames(frames: object, view: object = 'current', subject: object | None = None, shading: str | None = None, edges: bool | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: One view over time in one strip: a frame count spread over the range or up to 12 frame numbers; a named view is framed over all of them; the time slider come...
- `.capture_views(views: object | None = None, subject: object | None = None, shading: str | None = None, edges: bool | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: Up to 8 framed views in one grid (default front, right, back, top; also perspective, three_quarter, current, (x, y, z) eye directions); subject frames one hi...
- `.edges` (bool): Whether edged faces show.
- `.eye` (_mx.Point3): Eye position (x, y, z).
- `.fov` (float | None): Horizontal field of view in degrees; None for orthographic views.
- `.set(view: str = 'perspective', eye: _mx.Point3 | None = None, target: _mx.Point3 | None = None, frame: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None, fov: float = 45.0, padding: float = 1.15, shading: str = 'smooth', edges: bool = False, grid: bool = False) -> None`: Set the whole view, defaults included: view (perspective, orthographic, front, back, left, right, top, bottom), eye/target, frame (nodes to fit), fov, shadin...
- `.shading` (str): Visual style: smooth, flat, wireframe, clay, realistic, facets, hidden_line, ...
- `.size` (tuple[int, int]): (width, height) in pixels.
- `.target` (_mx.Point3): The point the view looks at and orbits: (x, y, z).
- `.type` (str): perspective, orthographic, front, back, left, right, top, bottom, camera, light or other.

## Errors

- `Ambiguous`: A name matches several nodes or classes; pass a handle.
- `BadParam`: A value has the wrong type or 3ds Max refused it.
- `MaxError`: Base of every _mx error.
- `NotFound`: A node, class or object does not exist.
- `UnknownParam`: No parameter of that name.
- `WrongThread`: Called off 3ds Max's main thread.

## Values

- `BUILD` = 'b20261111215429'
- `MAX_VERSION` = 2027
