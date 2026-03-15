# Native C++ Feature Ideas

Things we can do with the C++ SDK that are **impossible or impractical via MAXScript**.
These are features that would make the MCP truly next-level.

---

## 1. Direct Mesh Surgery

MAXScript can read vertex positions but it's painfully slow for large meshes and can't do real topology edits. The SDK gives us `MNMesh` (350+ methods) and `Mesh` with direct memory access.

**Ideas:**
- **`native:get_mesh_data`** — Dump vertex positions, face topology, UVs, normals as binary or compressed JSON. A 100K vertex mesh that takes 30s in MAXScript could return in <100ms
- **`native:weld_vertices`** — Weld by threshold without going through the modifier stack
- **`native:mesh_boolean`** — Direct boolean operations on MNMesh without the Boolean modifier
- **`native:retopology_stats`** — Face count, triangle distribution, edge flow analysis, ngon detection, non-manifold detection — all in one fast call
- **`native:uv_analysis`** — UV island count, overlap detection, distortion stats, seam length — things that take minutes in MAXScript
- **`native:vertex_paint`** — Direct vertex color channel writes without VertexPaint modifier

**Key classes:** `MNMesh`, `Mesh`, `ObjectWrapper`, `MNMapFace`, `MNNormalSpec`

---

## 2. Real-Time Viewport Overlays

MAXScript has zero access to viewport drawing. The SDK has `GraphicsWindow` with 160+ methods for direct GPU rendering.

**Ideas:**
- **Live measurement overlay** — Draw dimensions, angles, distances directly in the viewport as the user moves objects
- **Wireframe highlight** — Custom colored wireframe overlays on specific objects (selection preview, error highlighting)
- **3D annotations** — Floating text labels attached to objects in viewport space
- **UV visualization** — Checkerboard/distortion heatmap rendered directly on meshes without materials
- **Hierarchy visualization** — Draw parent-child connection lines in the viewport
- **Grid/snap preview** — Show placement guides before object creation

**Key classes:** `GraphicsWindow`, `IDisplayCallback`, `IPrimitiveRenderer`, `ViewExp`

---

## 3. Scene Event Streaming

MAXScript has limited callbacks. The SDK has a deep notification/event system that can track everything happening in the scene in real-time.

**Ideas:**
- **`native:watch_scene`** — Stream scene changes (object added/deleted/moved/modified) as events to the MCP without polling
- **`native:undo_stream`** — Track undo/redo operations with full context of what changed
- **`native:selection_changed`** — Push notification when selection changes instead of polling `get_selection`
- **`native:render_progress`** — Stream render progress, bucket completion, time estimates

**Key classes:** `INodeEvent`, `ISceneNodeEvent`, `IReferenceTargetEvent`, `UndoNotify`

---

## 4. Direct Bitmap/Texture Memory Access

MAXScript can load bitmaps but can't read or write pixels efficiently. The SDK gives raw pixel buffer access.

**Ideas:**
- **`native:read_texture_pixels`** — Read actual pixel data from any texture in the scene. Check if textures are blank, analyze color distribution, detect issues
- **`native:generate_texture`** — Procedurally generate textures (gradients, noise, patterns) and assign them directly — no file I/O needed
- **`native:texture_atlas`** — Combine multiple textures into an atlas with UV remapping
- **`native:screenshot_to_memory`** — Capture viewport to memory buffer and return as base64 without temp file round-trip
- **`native:render_element_data`** — Read individual render elements (Z-depth, normals, object ID) as raw data for analysis

**Key classes:** `Bitmap`, `BitmapManager`, `IRenderElement`, `GBuffer`

---

## 5. Animation System Deep Access

MAXScript exposes keys and controllers but the SDK goes much deeper with direct curve manipulation, IK solving, and controller decomposition.

**Ideas:**
- **`native:fcurve_analysis`** — Analyze animation curves: detect overshoots, pops, discontinuities, extreme values. Quality check before render
- **`native:bake_animation`** — Bake controller trees to keyframes at native speed (10-100x faster than MAXScript)
- **`native:retarget_skeleton`** — Direct bone-to-bone animation retargeting using SDK's IK and constraint solving
- **`native:animation_diff`** — Compare two animation states and return exact differences per controller
- **`native:ease_curve_edit`** — Direct ease/multiplier curve manipulation that MAXScript can't touch

**Key classes:** `Animatable`, `Control`, `IKeyControl`, `TrackClipObject`

---

## 6. Multi-Threaded Scene Processing

MAXScript is single-threaded and blocks the UI. The SDK supports worker thread pools for parallel processing.

**Ideas:**
- **Parallel mesh analysis** — Analyze all meshes in the scene concurrently (vertex counts, UV stats, material assignments)
- **Background scene validation** — Run full scene health checks without freezing Max
- **Batch texture loading** — Load and analyze all scene textures in parallel
- **Parallel modifier evaluation** — Pre-evaluate modifier stacks across objects simultaneously

**Key classes:** `IMainThreadTaskManager`, `WorkerThreadSet`, `ThreadTools`

---

## 7. Scene Health & Optimization Engine

Combine multiple SDK capabilities for a "scene doctor" that MAXScript simply can't do fast enough.

**Ideas:**
- **`native:scene_health`** — One-call deep analysis:
  - Degenerate faces (zero-area triangles)
  - Non-manifold geometry
  - Flipped normals
  - Missing texture files
  - Unused materials
  - Orphaned bones
  - Overlapping UVs
  - Objects at extreme distances from origin
  - Excessive modifier stack depth
- **`native:optimize_scene`** — Auto-fix common issues:
  - Remove degenerate faces
  - Weld coincident vertices
  - Reset xforms on misaligned objects
  - Collapse unnecessary modifier stacks
  - Clean empty layers and groups

---

## 8. Custom Geometry Generation Pipeline

The SDK allows creating geometry directly in memory without going through MAXScript's create/modify cycle.

**Ideas:**
- **`native:create_mesh_from_data`** — Send vertex/face arrays directly, create a mesh in one call. Build complex procedural geometry server-side
- **`native:point_cloud_to_mesh`** — Convert point data to mesh (Delaunay triangulation, Poisson reconstruction)
- **`native:sweep_profile`** — Sweep a 2D profile along a spline with full control over banking, scaling, twist — faster than the Sweep modifier
- **`native:scatter_geometry`** — Instance placement with collision detection and orientation, all computed natively

**Key classes:** `MNMesh`, `PolyObject`, `TriObject`, `GenericShape`

---

## 9. File & Asset Intelligence

Direct access to 3ds Max file internals and asset resolution that MAXScript can't reach.

**Ideas:**
- **`native:scan_max_file`** — Read metadata from a .max file WITHOUT opening it: object count, textures referenced, plugins required, file version, scene stats
- **`native:asset_audit`** — Crawl all asset references (textures, proxies, caches, IES files) and check which are missing, broken, or absolute paths
- **`native:dependency_graph`** — Full reference graph export showing how every object, material, controller, and modifier is connected

**Key classes:** `AssetEnumCallback`, `ISave`/`ILoad`, `BitmapManager`

---

## 10. Hardware Mesh & GPU Direct

The SDK has direct GPU mesh access for viewport rendering performance.

**Ideas:**
- **`native:gpu_mesh_stats`** — Report GPU memory usage per object, triangle strip efficiency, draw call count
- **`native:viewport_perf`** — Profile viewport FPS, identify which objects are most expensive to display
- **`native:lod_preview`** — Generate and preview LOD levels using GPU-side mesh decimation

**Key classes:** `HardwareMesh`, `HardwareMNMesh`, `IRenderMeshCache`

---

## Priority Ranking

| # | Feature | Impact | Effort | MAXScript Gap |
|---|---------|--------|--------|---------------|
| 1 | Direct Mesh Surgery | Massive | Medium | Complete — MAXScript is 100x slower |
| 2 | Scene Health Engine | Massive | Medium | Most checks impossible in MAXScript |
| 3 | Viewport Overlays | High | Hard | Zero MAXScript access |
| 4 | Scene Event Streaming | High | Medium | Polling vs. push |
| 5 | Bitmap Memory Access | High | Easy | MAXScript can't read pixels |
| 6 | Animation Deep Access | Medium | Medium | Partial MAXScript coverage |
| 7 | File Intelligence | Medium | Easy | Can't read .max without opening |
| 8 | Geometry Generation | Medium | Medium | MAXScript is slow for large meshes |
| 9 | Multi-Threaded Processing | Medium | Hard | MAXScript is single-threaded |
| 10 | GPU Direct | Low | Hard | Niche use cases |
