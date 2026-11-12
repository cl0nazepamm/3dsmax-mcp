# tyFlow Graphs

Read this reference completely before building, inspecting, editing, or verifying tyFlow event graphs.
The tyFlow tools' manuals cover their own arguments; this holds what they do not say.

## Limits

- Shape, Birth Burst, Actor and Spline Paths have no Operator interface in MAXScript (`op.Operator` fails), so `execute_maxscript` cannot rename, enable or remove them. Use the tyFlow tools for these edits.
- Value and Global operators stand alone; every other operator needs an event of its own family.
- tyFlow graph edits are outside Max's undo: `undo_last` does not undo them, and a `run_python` script that fails does not roll them back, so delete a flow you no longer need with `delete_objects`. Build events and operators with the tyFlow tools, never with `rt` inside a `run_python` script: with tyFlow 2.101, rolling such edits back crashes 3ds Max at its next save or hold.

## Editing

- Always pass explicit operator `name`s in `add_operator`: edges and `operator_types` key operators as `"Event/OpName"`. Its position is 0-based; `-1` appends.
- There is no compile step, so structural success does not imply behavioral success: verify with `verify_frames`.
- A checkpoint rollback swaps the flow node, which breaks references other scene objects hold to the flow (e.g. tyMesher): use `checkpoint=false` when such references exist.
- Prefer additive patches plus a follow-up destructive patch over mixed batches: additive batches avoid clone churn entirely, which matters for tyFlow stability.
- Use manifest type and property names verbatim — tyFlow PB2 names are non-obvious (`shape_type_tab`, `type_3d_ID_tab`, `frequency_tab`), and export operators are named `Export Particles`, `Export Terrain`, `Export VDB`, `Export Inferno`. Never guess.
- `modify_tyflow_operator(raw_values=true)` takes MAXScript expressions, e.g. `{"objectList": "#($Name)"}` for node lists.

## Script operator (C#)

- Work through `tyflow_script`, never through `set_properties` or `execute_maxscript`: tyFlow compiles a Script operator once and ignores later code changes, and it reports neither compile errors nor runtime exceptions on its own.
- Loop particles with `for (int i = 0; i < eventParticleCount; i++) { int s = tf.GetSimIndex(i); ... }`; every `tf.Get*`/`tf.Set*` call takes the simulation index `s`.
- tyFlow keeps at most 100 `Print()` lines per step, and stops calling a callback that throws.
- `set` on an operator that already compiled replaces it in place (same name, position, settings and wiring) so the new code compiles; read the graph again before the next patch.
- Call `objects[i].PrepAPI()` before `NumVerts`/`GetVert`/`GetFace`; without it they report 0 vertices. `GetFace` returns 0-based `v1/v2/v3` that match `GetVert`.
- Scripts cannot build meshes (`tfMesh` is read-only). For a surface, rasterize particles with Particles to VDB, or deform a twin mesh with a `tyParticleSkin` modifier: `particleNode` = the flow, `maxCount` 1 and a small `distance` bind each vertex to its own particle, which must sit on the vertex at `refFrame`.
- Keep scripts small: a ~90 KB script with a large embedded data literal reported `compiled` false with no diagnostics, while 8 KB compiled. Compute data in `simulationStart` instead.

## VDB events

- VDB operators go in their own event, started by `Birth VDB`; it needs no wire from particle events (particle-to-VDB wires are refused). `Particles to VDB` with `includeThis=true` reads this flow's particles from any event.
- Most VDB operators (`Particles to VDB`, `VDB Clear`, `VDB Filter`, ...) default to `timingInterval=0`, which runs them once, at the VDB's birth. Set `timingInterval=1` (continuous) on every operator that must update each step, or the grid stays empty or frozen.
- `Particles to VDB` `gridType` 0 is the SDF grid (`sdfGridName`); `VDB Mesh` `inputMode` 0 meshes it at `sdfISO`. `radiusMode` 0 is an absolute `radius` (particles need no shape for it); 1 uses each particle's scale as its diameter.
- Simulation groups split one event: a Script operator sets `tf.SetSimulationGroups(s, 1 << (group - 1))`. Particles to VDB with group boxes checked rasterizes only those groups (all unchecked means every particle); a Display with a group checked shows only that group in the viewport, while renders include all.
- Continuous rasterizing accumulates (that is how trails form); put a continuous `VDB Clear` first for a grid rebuilt each step.
- Start with a voxel size near the particle spacing and refine. Check output with a capture or `snapshotAsMesh` face count at a frame.
- VDB rebuilds are expensive (a 350-unit tree at voxel 1.0 cost ~2 s per frame). Once the source stops changing, end `timingIntervalEnd` on VDB Clear, Particles to VDB and VDB Filter; the mesh holds.

## Splines and cloth

- Spline Paths sends to a `tySplines` object through `splinePathsNode`, an internal NodeMonitor: assigning a node is refused, and constructing a NodeMonitor in MAXScript crashed Max. Reuse an already linked flow and tySplines pair (for example from a merged example scene); the tySplines object lists its flows in `flowNodes`, `flows` and `flowInfo`.
- Spline Paths modes, per the docs: Trajectories, Siblings, Connect to parent, Parent, Target, Neighbors, Particle bindings, PhysX bindings. Only mode 0 (Trajectories) is verified; index 4 with a `target` float channel produced no splines (unresolved).
- Trajectories on tens of thousands of particles under a tySplineMesher produced 1.4M vertices and stalled Max. Test spline modes on a handful of particles.
- Cloth recipe (tyFlow example): Birth Objects (hidden source mesh) → Cloth Bind → Force (small gravity, e.g. -0.25) → Collision (`colliderList`, Bounce 0, high friction). Cloth Bind generates the cloth mesh.
- Against collision poke-through, raise Collision `radius` and `offset` and use a finer flow `timeStep` (2 measured at twice the cost of 1, same motion).
- Cloth Bind `enableCollisions` makes the cloth ignore Collision operators, and switching it back off on a cached sim crashed Max (seen once). Reset the sim at frame 0 before editing Cloth Bind.

## Per-event census

- `tyflow_event_census` with `method="groups"` misreports particles that accumulated bits across multiple events; prefer the default mapping method.

## Physics gotchas (live-verified, tyFlow 2.05 / Max 2027)

- Speed `directionMode` 0/1 ("Along Icon Arrow" / "Icon Center Out") produce a ZERO vector on a default flow icon — the operator silently contributes nothing (and divergence spreads nothing). Use mode 8 ("Custom vector direction", defaults up) for directional jets; the operator default is 3 ("Random 3D").
- Force `gravityStrength` is signed with POSITIVE pushing +Z (up). Downward gravity needs a negative value. Working fountain reference: Speed magnitude 25 (±6) with gravityStrength -2.6 gives ~1m arcs and ~18-frame flight.
- Birth emits at the flow icon's world position; moving the flow NODE did not relocate births in testing. Never place a collider surface at the birth plane — particles born touching a collider are captured instantly (jet velocity killed the same frame), which mimics "operators have no effect".
- Diagnose sims with data, not guesses: `getParticlePosition`/`getParticleVelocity` on the newest particle (index = numParticles) exposes launch state; identical census counts across setting changes means the settings are not reaching the behavior you think they are.
- `flow.baseobject.cacheEnable = false` plus `reset_simulation()` is a safe diagnostic step to rule out stale caching during iteration.

## Runtime rules

- tyFlow's dispatch exposes spaced names underscored: an operator displayed "Send Out" reads back as `Send_Out`, and graph reads and edges use that underscore form canonically. Tools accept either form in lookups; expect underscores in responses.
- Event/operator lookups are name-based: duplicate operator names within an event make edges and patch targets ambiguous — keep names unique.
- Probe gently: never loop create→simulate→delete of tyFlow objects inside a single MAXScript program — tyFlow's Qt cleanup can deadlock Max (live-confirmed). Reuse one scratch flow, mutate in place, pump messages (`windows.processPostedMessages()`) between sim iterations, delete once at the end.
- tyFlow simulates only inside the scene animation range; later frames hold the last state.
- tyFlow 2.101 adds operators from MAXScript only to a flow whose editor is open: `addOperator` otherwise returns undefined for every type. The tools handle this; raw MAXScript must call `flow.tyFlow.editor_open()` first, and should close it only if it was not already open (its window is titled `<flow name> : [tyFlow ... Editor]`).
- In MAXScript, reach operators as `flow.baseobject[#EventName][#Operator_Name]`; tyFlow nodes have no `.events` property.
- `inspect_max_file` lists a .max file's objects without opening it; operator settings need the flow loaded (merge it, read it, delete the merged objects).
- Probe/scratch objects are named `zzz_mcp_*` and are cleaned up by the tools; if a crash leaves one behind, delete it manually.
- `manage_scene(hold)` before risky sessions still protects everything.
