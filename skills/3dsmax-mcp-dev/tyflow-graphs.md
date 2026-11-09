# tyFlow Graphs

Read this reference completely before building, inspecting, editing, or verifying tyFlow event graphs.

## What MAXScript cannot do, and the tools can

- tyFlow has no MAXScript getter for wiring or operator types. `get_tyflow_graph` reads both from the flow itself: `edges` (from_event, from_op, to_event) and each operator's `type` are current, including wires drawn in the tyFlow editor and operators renamed by the user. Never reconstruct wiring from names or captures when `wiring_source` is `scene`.
- Shape, Birth Burst, Actor and Spline Paths have no Operator interface in MAXScript (`op.Operator` fails), so `execute_maxscript` cannot rename, enable or remove them. The tyFlow tools handle them like any other operator; prefer the tools for these edits.
- An event holds operators of one family, set by its first operator: particle, terrain, VDB or Inferno (Value and Global stand alone). An operator of another family is refused with an error, never silently dropped. Check `family` in `list_tyflow_operators` and give each family its own event.
- tyFlow graph edits are outside Max's undo: `undo_last` does not undo them, and a `run_python` script that fails does not roll them back, so delete a flow you no longer need with `delete_objects`. Build events and operators with the tyFlow tools, never with `rt` inside a `run_python` script: with tyFlow 2.101, rolling such edits back crashes 3ds Max at its next save or hold.

## The agentic loop

- Read with `get_tyflow_graph` — events (name/position/width/enabled), operators with `type` and `enabled`, optional properties, `edges`, `operator_types`, `graph_hash` and `wiring_source`.
- `graph_hash` covers wiring as well as structure, but not event positions, which tyFlow adjusts by itself. A hash conflict in `tyflow_apply_patch` means the flow changed since the read (often a user edit in the editor): re-read before retrying.
- Edit transactionally with `tyflow_apply_patch`, passing `expected_hash` from the last `get_tyflow_graph`.
- Patch ops: `add_event`, `remove_event`, `add_operator`, `remove_operator`, `set_properties`, `connect`, `disconnect`, `rename_event`, `rename_operator`, `set_event_position`, `set_enabled`. Always pass explicit operator `name`s in `add_operator`: edges and `operator_types` key operators as `"Event/OpName"`.
- Verify empirically: `verify_frames` + `min_particles`/`max_particles` resets the sim and asserts counts; there is no compile step, so structural success does not imply behavioral success.
- On failure, additive-only patches (add_event/add_operator/connect/set_event_position) roll back via synthesized inverse operations (`rollback_mode: inverse`) — no node clone involved. Patches containing other ops use a checkpoint clone (`rollback_mode: checkpoint`); that node swap breaks references other scene objects hold to the flow (e.g. tyMesher) — use `checkpoint=false` when such references exist.
- Prefer additive patches plus a follow-up destructive patch over mixed batches: additive batches avoid clone churn entirely, which matters for tyFlow stability.
- Single edges outside a patch: `connect_tyflow_operator` / `disconnect_tyflow_operator` (any test or Send Out operator; `create_if_missing=true` adds a missing Send Out). Re-connecting re-targets: the edge for that (event, operator) is replaced.

## Operator manifest

- `harvest_tyflow_manifest` probes every known operator type in a scratch flow and caches per tyFlow version; run once per installation (or `refresh=true` after a tyFlow update). `list_tyflow_operators` queries the cache with no Max traffic and reports each type's `available` and `family`.
- Use manifest type and property names verbatim — tyFlow PB2 names are non-obvious (`shape_type_tab`, `type_3d_ID_tab`, `frequency_tab`), and export operators are named `Export Particles`, `Export Terrain`, `Export VDB`, `Export Inferno`. Never guess.
- `Script` and `MAXScript` operators are executable code: `tyflow_apply_patch` and `tyflow_script(action="set")` fail closed unless MCP safe mode is disabled AND `allow_executable=true`. Creating them empty via harvest probing is inert and allowed.

## Script operator (C#)

- The Script operator runs C# per simulation step, for custom solvers. Work through `tyflow_script`, never through `set_properties` or `execute_maxscript`: tyFlow compiles a Script operator once and ignores later code changes, and it reports neither compile errors nor runtime exceptions on its own.
- The loop: `action="api"` (the installed tyFlow's API: `tf`, `f`, `eventParticleCount`, `Print`, `GetFloat`, `Point3`, `Matrix3`, `tfObj`…) → write the code → `action="check"` until there are no diagnostics → `action="set"` with `allow_executable=true` (safe mode off) and `frames` → read `run`. Repeat until `compiled` is true, `exceptions` is empty and `output` shows the intended values.
- Declare `simulationStart()`, `simulationStep()` and `simulationStepThreaded(int startInx, int endInx, int threadInx)`, even when empty; tyFlow refuses scripts without them. `postSimulationStep()` is optional.
- Loop particles with `for (int i = 0; i < eventParticleCount; i++) { int s = tf.GetSimIndex(i); ... }`; every `tf.Get*`/`tf.Set*` call takes the simulation index `s`.
- `Print()` lines come back in `output` (tyFlow keeps at most 100 per step); use them to verify values. A callback that throws is reported in `exceptions` and tyFlow stops calling it.
- Never put `%` in `Print`/`PrintAll` text, including `ToString("P")` and `{x:P}`: tyFlow formats it printf-style and 3ds Max crashes. Write `pct` or print a ratio.
- `set` on an operator that already compiled replaces it in place (same name, position, settings and wiring) so the new code compiles; read the graph again before the next patch.
- Script objects are `obj001`… and texmaps `tex001`… in listbox order, and `GetFloat(name)` reads an enabled float slot; `action="get"` lists them.
- Call `objects[i].PrepAPI()` before `NumVerts`/`GetVert`/`GetFace`; without it they report 0 vertices. `GetFace` returns 0-based `v1/v2/v3` that match `GetVert`.
- Scripts cannot build meshes (`tfMesh` is read-only). For a surface, rasterize particles with Particles to VDB, or deform a twin mesh with a `tyParticleSkin` modifier: `particleNode` = the flow, `maxCount` 1 and a small `distance` bind each vertex to its own particle, which must sit on the vertex at `refFrame`.
- Keep scripts small: a ~90 KB script with a large embedded data literal reported `compiled` false with no diagnostics, while 8 KB compiled. Compute data in `simulationStart` instead.

## VDB events

- VDB operators go in their own event, started by `Birth VDB`; it needs no wire from particle events (particle-to-VDB wires are refused). `Particles to VDB` with `includeThis=true` reads this flow's particles from any event.
- Most VDB operators (`Particles to VDB`, `VDB Clear`, `VDB Filter`, ...) default to `timingInterval=0`, which runs them once, at the VDB's birth. Set `timingInterval=1` (continuous) on every operator that must update each step, or the grid stays empty or frozen.
- `Particles to VDB` `gridType` 0 is the SDF grid (`sdfGridName`); `VDB Mesh` `inputMode` 0 meshes it at `sdfISO`. `radiusMode` 0 is an absolute `radius`; particles need no shape for it.
- Its simulation/export group boxes all unchecked mean every particle; checked boxes restrict to particles in those groups. A Script operator can route particles with `tf.SetSimulationGroups(s, 1 << (group - 1))`.
- Continuous rasterizing accumulates (that is how trails form); put a continuous `VDB Clear` first for a grid rebuilt each step.
- Start with a voxel size near the particle spacing and refine. Check output with a capture or `snapshotAsMesh` face count at a frame.
- `Particles to VDB` `radiusMode` 1 uses each particle's scale as its diameter; 0 uses the absolute `radius`.
- Simulation groups split one event: a script sets `tf.SetSimulationGroups(s, bits)`; a Display with a group checked shows only that group (viewport only, renders include all), and Particles to VDB with a group checked rasterizes only that group.
- VDB rebuilds are expensive (a 350-unit tree at voxel 1.0 cost ~2 s per frame). Once the source stops changing, end `timingIntervalEnd` on VDB Clear, Particles to VDB and VDB Filter; the mesh holds.

## Splines and cloth

- Spline Paths sends to a `tySplines` object through `splinePathsNode`, an internal NodeMonitor: assigning a node is refused, and constructing a NodeMonitor in MAXScript crashed Max. Reuse an already linked flow and tySplines pair (for example from a merged example scene); the tySplines object lists its flows in `flowNodes`, `flows` and `flowInfo`.
- Spline Paths modes, per the docs: Trajectories, Siblings, Connect to parent, Parent, Target, Neighbors, Particle bindings, PhysX bindings. Only mode 0 (Trajectories) is verified; index 4 with a `target` float channel produced no splines (unresolved).
- Trajectories on tens of thousands of particles under a tySplineMesher produced 1.4M vertices and stalled Max. Test spline modes on a handful of particles.
- Cloth recipe (tyFlow example): Birth Objects (hidden source mesh) → Cloth Bind → Force (small gravity, e.g. -0.25) → Collision (`colliderList`, Bounce 0, high friction). Cloth Bind generates the cloth mesh.
- Against collision poke-through, raise Collision `radius` and `offset` and use a finer flow `timeStep` (2 measured at twice the cost of 1, same motion).
- Cloth Bind `enableCollisions` makes the cloth ignore Collision operators, and switching it back off on a cached sim crashed Max (seen once). Reset the sim at frame 0 before editing Cloth Bind.

## Per-event census

- No API reports which event a particle is in. `tyflow_event_census` instruments each event with a temporary `Mapping` operator (ordinal stamped into UVW channel 99 by default), resets, evaluates probe frames, buckets per-particle reads, then removes the instrumentation. It mutates the flow during the call — opt-in diagnostic, not a free read.
- `method="groups"` (export-group bits) is limited to 16 events and misreports particles that accumulated bits across multiple events; prefer the default mapping method.
- The response's `instrumentation` block names the properties actually resolved on the Mapping operator — check it before trusting zeros.

## Older bridges (`wiring_source: ledger`)

- A bridge that cannot read the flow reports edges from an MCP ledger instead: `ledger_status` is `fresh` (matches the last MCP edit), `stale` (edited outside MCP; edges may be outdated) or `absent` (no MCP edits yet).
- To reconcile, `capture_tyflow_editor` (requires `enabled=true`) captures the editor with per-event rectangles; read the wires from the image and record them with `set_tyflow_wiring_ledger` (mode `replace` or `merge`). With `wiring_source: scene` neither is needed.

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
- `add_operator` position is 0-based; `-1` appends.
- tyFlow simulates only inside the scene animation range; later frames hold the last state.
- tyFlow 2.101 adds operators from MAXScript only to a flow whose editor is open: `addOperator` otherwise returns undefined for every type. The tools handle this; raw MAXScript must call `flow.tyFlow.editor_open()` first, and should close it only if it was not already open (its window is titled `<flow name> : [tyFlow ... Editor]`).
- In MAXScript, reach operators as `flow.baseobject[#EventName][#Operator_Name]`; tyFlow nodes have no `.events` property.
- `modify_tyflow_operator(raw_values=true)` takes MAXScript expressions, e.g. `{"objectList": "#($Name)"}` for node lists.
- `inspect_max_file` lists a .max file's objects without opening it; operator settings need the flow loaded (merge it, read it, delete the merged objects).
- Census and patch verification call `reset_simulation` — cached sim state is discarded. Never fire renders from these tools.
- Probe/scratch objects are named `zzz_mcp_*` and are cleaned up by the tools; if a crash leaves one behind, delete it manually.
- `manage_scene(hold)` before risky sessions still protects everything.
