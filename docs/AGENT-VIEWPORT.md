# Agent viewport

The first implementation owns one of Max's unused floating Nitrous panels. Both
the floating window caption and a viewport overlay say **AGENT VIEWPORT**. A
single capture also carries that label in the saved image.

## Contract

- Opening reserves a hidden floating panel and configures it as one viewport.
  Visible user panels are never claimed. Max briefly activates a panel when
  showing it; the initialization path restores the previous active panel,
  viewport, top-level window and keyboard focus. New panels explicitly start in
  Shaded mode with textures enabled and edged faces off, irrespective of the
  unused panel's saved display settings. Status includes `shading` and `edges`.
- `open(start_minimized=true)` reserves a minimized panel. `minimize` parks an
  owned panel between inspections; `restore` shows it without requesting focus.
  Minimized status reports `capture_ready=false` and `next_action="restore"`.
  Captures and navigation refuse while minimized; neither operation secretly
  restores a window or redirects to the user's viewport. Restoring invalidates
  the previous view token, so targeting requires a fresh capture.
  If the agent panel itself is active, `minimize` refuses before changing state;
  activate a user viewport first. Camera cleanup after an interrupted multi-view
  capture can restore an owned minimized panel without showing or capturing it.
  Releasing a minimized panel restores its normal window state without activation
  before hiding it, so the same floating slot can be reserved again.
- Navigation addresses the owned `IViewPanel`/`ViewExp` directly. There are no
  camera nodes, selection changes, global zoom-extents commands, or scene hiding.
- A Win32 ownership property, panel HWND, floating ID and viewport ID are
  cross-checked for every operation. A closed/reconfigured/lost panel fails
  explicitly. Release affects only a panel whose ownership still matches.
  Geometry evaluation and redraw can process UI callbacks; ownership is resolved
  again afterward before camera writes, viewport filtering, or HWND access. This
  avoids the SDK's default-viewport routing for invalid `ViewExp` instances.
- Captures use the owned viewport automatically once opened. Explicit `agent`
  requests never fall back into the active viewport, including on older GUPs.
- The viewport must remain visible and on a monitor. Hidden panels returned
  black images and offscreen panels returned stale images in the live Max 2027
  feasibility tests. A redraw callback provides a fresh-draw check.
- A view token includes the camera, projection, dimensions, scene mutation
  sequence, time and ownership generation. Picking can reject stale images.
  The returned hit belongs to evaluated geometry; it is not silently presented
  as an Editable Poly base-face ID. Use `inspect_mesh` and its mesh token before
  editing components. Image labels are added to the agent capture itself.
- `project(points=..., expected_view=...)` accepts up to 2000 world points and
  returns native viewport pixel coordinates, camera-axis depth in scene units,
  and `in_front`/`in_frame` flags. Behind-eye perspective points return null pixels;
  orthographic projection has no behind-eye rejection. This is projection, not
  an occlusion test. Pixel coordinates refer to the returned native `width` and
  `height`; callers must scale any resized capture coordinates accordingly.
- Scene open/reset/new invalidates the ownership's viewport identity. Explicit
  release/open reacquires it. Callback registrations are removed at release and
  bridge shutdown. No native SDK pointers to nodes or viewports persist.

## Interactive render modes

`agent_viewport(action="render", mode="shaded|activeshade|vray_ipr|vray_vfb")`
controls the owned preview. ActiveShade uses the assigned ActiveShade renderer;
`renderer_source="production"` selects the existing production renderer if it
implements the interactive interface. Renderer settings are not reassigned.
V-Ray uses its installed `vrayViewportIPRControl` menu function, checking the
current state before toggling. CPU/GPU availability follows the production
renderer. Queries and toggles temporarily activate only the owned viewport
internally and restore the previous panel, view, active window and focus.

`vray_vfb` starts VFB IPR with `Interface16::SetRendViewID(RS_Production, viewID)`
and the render-view lock. Its lifecycle is `starting` before the asynchronously
posted V-Ray launch becomes observable, then `running`. Capture refuses the
starting state rather than returning the previous VFB image. The global VFB stop
requires the renderer handle and render-view lock still to match. Loss of that
ownership fails explicitly. A launch never observed running retains its lease;
it cannot safely be assumed stopped solely because a timeout elapsed.

Both V-Ray paths set `ipr_progressiveMode=true`, enable denoiser result calculation
and the VFB denoiser layer, and set progressive post-effects update rate to 100.
An existing denoiser retains its engine/preset; otherwise a temporary default V-Ray
denoiser is added. On stop, saved scalar settings are restored only if still equal
to the applied values, and the temporary element is removed. ActiveShade has no
universal denoiser adapter. Unsupported V-Ray property surfaces fail explicitly.
Scene load/reset drops old handles rather than restoring them into a new scene.

`agent_viewport(action="capture")` routes to viewport pixels or the visible VFB.
`stop_capture` saves first, then stops, keeping the last image before renderer
cleanup can change it. Optional `crop` applies only to VFB screenshots.

The controls reject other active IPR/ActiveShade sessions rather than globally
stopping them. Switching back to shaded and release stop only this viewport's
render. An open user hold blocks render-mode changes. Stop interactive rendering
before minimizing the agent panel. Failed starts attempt to leave its preview
stopped; they do not claim an atomic renderer rollback. Scene/layout replacement
still requires release/open, and no renderer/viewport pointers are cached.

Status includes `render.mode`, V-Ray API availability, CPU/GPU menu availability,
and `targeting_supported`. Render state participates in the view token, but that
token is not a render generation or convergence certificate. Rendered captures
report unknown image freshness/convergence; component picking/projection requires
shaded mode and a fresh capture because render effects can change pixel-to-mesh
correspondence. SDK label projection does not certify visibility in rendered pixels.

`capture_screen(enabled=true, target="vray_vfb", crop=[x,y,width,height])` captures
visible screen pixels in the VFB client rectangle. The crop is optional, measured
in physical pixels relative to that rectangle before resizing. Window discovery
is restricted to the bridge process; ambiguous, minimized, missing, moving, or
offscreen targets fail explicitly. It never activates or uncovers the VFB and
does not exclude occluding windows. This is not a renderer bitmap export. Cropped
requests require the `desktop_crop_v1` result contract, so older native plugins
cannot silently return a full-screen image in response to a crop request.

Cancellation and desktop capture use separate short-lived Python pipe connections
while a normal request is in flight. They pin the existing request's Max instance
and never fall back to TCP or another claimed Max. This fixes delivery blocking at
the Python pipe lock; it does not make arbitrary SDK work safe off the main thread
or guarantee cancellation of a renderer that does not poll the abort flag. The
existing native cancel route calls `Interface::AbortRender()` from its pipe worker.
The SDK documents flag-setting, but gives no explicit cross-thread guarantee in
that declaration; actual cancellation still requires renderer-specific live validation.

`render_automations(action="cancel_capture", job_id=..., crop=...)` uses a dedicated
native route to capture visible VFB pixels then request cancellation of the
matching armed, started production job. Old bridges reject the route before any
global abort. Cancellation still runs if window capture fails. The result leaves
`stopped`/`converged` unknown; POST_RENDER reports termination with an unknown
outcome and the recorded cancellation request. Arm only the production render
the agent is about to start. This is a recovery operation, not an automatic
production-preview launcher: progressive sampling and renderer-specific denoising
must be configured before rendering starts. It cannot retrofit settings into a
blocked renderer. No hard interruption or guaranteed denoised partial bitmap is
claimed. `capture_target="screen"` is an explicit desktop fallback for other VFBs.

Live Max 2027/V-Ray 7 verification: viewport IPR start/idempotence/capture/stop;
VFB asynchronous start, native view lock, progressive/denoiser readback, cropped
visible capture, and settings restoration passed through the SDK probe. The user
panel/camera remained unchanged. The current Scanline ActiveShade assignment was
correctly refused; a successful compatible ActiveShade session and actual blocked
production cancellation remain unverified. Evidence: `local/ipr-development/`.

## Modeling boundary

Arbitrary-node isolation is not available through Max's public per-viewport
filter, which exposes classes/categories. `frame_root` therefore frames without
hiding in the agent panel, and reports `isolation_applied=false`. The old
scene-wide `isolate_and_capture_selected` operation is rejected while the agent
owns a panel. Do not implement isolation by toggling scene visibility around a
draw callback: that would reintroduce shared state changes and callback hazards.

The other requested modeling priorities build on this interface: geometric
target resolution with explicit ambiguity and topology tokens; section-based
loft/sweep construction; versioned construction parameters stored with nodes;
and mesh QA for borders, orientation, intersections and thickness. They are
separate work from this viewport implementation.

Max's `IViewPanelManager::SetFloatingViewPanelVisibility` explicitly makes a panel
visible **and active**. Therefore initial opening cannot promise zero transient
activation. Minimize/restore uses Win32 `SW_SHOWMINNOACTIVE`/`SW_SHOWNOACTIVATE`
and preserves the prior Max activation state; it does not use
`SetForegroundWindow`. This does not make minimized Nitrous rendering available.
See [Win32 show states](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow).

## Verification

`MCP_BUILD_AGENT_VIEWPORT_PROBE=ON` builds an optional SDK test DLL from the same
viewport implementation. Its exported test entry verifies the Max main thread
before entering the SDK. It can be loaded through Max's Python runtime to test
without replacing or unloading the running GUP. The regular bridge uses the
main-thread executor and the `native:agent_viewport` route.

Live acceptance includes two distinct captures while the main view, layout,
selection and hidden-node set remain unchanged; named elevations; framing;
world-point projection/ray agreement; stale-token rejection; ownership loss;
release/reopen; and removal of callback registrations. Integrated capture
routing must additionally be tested against the rebuilt GUP after user reload.

The September 5 acceptance run passed all nine live checks, including exact
orthographic pan/zoom and display-setting restoration. Artifacts and the report
are in `local/agent-viewport-smoke/`. The targeted Python suite passed 73 tests
and 49 subtests; Release builds passed for Max 2023 through 2027. Only Max 2027
was exercised live. Rebuilt binaries and SHA-256 hashes are staged separately in
`local/agent-viewport-release/`; the installed GUP was not replaced.

The subsequent chair session verified the installed external capture, multi-view
and polygon-picking routes. It also exposed missed spline armrest hits. The
picker now intersects a temporary `ShapeObject::GenerateMesh` using the displayed
viewport settings, because the base `ShapeObject::IntersectRay` returns false.
The mesh is scoped to the request; the source spline stays editable. Hits include
world normals, transformed with the inverse transpose for nonuniform scales.
Line-only splines do not have a surface and remain outside this surface picker.
See the [SDK shape reference](https://help.autodesk.com/cloudhelp/2027/ENU/MAXDEV-CPP-API-REF/class_shape_object.html).

The corrected implementation hit `Astra_Chair_Arm_R` at the exact camera/pixel
where the installed binary returned null, with the user viewport state preserved.
Evidence: `local/chair-development/curve-pick-after.json` and `curve-pick-hit.png`.
This additional native fix needs a subsequent GUP reload for the regular route.

The minimized lifecycle, shaded initialization, activation restoration and
projection metadata are a subsequent source change. Their Python routing and
argument validation have focused coverage in `tests/test_agent_viewport.py`.
Acceptance on a loaded GUP must verify status while minimized, capture refusal,
restore/capture freshness, user panel and keyboard-focus preservation, and
projection agreement with a known visible point. Build success alone does not
establish those UI and Nitrous behaviors.

The subsequent live SDK harness passed minimized start, shaded restore,
minimized capture refusal, projection agreement, stale-token rejection, and
user-view preservation. Restoring the camera while minimized had zero matrix
and orthographic-width error. Releasing a minimized panel and reopening reused
the same floating slot. Evidence: `local/surface-development/viewport-lifecycle.json`.
After the user's next reload, the regular external MCP route passed minimized
start, capture refusal, shaded restore, minimized release/reopen with slot reuse,
and unchanged user viewport/selection. Native writes rejected an active hold with
retryable `USER_BUSY`; read routes remained available and writes resumed afterward.
Evidence: `local/dining-chair/installed-verification.json`. Independent agent views
were then used throughout the dining-chair modeling session. One stale redraw
capture was refused and succeeded after restore/retry; minimized or occluded
Nitrous capture remains a limitation rather than a silent fallback.
