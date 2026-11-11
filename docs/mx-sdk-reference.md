# sdk (_mx) reference

3ds Max for agents, bound from the SDK: the scene's nodes, parameters, modifiers, materials and animation, and modeling, viewport, file and plugin work on them. Lights, cameras and renders are mx.render_setup, the same on every renderer.

Build `b20261111233756`, 3ds Max SDK 2027.

## Collections

What the scene or 3ds Max has many of. Every collection answers the same way: `c[key]`, `c.get(key)`, `key in c`, `len(c)`, iteration, and `c(pattern, ...)` to list by wildcard.

### action_tables

The action tables 3ds Max has registered: action_tables['Main UI'] by name, action_tables('Main*') by wildcard. actions() searches the actions of all of them.

- `action_tables(pattern: str | None = None) -> list[_mx.ActionTable]`: Action tables by wildcard name: action_tables('Main*').
- `action_tables[key: str] -> _mx.ActionTable`: The item of that key; NotFound or Ambiguous otherwise. `key in action_tables`, `len(action_tables)` and `for item in action_tables` work as on a list.
- `.get(key: str) -> _mx.ActionTable | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.

### classes

The classes 3ds Max has registered. classes['TurboSmooth'] is one by MAXScript or UI name, and classes['Bend', 'modifier'] names the kind where several share a name; classes('modifier', '*Bend*') lists by kind, wildcard name and plugin file.

- `classes(kind: str | None = None, pattern: str | None = None, plugin: str | None = None) -> list[_mx.ClassInfo]`: Registered classes by kind, wildcard name and plugin file: classes('modifier', '*Bend*'), classes(plugin='tyFlow*').
- `classes[key: str | tuple[str, str]] -> _mx.ClassInfo`: The item of that key; NotFound or Ambiguous otherwise. `key in classes`, `len(classes)` and `for item in classes` work as on a list.
- `.get(key: str | tuple[str, str]) -> _mx.ClassInfo | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.

### effects

The scene's atmospherics and render effects, atmospherics first. effects['Fog'] is one by its name in the Environment or Effects list; effects('atmospheric') lists one kind. fx.delete() removes one.

- `effects(kind: str | None = None) -> list[_mx.Effect]`: Atmospherics then render effects, or one kind: effects('atmospheric').
- `effects[key: str] -> _mx.Effect`: The item of that key; NotFound or Ambiguous otherwise. `key in effects`, `len(effects)` and `for item in effects` work as on a list.
- `.get(key: str) -> _mx.Effect | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.

### groups

The scene's groups, nested ones included. groups['Chairs'] is one by its head's name, handle or Node; groups.new(nodes, 'Chairs') groups nodes. A group dissolves with group.ungroup() or goes, members and all, with group.delete().

- `groups(pattern: str | None = None) -> list[_mx.Group]`: Every group, or those a wildcard name matches: groups('Chair*').
- `groups[key: _mx.Node | str | int] -> _mx.Group`: The item of that key; NotFound or Ambiguous otherwise. `key in groups`, `len(groups)` and `for item in groups` work as on a list.
- `.get(key: _mx.Node | str | int) -> _mx.Group | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.
- `.new(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str], name: str = '') -> _mx.Group`: Group nodes under a new group head: groups.new(nodes('Chair*'), 'Chairs').

### interfaces

3ds Max's core interfaces (MAXScript's global ones). interfaces['LayerManager'] is one by name, a plugin class's static interface included; interfaces('*Layer*') lists the core ones by wildcard. An object's own interfaces are on the object: node.interfaces, mod.interface('unwrap2').

- `interfaces(pattern: str | None = None) -> list[_mx.Interface]`: Core interfaces (MAXScript's global ones), by wildcard name: interfaces('*Layer*').
- `interfaces[key: str] -> _mx.Interface`: The item of that key; NotFound or Ambiguous otherwise. `key in interfaces`, `len(interfaces)` and `for item in interfaces` work as on a list.
- `.get(key: str) -> _mx.Interface | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.

### layers

The scene's layers, in the layer manager's order. layers['Walls'] is one by name, ignoring case; layers.new('Walls') makes one, layers.current is where new objects go, layers.remove(layer) deletes an empty one.

- `layers(pattern: str | None = None) -> list[_mx.Layer]`: Every layer, or those a wildcard name matches: layers('Wall*').
- `layers[key: str] -> _mx.Layer`: The item of that key; NotFound or Ambiguous otherwise. `key in layers`, `len(layers)` and `for item in layers` work as on a list.
- `.current` (_mx.Layer, settable): The layer new objects go on; assign a Layer or a layer name: layers.current = 'Walls'.
- `.get(key: str) -> _mx.Layer | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.
- `.new(name: str) -> _mx.Layer`: Create a layer; a name already taken (ignoring case) is BadParam: layer = layers.get('Walls') or layers.new('Walls').
- `.remove(layer: _mx.Layer | str) -> None`: Delete a layer (a Layer or its name); it must hold no nodes and not be current.

### macros

The macroscripts of this 3ds Max session. macros['SmartIsolate'] is one by name, and macros['SmartIsolate', 'Tools'] names the category where several share a name; macros('*isolate*') lists by wildcard name, tooltip or button text.

- `macros(pattern: str | None = None, category: str | None = None) -> list[_mx.Macro]`: Macroscripts by wildcard name, tooltip or button text and category: macros('*isolate*').
- `macros[key: str | tuple[str, str]] -> _mx.Macro`: The item of that key; NotFound or Ambiguous otherwise. `key in macros`, `len(macros)` and `for item in macros` work as on a list.
- `.get(key: str | tuple[str, str]) -> _mx.Macro | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.

### materials

The materials assigned to scene nodes. materials['Brick'] is one by name, materials('Brick*') lists by wildcard, and materials.new('PhysicalMaterial', 'Red', base_color=(0.8, 0.1, 0.1)) creates one; a new material is in the collection once a node uses it (node.material = mat).

- `materials(pattern: str | None = None) -> list[_mx.Material]`: Materials assigned to scene nodes, or those a wildcard name matches: materials('Brick*').
- `materials[key: str] -> _mx.Material`: The item of that key; NotFound or Ambiguous otherwise. `key in materials`, `len(materials)` and `for item in materials` work as on a list.
- `.get(key: str) -> _mx.Material | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.
- `.new(cls: str = 'PhysicalMaterial', name: str = '', **params) -> _mx.Material`: Create a material with parameters: materials.new('PhysicalMaterial', 'Red', base_color=(0.8, 0.1, 0.1)).

### nodes

The scene's nodes. nodes['Ball'] is one by name, handle or Node; nodes('Wall*', cls='Box', hidden=False) lists those every filter matches; nodes.new('Sphere', 'Ball', radius=10) creates one and nodes.remove(nodes) deletes.

- `nodes(pattern: str | None = None, cls: str | None = None, layer: str | None = None, roots_only: bool = False, uses: str | None = None, **props) -> list[_mx.Node]`: Nodes matching every filter, all of them without one: nodes('Wall*', cls='Box', layer='Walls', uses='TurboSmooth', hidden=False, height=50). Keywords compare...
- `nodes[key: _mx.Node | str | int] -> _mx.Node`: The item of that key; NotFound or Ambiguous otherwise. `key in nodes`, `len(nodes)` and `for item in nodes` work as on a list.
- `.get(key: _mx.Node | str | int) -> _mx.Node | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.
- `.new(cls: str, name: str = '', pos: _mx.Point3 | None = None, **params) -> _mx.Node`: Create an object (geometry, shape, helper, light, camera) with parameters: nodes.new('Sphere', 'Ball', pos=(0, 0, 10), radius=10, segs=32).
- `.remove(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Delete a node or a list of nodes; an empty list deletes nothing.

### plugins

The plugin DLLs 3ds Max has in its directory. plugins['prim.dlo'] is one by file name, ignoring case; plugins('*corona*') lists by wildcard file name or description.

- `plugins(pattern: str | None = None) -> list[_mx.Plugin]`: Plugin DLLs by wildcard file name or description: plugins('*corona*').
- `plugins[key: str] -> _mx.Plugin`: The item of that key; NotFound or Ambiguous otherwise. `key in plugins`, `len(plugins)` and `for item in plugins` work as on a list.
- `.get(key: str) -> _mx.Plugin | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.

### render_jobs

The render jobs queued in this Max session, oldest first: render_jobs[job.id], render_jobs(). render() queues one.

- `render_jobs(pattern: str | None = None) -> list[_mx.RenderJob]`: Render jobs queued in this Max session, oldest first, or those a wildcard ID matches.
- `render_jobs[key: str] -> _mx.RenderJob`: The item of that key; NotFound or Ambiguous otherwise. `key in render_jobs`, `len(render_jobs)` and `for item in render_jobs` work as on a list.
- `.get(key: str) -> _mx.RenderJob | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.

### selection

The selected nodes. selection() lists them, `node in selection` and len(selection) read the state; selection.set(nodes) selects these and no others, add and remove change it, clear() empties it.

- `selection(pattern: str | None = None) -> list[_mx.Node]`: The selected nodes, or those a wildcard name matches: selection('Wall*').
- `selection[key: _mx.Node | str | int] -> _mx.Node`: The item of that key; NotFound or Ambiguous otherwise. `key in selection`, `len(selection)` and `for item in selection` work as on a list.
- `.add(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Add nodes to the selection.
- `.clear() -> None`: Deselect everything.
- `.get(key: _mx.Node | str | int) -> _mx.Node | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.
- `.remove(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Take nodes out of the selection; they stay in the scene.
- `.set(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Select these nodes and no others; set([]) clears the selection.

### selection_sets

The scene's named selection sets. selection_sets['Lights'] is one by name; selection_sets.new('Lights', nodes('Light*')) makes one, selection_sets.remove(s) deletes one and leaves its nodes.

- `selection_sets(pattern: str | None = None) -> list[_mx.SelectionSet]`: Every named selection set, or those a wildcard name matches.
- `selection_sets[key: str] -> _mx.SelectionSet`: The item of that key; NotFound or Ambiguous otherwise. `key in selection_sets`, `len(selection_sets)` and `for item in selection_sets` work as on a list.
- `.get(key: str) -> _mx.SelectionSet | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.
- `.new(name: str, nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> _mx.SelectionSet`: Create a named selection set of nodes; a name already taken is BadParam.
- `.remove(set: _mx.SelectionSet | str) -> None`: Delete a set (a SelectionSet or its name); its nodes stay in the scene.

### state_sets

The top-level State Sets with their cameras and ranges, as snapshots: state_sets['Shot 01'], state_sets(), state_sets.camera_sequence().

- `state_sets(pattern: str | None = None) -> list[_mx.StateSet]`: Every top-level State Set, or those a wildcard name matches.
- `state_sets[key: str] -> _mx.StateSet`: The item of that key; NotFound or Ambiguous otherwise. `key in state_sets`, `len(state_sets)` and `for item in state_sets` work as on a list.
- `.camera_sequence() -> list[_mx.StateSet]`: The State Sets that have a camera, by render start frame (sets without a render range last).
- `.get(key: str) -> _mx.StateSet | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.

### texmaps

The texture maps the scene uses: every map the scene materials reach, through sub-materials and maps of maps, and the environment map. texmaps['Wood'] is one by name, texmaps('*noise*') lists by wildcard, and texmaps.new('Bitmaptexture', 'Wood', filename='C:/tex/wood.png') creates one; a new map is in the collection once something in the scene uses it.

- `texmaps(pattern: str | None = None) -> list[_mx.Texmap]`: Texture maps the scene uses, or those a wildcard name matches: texmaps('*noise*').
- `texmaps[key: str] -> _mx.Texmap`: The item of that key; NotFound or Ambiguous otherwise. `key in texmaps`, `len(texmaps)` and `for item in texmaps` work as on a list.
- `.get(key: str) -> _mx.Texmap | None`: The item [key] gives, or None when there is none; a key several items share is still Ambiguous.
- `.new(cls: str, name: str = '', **params) -> _mx.Texmap`: Create a texture map with parameters: texmaps.new('Bitmaptexture', 'Wood', filename='C:/tex/wood.png').

## Functions

### `actions(pattern: str | None = None, table: str | None = None) -> list[_mx.Action]`

Actions by wildcard menu text, description or category across tables: actions('*Isolate*').

### `agent_viewport(width: int = 1000, height: int = 740, minimized: bool = False) -> _mx.AgentViewport`

Open the agent's own floating viewport (or restore it) and return it; the size applies when it first opens: av = agent_viewport().

### `animate(on: bool = True) -> _mx.AnimateMode`

with animate(): edits create keys, like the Auto Key button.

### `at(frame: float) -> _mx.AtTime`

with at(frame): reads and edits happen at that frame; the time slider does not move.

### `backup_material_library(source: str = 'all', folder: str = '', path: str = '', prefix: str = '') -> list[dict[str, str | int | bool]]`

Save library, editor slots and both combined ('all') to timestamped .mat files; returns one {'source', 'path', 'count', 'saved', 'skipped', 'error'} per file, skipped with the reason in error when that library is empty.

### `capabilities() -> dict`

What this 3ds Max has: its version, the renderer in use and those installed, which known plugins are installed (plugins: forest_pack, forest_lite, tyflow, railclone, phoenix_fd, as bools) and class counts. Keys: {'max_version', 'renderer', 'renderers', 'plugins', 'material_classes', 'geometry_classes', 'modifier_classes'}.

### `capture_isolated(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None) -> list[_mx.Capture]`

Capture each top-level hierarchy of the selection (or of nodes) alone, framed in the active view; instances share one image, and visibility and selection come back.

### `capture_screen(target: str = 'screen', crop: tuple[int, int, int, int] | None = None, max_width: int = 1600, max_height: int = 0, desktop: bool = False) -> _mx.Capture`

Save an open frame buffer or editor window (vray_vfb, corona_vfb, fstorm_vfb, tyflow_editor) to a JPEG, or the primary monitor with desktop=True (only when the user asked: it shows other applications); crop=(x, y, w, h) in its pixels: capture_screen('vray_vfb').

### `changes(since: _mx.Mark | int) -> list[_mx.Change]`

Persistent scene edits after a Mark (or its seq), oldest first; selection changes are left out.

### `class_relationships(pattern: str | None = None, superclass: str | None = None) -> list[dict[str, str | dict[str, list[str]]]]`

Classes whose parameters take nodes, materials, texmaps or other references: [{'class', 'superclass', 'accepts': {kind: [parameter names]}}], kind being node, material, texturemap, object or ref_target: class_relationships('*Scatter*', superclass='geometry').

### `contacts(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str], against: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None, tolerance: float = 0.0, near_gap: float = 0.0, include_separate: bool = False) -> dict`

Evaluated meshes that penetrate, intersect, touch or nearly touch, worst first: contacts(legs, against=[floor, seat]) checks each leg against those only. tolerance and near_gap default to 0.1 mm and 10 mm in scene units; depth is a lower and gap an upper bound, both from vertices. Nodes without a mesh are listed in 'skipped'. pairs are {'a', 'b', 'status', 'depth', 'gap', ...} and summary counts them by status. Keys: {'tolerance', 'near_gap', 'summary', 'pairs', 'open_meshes', 'skipped'}.

### `create_mesh(name: str, verts: collections.abc.Sequence[_mx.Point3], faces: collections.abc.Sequence[collections.abc.Sequence[int]]) -> _mx.Node`

Create an Editable Poly from world vertices and polygons of 1-based vertex IDs, counterclockwise seen from outside, as one undo step: create_mesh('Tri', [(0, 0, 0), (10, 0, 0), (0, 10, 0)], [(1, 2, 3)]).

### `curve_create(definition: dict[str, dict | float | str], name: str = '', parameters: dict[str, float] | None = None) -> _mx.Node`

Build a curve_model definition as an editable spline or quad cage centered on its bounds, saving the recipe for node.curve_update. definition and parameters are curve_preview's: {'curves': {name: curve}, 'output': {'kind': 'curve' | 'sweep' | 'loft', ...}} and {'width': 4}; mx.help('curve_model') has the grammar.

### `curve_preview(definition: dict[str, dict | float | str], parameters: dict[str, float] | None = None) -> dict`

Evaluate a curve_model definition without touching the scene: counts, bounds and per-curve QA. definition={'curves': {name: curve}, 'output': {'kind': 'curve' | 'sweep' | 'loft', ...}, 'tolerance': 0.01}; parameters {'width': 4} are the numbers its expressions name. mx.help('curve_model') has the grammar of curves and outputs. Keys: {'kind', 'curves', 'alignment', 'bounds'?, 'notes', 'counts'}.

### `fetch() -> None`

Restore what hold() held (Edit > Fetch), dropping the changes since; refuses a hold made elsewhere or for another scene.

### `file_info(path: str | os.PathLike | collections.abc.Sequence[str | os.PathLike], objects: bool = False, classes: bool = False) -> dict | list[dict]`

A .max file's metadata without opening it: its path and size in bytes, the created and modified times (UTC), title, subject, author, comments and properties it holds, plus 'objects' (names) and 'classes' (plugin classes by kind) when asked. A list of paths gives a list of reports, where an unreadable file reports only its 'path' and 'error'. Keys: {'path', 'size'?, 'created'?, 'modified'?, 'title'?, 'subject'?, 'author'?, 'comments'?, 'properties'?, 'objects'?, 'classes'?, 'error'?}.

### `frame() -> float`

Current frame.

### `hold() -> None`

Save the scene to the hold buffer (Edit > Hold) for fetch().

### `import_file(path: str | os.PathLike) -> list[_mx.Node]`

Import a file (FBX, OBJ, USD, ...) with its importer's defaults, no dialogs, and return the new nodes.

### `loft_mesh(name: str, sections: collections.abc.Sequence[collections.abc.Sequence[collections.abc.Sequence[float | str]]], parameters: dict[str, float] | None = None, *, profile_closed: bool = True, close_path: bool = False, caps: bool = False, reverse: bool = False) -> _mx.Node`

Create a parametric quad loft (Editable Poly) through matched cross sections and return its node: loft_mesh('Rail', [[(0, 0, 0), ('w', 0, 0), ('w', 'd', 0)], [(0, 0, 'h'), ('w', 0, 'h'), ('w', 'd', 'h')]], {'w': 10, 'd': 4, 'h': 50}). Sections are lists of (x, y, z) points, the same count in corresponding order, in world space; coordinates are numbers or expressions over parameters (+ - * / % **, sin/cos in radians, sqrt, abs, min, max, pi). profile_closed wraps each section, close_path joins the last section to the first, caps adds end faces (closed profiles, open path), reverse flips the winding.

### `mark() -> _mx.Mark`

Mark the change journal: m = mark(); later, changes(m) lists the edits since and patch(ops, expected=m) refuses to run if there were any.

### `material_classes(pattern: str | None = None) -> list[str]`

Material class names materials.new() takes: material_classes('*PBR*').

### `material_library() -> list[_mx.Material | _mx.Texmap]`

The current material library (currentMaterialLibrary): Materials and Texmaps.

### `maxscript(code: str) -> str`

Evaluate MAXScript; returns the result as text.

### `medit_materials() -> list[_mx.Material | _mx.Texmap | None]`

The 24 Compact Material Editor slots (meditMaterials), None where empty.

### `merge(path: str | os.PathLike, names: str | collections.abc.Sequence[str] | None = None, duplicates: str = 'rename', materials: str = 'rename') -> list[_mx.Node]`

Merge a .max file's objects (all, or names) and return the new nodes. Name clashes: duplicates='rename', 'skip', 'merge' (keep both) or 'delete_old'; materials='rename', 'scene' or 'merged'.

### `native(type: str, **args) -> object`

Run one of the bridge's handlers in-process: native('scene_delta', ...). Returns its reply as Python values.

### `osl(code: str, name: str = '', **params) -> _mx.Texmap`

Compile OSL code into an OSLMap and set its shader parameters; name defaults to the shader's.

### `osl_info(shader: str) -> dict`

Compile an OSL shader (a .osl path or a name in 3ds Max's OSL folder) in a temporary map: params as describe() lines and outputs as (name, type). Keys: {'shader', 'path', 'params', 'outputs'}.

### `overview() -> dict`

Scene summary: object, hidden, frozen and root counts, nodes per class, material and modifier counts, layers in use. class_counts, materials and modifiers are {name: count}. Keys: {'object_count', 'hidden_count', 'frozen_count', 'root_count', 'class_counts', 'materials', 'modifiers', 'layers'}.

### `patch(ops: collections.abc.Sequence[dict], expected: _mx.Mark | int | None = None, dry_run: bool = False, label: str = 'MCP Scene Patch') -> dict`

Validate, then apply node edits as one undo step, all or nothing: patch([{'op': 'rename', 'target': n, 'name': 'Wall'}, {'op': 'transform', 'target': 'Box001', 'move': (0, 0, 10)}]). Ops: rename (name); transform (relative move, rotate in degrees, scale; coordinate_system 'world' or 'local'); set_flags (hidden, frozen, renderable, cast_shadows, receive_shadows); set_parent (parent: a node or None, keep_transform). expected=mark() taken earlier (or its scene_seq) refuses a scene changed since; dry_run=True validates only. A target or parent is a node as resolve() takes it. The report's status is preflight (a dry run), applied, noop, or partial when a transform controller ignored part of an edit (listed in ignored); operations holds one entry per operation, touched the nodes, scene_seq and activity_seq the journal's before and after. Keys: {'dry_run', 'status', 'scene_seq', 'activity_seq', 'journal', 'counts', 'operations', 'transaction', 'touched', 'warnings'?, 'ignored'?, 'journal_event_count'?, 'undo_label'?}.

### `plugin_patch(edits: collections.abc.Iterable[tuple[dict, dict]], all_instances: bool = False) -> dict`

Patch several objects in one guarded undo step: plugin_patch([(s1, {'multiplier': 2}), (s2, {'size': 5})]). properties lists {'owner', 'name', 'before', 'value'} per edit. Keys: {'status', 'verification', 'transaction', 'properties'}.

### `redraw() -> None`

Redraw the viewports.

### `rename(renames: dict) -> list[_mx.Node]`

Rename nodes as one undo step, all or nothing: rename({'Box001': 'Wall_A', node: 'Wall_B'}); refuses names that would be shared.

### `render(width: int | None = None, height: int | None = None, output: str = '') -> _mx.RenderJob`

Queue a production render of the active view at the time slider's frame; it starts when this script returns, or now with .wait(): render(output='C:/r/a.png').wait(). Size defaults to Render Setup's.

### `reset() -> None`

Reset to an empty scene (File > Reset); refuses while the scene has work that was never saved.

### `resolve(ref: _mx.Node | str | int | dict[str, str | int]) -> _mx.Node`

Node by '/Parent/Child' path, name, handle or Node, or by a {'handle', 'name', 'path'} dict whose parts must agree.

### `save() -> str`

Save the scene to its own file and return the path; an untitled scene raises (never Save As).

### `scene_file() -> str | None`

Path of the open .max file, or None while the scene is untitled.

### `scene_info() -> dict`

The open scene: its file (None while untitled), whether it has unsaved changes, its object count and the faces of the evaluated meshes, triangulated. Keys: {'file', 'unsaved', 'objects', 'faces'}.

### `scene_patterns() -> dict`

How the scene combines classes: classes, modifier stacks and material, modifier and texmap pairings, by 3ds Max's display names. Every entry but total_nodes is {pattern: count}, most common first. Keys: {'total_nodes', 'geometry_classes', 'material_classes', 'modifier_classes', 'texmap_classes', 'modifier_stacks', 'material_on_geometry', 'modifier_on_geometry', 'texmap_in_material'}.

### `scene_qa(checks: str | collections.abc.Sequence[str] | None = None, nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None, fix: str | collections.abc.Sequence[str] | None = None, dry_run: bool = False, expected: _mx.Mark | int | None = None, far_from_origin: float | None = None) -> dict`

Scan scene hygiene (names, transforms, hierarchy, groups, timeline; no mesh checks) or repair it: scene_qa(fix=['name_collisions', 'empty_names']). checks and fix take one name or a list; nodes= limits the scope; expected= guards a fix as in patch(). A scan reports summary and issues [{'code', 'severity', 'message', 'node'?, 'details'?}]; a fix reports the scan before and what it planned (dry_run=True) or applied, with the scan after. Keys: {'action', 'mesh_checks_included', 'scope'?, 'scene_seq'?, 'scanned_nodes'?, 'checks'?, 'summary'?, 'issues'?, 'truncated'?, 'timeline'?, 'dry_run'?, 'scene_seq_before'?, 'scene_seq_after'?, 'fixes'?, 'before'?, 'planned'?, 'applied_count'?, 'applied'?, 'after'?, 'status'?, 'warnings'?}.

### `search_max_files(folder: str | os.PathLike, pattern: str = '*', recursive: bool = True) -> list[dict[str, str | int | list[str]]]`

The .max files under folder with objects named like pattern: [{'path', 'objects', 'names'}], objects being the file's object count and names the ones that match: search_max_files('D:/lib', 'chair*'); then merge(hit['path'], hit['names']).

### `set_frame(frame: float) -> None`

Move the time slider.

### `smart_import(folder: str | os.PathLike, texture_folder: str | os.PathLike | None = None, recursive: bool = True, grid_spacing: float = 200.0, lod_filter: str = 'lod0', name_pattern: str = '', exclude_pattern: str = '') -> dict`

Import the mesh files under folder onto an XY grid, pairing each with its textures by name: {'imported': [{'path', 'name', 'nodes', 'textures': {channel: path}, 'material_name', 'mtlx'}], 'skipped', 'failed', 'lod_skipped', 'filtered_out'}. Skips a file when an object named like its stem is in the scene. Keys: {'imported', 'skipped', 'failed', 'lod_skipped', 'filtered_out'}.

### `spline(points: collections.abc.Sequence[_mx.Knot | _mx.Point3 | dict[str, _mx.Point3 | str | None]], name: str = '', closed: bool = False, knot: str = 'smooth', center_pivot: bool = False) -> _mx.Node`

Create an editable spline from world points, dicts or Knots: spline([(0, 0, 0), (40, 0, 0), (40, 30, 0)], 'Rail', knot='corner'). knot: corner, smooth, bezier, bezier_corner or bounded (short handles that do not overshoot). At least two entries; a dict is {'pos', 'type', 'in_vec', 'out_vec', 'seg'} with only 'pos' required, and a bezier or bezier_corner knot needs both handles.

### `texmap_classes(pattern: str | None = None) -> list[str]`

Texture map class names texmaps.new() takes: texmap_classes('*bitmap*').

### `timeline(start: float | None = None, end: float | None = None, fps: int | None = None) -> tuple[float, float, int]`

Animation range and frame rate: (start, end, fps); pass any to change it.

### `undo() -> str | None`

Undo the last scene operation; returns the undo entry's name ('' if unnamed), or None when nothing was undone. In a run_python script, what the script changed so far becomes its own undo step first, so undo() undoes that, or the step before the script when it has changed nothing yet.

### `viewport(source: str = 'auto') -> _mx.Viewport | _mx.AgentViewport`

The AGENT VIEWPORT while it is open (an AgentViewport), else the user's active view (a Viewport); source='active' or 'agent' picks one: viewport().capture().

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

- `.capture(crop: tuple[int, int, int, int] | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: Save this view, or the VFB its V-Ray/Corona preview renders into, to an image; crop=(x, y, w, h) in VFB client pixels.
- `.frame(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None, padding: float = 1.15) -> None`: Fit nodes and their descendants (default: visible geometry, or the lights and cameras of a rig-only scene).
- `.minimize() -> None`: Park the window; restore() it before navigating or capturing.
- `.orbit(yaw: float = 0.0, pitch: float = 0.0) -> None`: Orbit the target by yaw and pitch degrees: orbit(30, 10).
- `.pan(x: float = 0.0, y: float = 0.0) -> None`: Move the view and its target by x, y scene units in the view plane.
- `.pick(x: float, y: float, capture: _mx.Capture | None = None) -> _mx.Hit | None`: The nearest surface under image point x, y (0-1 from the top-left) as a Hit, or None; capture refuses a view that changed since: pick(0.5, 0.4, cap).
- `.project(points: collections.abc.Sequence[_mx.Point3], capture: _mx.Capture | None = None) -> list[tuple[float, float, float] | None]`: Image positions (x, y, depth) of world points, x and y 0-1 from the top-left as pick takes them; None behind the eye; no occlusion test: project([(0, 0, 50)]...
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
- `.views` (list[str | _mx.Point3]): The tiles' views in order: names or (x, y, z) directions; [] for one plain view.
- `.width` (int): Image width in pixels.

### Change

A journal entry (a snapshot): seq, scene_seq, type ('added', 'name_changed', 'deleted', ...), node, name (when it happened), details.

- `.details` (dict[str, int | str | list[int]] | None): What else the event recorded, as a dict, or None: a 'scene_patch' event holds operation_count, changed_count and handles, a 'plugin_patch' event created and...
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
- `.name` (str): MAXScript name, as nodes.new() and classOf take it.
- `.nodes` (list[_mx.Node]): Scene nodes whose object, modifiers, material or controllers use the class.
- `.plugin` (_mx.Plugin | None): The plugin DLL that defines the class, or None.
- `.schema(*fields, query: str = '', offset: int = 0, limit: int = 256) -> dict`: Plugin inspector metadata: property refs, labels, units, domains, coverage: schema('radius'). properties holds one dict per parameter (those named, or all; q...
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

A group: a Node whose members move with it. groups['Chairs'] is an existing one, groups.new(nodes) makes one.

- `.add(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Add nodes to the group: group.add('Lamp').
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
- `.name` (str): The interface's name, as interfaces[name] takes it.

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

- `.add(nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str]) -> None`: Move nodes onto this layer: layer.add(nodes('Wall*')).
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
- `.nodes` (list[_mx.Node]): Nodes on this layer, not on its child layers; selection.set(layer.nodes) selects them.
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

- `.definition` (dict[str, list[list[list[float | str]]] | bool]): 'sections' as written (numbers and expressions) and the 'profile_closed', 'close_path', 'caps' and 'reverse' options.
- `.node` (_mx.Node): The node that carries this loft.
- `.origin` (_mx.Point3): World point the initial sections were centered on; the cage follows the node's transform since.
- `.parameters` (dict[str, float] | None): The parameters that built the current cage, or None when the cage was edited by hand (update refuses then).
- `.update(**parameters) -> dict[str, tuple[float, float]]`: Rebuild the cage for new parameter values, keeping vertex IDs, transform, modifiers and materials; returns {name: (before, after)} of what changed.

### Macro

A macroscript: name, category, tooltip, text, file, run().

- `.category` (str): The category it is filed under.
- `.file` (str): Source file.
- `.id` (int): Its ID in this 3ds Max session's macro directory.
- `.name` (str): The macroscript's name, as macros[name] and MAXScript's macros.run take it.
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
- `.remap_files(texture_folder: str = '', path_map: dict[str, str] | None = None, allow_missing: bool = False, preview: bool = False) -> list[dict[str, _mx.Material | _mx.Texmap | str | int | bool | None]]`: Repoint texture files in place, by file name in texture_folder or by path_map {old path or file name: new path}; returns one {'map', 'param', 'old', 'new'} p...
- `.replace_with(new: _mx.Material, sub_materials: bool = True) -> dict`: Put new wherever this material is: on nodes and in sub-material slots. Keys: {'nodes', 'slots', 'skipped'}.
- `.replicate(texture_folder: str = '', path_map: dict[str, str] | None = None, name: str = '', nodes: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None, allow_missing: bool = False) -> _mx.Material`: Copy the material, its texture files repointed to texture_folder or path_map {old path or file name: new path}, onto nodes, and return the copy: mat.replicat...
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
- `.add_spline(points: collections.abc.Sequence[_mx.Knot | _mx.Point3 | dict[str, _mx.Point3 | str | None]], closed: bool = False, knot: str = 'smooth', *, expected: str | None = None) -> _mx.Spline`: Add a spline from world points, dicts or Knots, as spline() takes them, to an editable shape (a hole, another outline); returns it.
- `.bbox` (_mx.Box | None): World bounding box ((min), (max)) of the evaluated geometry, or None.
- `.boolean(operands: _mx.Node | str | collections.abc.Sequence[_mx.Node | str] | None = None, operation: str = 'subtract', *, cutters: collections.abc.Sequence[dict[str, str | int | float | _mx.Point3]] | None = None, repeat: dict[str, int | float | str] | None = None, operations: collections.abc.Sequence[str] | None = None, option: str = '', method: str | None = None, voxel_size: float | None = None, live: bool = False, use_operand_material: bool = True, new_modifier: bool = False, modifier_name: str = '') -> _mx.Boolean`: Cut or fuse with a Boolean modifier and return it: wall.boolean(['Door', 'Window']) subtracts both. operation: union, subtract, intersect, merge, attach, ins...
- `.boolean_modifier` (_mx.Boolean | None): The topmost Boolean modifier on the stack, or None.
- `.children` (list[_mx.Node]): The nodes parented to this one, one level down.
- `.class_info` (_mx.ClassInfo): The object's class as a ClassInfo (a node's: its base object's): x.class_info.describe() lists what the class takes, x.class_info.plugin the DLL it comes from.
- `.class_name` (str): Class of the base object: Sphere, Editable_Poly, ...
- `.clone(mode: str = 'copy', name: str = '', offset: _mx.Point3 | None = None) -> _mx.Node`: Copy, instance or reference the node (and its children), moved by offset.
- `.conform_verts(target: _mx.Node | str | int, indices: collections.abc.Sequence[int] | None = None, *, axes: str = 'xyz', axis: str = '', strength: float = 1.0, spline: int = 1, convert: bool = False) -> tuple[int, int]`: Pull base vertices onto a spline (closest point on spline number `spline`) or a surface (ray along axis: x/y/z both ways, +x/-x/... one way, default z); axes...
- `.convert_to_poly() -> _mx.Poly`: Collapse the modifier stack into an Editable Poly (MAXScript convertToPoly) as one undo step; returns node.poly.
- `.convert_to_spline() -> None`: Collapse a shape to an editable spline (convertToSplineShape), discarding its modifier stack.
- `.curve_model(include_definition: bool = False) -> dict`: The node's curve_model recipe: kind, parameters, model_token, whether the geometry still matches it (reason says why not), and its definition with include_de...
- `.curve_token` (str): Token of the base knots, topology and object transform; pass it as expected= so edits refuse a changed shape.
- `.curve_update(parameters: dict[str, float], *, expected: str | None = None) -> dict`: Change curve_model parameters in place, keeping placement, materials and modifiers: node.curve_update({'bow': 6}, expected=node.curve_model()['model_token'])...
- `.delete() -> None`: Delete the node.
- `.dependents` (list[_mx.Node]): Nodes whose references reach this one directly: constraint, look-at and skin users.
- `.exists` (bool): False once the node is deleted.
- `.faces() -> list[tuple[int, int, int]]`: Triangles of the evaluated mesh as 1-based vertex IDs into verts(): create_mesh('Copy', n.verts(), n.faces()) copies it.
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
- `.info(query: str = '') -> list[dict]`: describe() as a list of dicts. Each has name, type and source (paramblock2, subanim or maxscript), and where they apply value, default, range, choices (with...
- `.interface(name: str) -> _mx.Interface`: A mixin interface of the object by name: mod.params.interface('unwrap2').
- `.interfaces` (list[_mx.Interface]): Mixin interfaces of the object these parameters belong to.
- `.keys() -> list[str]`: Parameter names, then transform/point subanims (gizmo, center), then MAXScript properties that are not parameters.
- `.patch(schema: dict, all_instances: bool = False, **values) -> dict`: Guarded edit, one undo step, refused if anything inspected changed: p.patch(s, multiplier=2.0). Enums take their names; all_instances=True allows editing a s...
- `.schema(*fields, query: str = '', offset: int = 0, limit: int = 256) -> dict`: Plugin inspector report with live values and the schema and state tokens patch() needs: p.schema('multiplier'). properties holds one dict per parameter (thos...
- `.to_dict() -> dict[str, float | int | bool | str | _mx.Point3 | tuple[float, float, float] | tuple[float, float, float, float] | _mx.Node | _mx.Material | _mx.Texmap | _mx.Object | None | list[float | int | bool | str | _mx.Point3 | tuple[float, float, float] | tuple[float, float, float, float] | _mx.Node | _mx.Material | _mx.Texmap | _mx.Object | None]]`: Every ParamBlock2 parameter and its value, by name; a list parameter gives a list.
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
- `.edit(ops: collections.abc.Sequence[dict[str, str | int | float | _mx.Point3 | collections.abc.Sequence[int]]], expected: str = '', show: bool = False) -> dict`: Run ops as one undo step, rolled back if one fails: edit([{'op': 'extrude', 'selection': ids, 'amount': 10}], expected=token). Each op has op, level ('vertex...
- `.faces(ids: collections.abc.Sequence[int] | None = None) -> list[list[int] | None]`: Vertex IDs of each face in winding order, all or of the given IDs.
- `.find(level: str, ids: collections.abc.Sequence[int] | None = None, current: bool = False, bbox: _mx.Box | None = None, near: _mx.Point3 | None = None, radius: float | None = None, normal: _mx.Point3 | None = None, angle: float | None = None, boundary: bool | None = None, sharp: float | None = None) -> list[int]`: IDs passing every filter given: among ids, selected (current), world center in bbox or within radius of near, face normal within angle (default 10) of normal...
- `.instances` (list[_mx.Node]): Nodes sharing this base, this one first; an edit changes them all.
- `.node` (_mx.Node): The node whose base this is.
- `.normals(ids: collections.abc.Sequence[int] | None = None, world: bool = True) -> list[_mx.Point3 | None]`: Unit face normals (Newell; (0, 0, 0) for a degenerate face), all or of the given face IDs.
- `.pick(x: float, y: float, view: str, level: str = 'edge', ids: collections.abc.Sequence[int] | None = None, tolerance: float = 0.025, limit: int = 5, prefer_surface: bool = True) -> dict`: Components near image point x, y (0-1, top-left) of the agent viewport capture whose token is view, within tolerance (fraction of the shorter side); ranked b...
- `.token` (str): Hash of the cage and its transform: edit(ops, expected=token) refuses IDs read before the mesh changed.
- `.verts(ids: collections.abc.Sequence[int] | None = None, world: bool = True) -> list[_mx.Point3 | None]`: Vertex positions, all or of the given IDs; None for a deleted slot.

### RenderJob

A production render queued by render(): job.wait() renders it now; else it starts when the script returns.

- `.cancel() -> bool`: Drop the job if it has not started; returns whether it was dropped.
- `.done` (bool): True once the job ended (done, failed or cancelled).
- `.error` (str | None): Why the render failed, else None.
- `.id` (str): The job's ID, as render_automations(job_id=...) takes it.
- `.result` (dict | None): What wait() returns, {'status', 'output', 'width', 'height', 'seconds'}, once done, else None.
- `.signal_path` (str): Done file written when the job ends; render_automations(action='status', job_id=job.id) reads it outside Max.
- `.status` (str): 'queued', 'rendering', 'done', 'failed' or 'cancelled'.
- `.wait() -> dict`: Render now if still queued and return the result; raises if it failed, was cancelled, or already went to Max. Keys: {'status', 'output', 'width', 'height', '...

### Segment

A spline segment snapshot: type (line or curve), world length, and its cubic's control points.

- `.length` (float): World length of the segment.
- `.points` (tuple[_mx.Point3, _mx.Point3, _mx.Point3, _mx.Point3]): (start, start handle, end handle, end) in world space; a line's handles lie on its chord.
- `.type` (str): line or curve.

### SelectionSet

A named selection set, held by name: s.nodes = nodes replaces it; selection.set(s.nodes) selects it.

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
- `.pick(x: float, y: float, view: str, tolerance: float = 0.025, knots: collections.abc.Sequence[int] | None = None) -> list[dict[str, int | str | float | _mx.Point3 | tuple[float, float]]]`: Knots and handles near image point (x, y) of a capture (fractions 0-1), nearest first; view is the capture's view_token. Overlapping front and back handles s...
- `.qa(tolerance: float = 0.01) -> dict`: Sampled QA report: length, planarity, self-intersections, tangent breaks and segments that backtrack. complete is False, with a reason, for a spline it could...
- `.reverse(*, expected: str | None = None) -> None`: Reverse the knot order, keeping the first knot first.
- `.sample(count: int) -> list[_mx.Point3]`: count points evenly spaced by length along the spline, world space (closed: no repeated end).
- `.segments` (list[_mx.Segment]): The segments as Segment snapshots, segment i leaving knot i.
- `.set_knot(index: int, *, pos: _mx.Point3 | None = None, in_vec: _mx.Point3 | None = None, out_vec: _mx.Point3 | None = None, type: str | None = None, seg: str | None = None, expected: str | None = None) -> None`: Change one knot; a moved knot carries its handles: set_knot(2, pos=(10, 0, 0)), set_knot(2, type='bezier_corner', out_vec=(14, 3, 0)).
- `.set_knots(knots: collections.abc.Sequence[_mx.Knot | _mx.Point3 | dict[str, _mx.Point3 | str | None]], *, expected: str | None = None) -> None`: Rewrite every knot at once from Knots, dicts or points (one per knot); checked before anything is written. A dict has 'pos' and any of 'type', 'in_vec', 'out...

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
- `.capture_frames(frames: int | collections.abc.Sequence[int], view: str | _mx.Point3 = 'current', subject: _mx.Node | str | int | None = None, shading: str | None = None, edges: bool | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: One view over time in one strip: a frame count spread over the range or up to 12 frame numbers; a named view or an (x, y, z) eye direction is framed over all...
- `.capture_views(views: str | collections.abc.Sequence[str | _mx.Point3] | None = None, subject: _mx.Node | str | int | None = None, shading: str | None = None, edges: bool | None = None, method: str = 'auto', max_width: int = 1600, max_height: int = 0) -> _mx.Capture`: Up to 8 framed views in one grid (default front, right, back, top; also perspective, three_quarter, current, (x, y, z) eye directions); subject (a node, its...
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

- `BUILD` = 'b20261111233756'
- `MAX_VERSION` = 2027
