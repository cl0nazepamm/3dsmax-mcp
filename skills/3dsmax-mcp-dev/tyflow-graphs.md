# tyFlow Graphs

Read this reference completely before building, inspecting, editing, or verifying tyFlow event graphs.

## What MAXScript cannot do, and the tools can

- tyFlow has no MAXScript getter for wiring or operator types. `get_tyflow_graph` reads both from the flow itself: `edges` (from_event, from_op, to_event) and each operator's `type` are current, including wires drawn in the tyFlow editor and operators renamed by the user. Never reconstruct wiring from names or captures when `wiring_source` is `scene`.
- Shape, Birth Burst, Actor and Spline Paths have no Operator interface in MAXScript (`op.Operator` fails), so `execute_maxscript` cannot rename, enable or remove them. The tyFlow tools handle them like any other operator; prefer the tools for these edits.
- An event holds operators of one family, set by its first operator: particle, terrain, VDB or Inferno (Value and Global stand alone). An operator of another family is refused with an error, never silently dropped. Check `family` in `list_tyflow_operators` and give each family its own event.

## The agentic loop

- Read with `get_tyflow_graph` — events (name/position/width/enabled), operators with `type` and `enabled`, optional properties, `edges`, `operator_types`, `graph_hash` and `wiring_source`.
- `graph_hash` covers wiring as well as structure. A hash conflict in `tyflow_apply_patch` means the flow changed since the read (often a user edit in the editor): re-read before retrying.
- Edit transactionally with `tyflow_apply_patch`, passing `expected_hash` from the last `get_tyflow_graph`.
- Patch ops: `add_event`, `remove_event`, `add_operator`, `remove_operator`, `set_properties`, `connect`, `disconnect`, `rename_event`, `rename_operator`, `set_event_position`, `set_enabled`. Always pass explicit operator `name`s in `add_operator`: edges and `operator_types` key operators as `"Event/OpName"`.
- Verify empirically: `verify_frames` + `min_particles`/`max_particles` resets the sim and asserts counts; there is no compile step, so structural success does not imply behavioral success.
- On failure, additive-only patches (add_event/add_operator/connect/set_event_position) roll back via synthesized inverse operations (`rollback_mode: inverse`) — no node clone involved. Patches containing other ops use a checkpoint clone (`rollback_mode: checkpoint`); that node swap breaks references other scene objects hold to the flow (e.g. tyMesher) — use `checkpoint=false` when such references exist.
- Prefer additive patches plus a follow-up destructive patch over mixed batches: additive batches avoid clone churn entirely, which matters for tyFlow stability.
- Single edges outside a patch: `connect_tyflow_operator` / `disconnect_tyflow_operator` (any test or Send Out operator; `create_if_missing=true` adds a missing Send Out). Re-connecting re-targets: the edge for that (event, operator) is replaced.

## Operator manifest

- `harvest_tyflow_manifest` probes every known operator type in a scratch flow and caches per tyFlow version; run once per installation (or `refresh=true` after a tyFlow update). `list_tyflow_operators` queries the cache with no Max traffic and reports each type's `available` and `family`.
- Use manifest type and property names verbatim — tyFlow PB2 names are non-obvious (`shape_type_tab`, `type_3d_ID_tab`, `frequency_tab`), and export operators are named `Export Particles`, `Export Terrain`, `Export VDB`, `Export Inferno`. Never guess.
- `Script` and `MAXScript` operators are executable code: `tyflow_apply_patch` fails closed unless MCP safe mode is disabled AND `allow_executable=true`. Creating them empty via harvest probing is inert and allowed.

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
- Census and patch verification call `reset_simulation` — cached sim state is discarded. Never fire renders from these tools.
- Probe/scratch objects are named `zzz_mcp_*` and are cleaned up by the tools; if a crash leaves one behind, delete it manually.
- `manage_scene(hold)` before risky sessions still protects everything.
