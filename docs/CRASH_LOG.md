# Crash / native fault log

Append-only. Do not fold these into `SKILL.md` pitfalls unless fixed and generalized.

## 2026-07-26 — `assign_material` native AV (Max 2027) — **FIXED**

- **Symptom:** `NativeError` / `SE Exception: Access Violation (0xC0000005)` via named-pipe bridge (`transport: namedpipe`). Bridge still responded to `get_bridge_status` afterward.
- **Tool:** `assign_material`, called with `material_class="Physical"`.
- **Context:** builder mode material pass on `m4_carbine` (session root `BLD_m4_carbine`). Two batches faulted; an identical earlier batch returned first — the fault is not deterministic per call.
- **Failing calls (batch), as logged:**
  - `material_class="Physical"`, `material_name="metal"`, `names=[buffer_tube, magazine, trigger_guard, delta_ring, front_sight, barrel, flash_hider]`, `params="roughness:0.32 metalness:0.85 base_color:(color 20 20 22)"`
  - same pattern for `metal_matte` on `[lower_receiver, upper_receiver, carry_handle]` with `roughness:0.42 metalness:0.75 base_color:(color 24 24 26)`
- **Preceding success:** `material_class="Physical"`, `material_name="polymer"`, three objects (`stock`, `pistol_grip`, `handguard`), same `base_color:(color …)` style params — completed without AV.
- **Root cause:** `NativeHandlers::AssignMaterial` resolved the class name in two steps —
  `FindClassDescByName(matClass, MATERIAL_CLASS_ID)` and then, on miss, an **unfiltered**
  `FindClassDescByName(matClass)` across every superclass — and blind-cast the result to `Mtl*`:
  `mtl = (Mtl*)ip->CreateInstance(cd->SuperClassID(), cd->ClassID())`.
  `Physical` is not a material: the Physical Material registers as `Physical Material` /
  `PhysicalMaterial`, while **`Physical` is the Physical Camera** (`CAMERA_CLASS_ID`). The unfiltered
  lookup therefore returned the camera ClassDesc, the handler instantiated a camera and treated it as a
  material, and every subsequent virtual call (`Mtl::SetName`, `INode::SetMtl`, viewport redraw)
  dispatched through the wrong vtable → 0xC0000005. Whether the fault surfaced on the creating call or a
  later one depended on when the viewport next evaluated the bogus material, which is why one batch
  appeared to succeed first.
- **Not the cause (ruled out):** `base_color:(color R G B)` in the params string — `ParseMtlParams` keeps
  the parenthesised value intact and `SetPB2ParamFromString` parses `(color r g b)` via `sscanf`;
  a bad value returns `false`, it does not fault. Multi-object batching and the modifier stack are
  likewise unrelated — the same token faults with a single object.
- **Fix:** `native/src/handlers/material_handlers.cpp` — the SDK path now only accepts a ClassDesc found
  under `MATERIAL_CLASS_ID`. If the name resolves only outside that superclass, the handler raises a
  structured `BAD_PARAM` ("Not a material class: …") carrying `hint.didYouMean` from
  `SuggestMaterialClasses()` (material classes whose name starts with the token, e.g.
  `Physical` → `Physical Material` / `PhysicalMaterial`) instead of instantiating and casting it.
  Requires a native rebuild + redeploy to take effect.
- **Caller-side note:** use `material_class="PhysicalMaterial"`. The earlier workaround (single object,
  no `base_color`, then MAXScript for the rest) is no longer needed.
- **Same pattern still unguarded elsewhere:** `native/src/handlers/modifier_handlers.cpp:78` (`(Modifier*)`)
  and `native/src/handlers/object_handlers.cpp:469` (`(Object*)`) both keep an unfiltered
  `FindClassDescByName` fallback ahead of a blind cast, so a cross-superclass name collision can fault
  the same way there. Not touched with this fix.
