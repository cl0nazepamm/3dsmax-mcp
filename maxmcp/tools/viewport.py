import json
import math
import os
import tempfile
from typing import Any

from ..server import mcp, client
from ..coerce import StrList, FloatList, IntList
from ..helpers.maxscript import safe_string
from ..helpers.native_compat import is_missing_native_route_error
from ..max_client import MaxBridgeError


COMMS_DIR = os.path.join(tempfile.gettempdir(), "3dsmax-mcp")
DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_MAX_WIDTH = 1600
DEFAULT_MIN_WIDTH = 640

_VIEW_TYPES = {
    "perspective": "view_persp_user", "orthographic": "view_iso_user",
    "front": "view_front", "back": "view_back", "left": "view_left",
    "right": "view_right", "top": "view_top", "bottom": "view_bottom",
}


def _source(value: str) -> str:
    if value not in {"auto", "agent", "active"}:
        raise ValueError("source must be auto, agent, or active")
    return value


def _validate_screen_crop(crop) -> None:
    if not isinstance(crop, (list, tuple)) or len(crop) != 4 or any(type(v) is not int for v in crop) or any(v < 0 for v in crop[:2]) or any(v <= 0 for v in crop[2:]):
        raise ValueError("crop must be [x,y,width,height] with nonnegative origin and positive integer size")
    if any(v > 131072 for v in crop):
        raise ValueError("crop exceeds the supported range")


def _agent_context(bridge=None) -> dict[str, Any] | None:
    bridge = bridge or client
    if not bridge.native_available: return None
    try:
        data=json.loads(bridge.send_command('{"action":"status"}',cmd_type="native:agent_viewport")["result"])
    except (MaxBridgeError,RuntimeError) as exc:
        if is_missing_native_route_error(exc): return None
        raise
    if data.get("owner")=="agent":
        if data.get("capture_ready") is False:
            state=data.get("window_state","unavailable")
            action=data.get("next_action","release then open")
            raise RuntimeError(f"AGENT VIEWPORT is {state}; use agent_viewport {action} before capture")
        return data
    if data.get("owned"): raise RuntimeError("AGENT VIEWPORT is closed or unavailable; reopen it before capture")
    return None


@mcp.tool()
def agent_viewport(
    action: str = "status",
    width: int = 1000,
    height: int = 740,
    frame_names: StrList | None = None,
    yaw: float = 0.0,
    pitch: float = 0.0,
    x: float = 0.0,
    y: float = 0.0,
    factor: float = 1.0,
    padding: float = 1.15,
    expected_view: str = "",
    start_minimized: bool = False,
    points: list[FloatList] | None = None,
    mode: str = "",
    renderer_source: str = "activeshade",
    crop: IntList | None = None,
) -> dict[str, Any]:
    """Own a shaded floating AGENT VIEWPORT without moving the user's view.

    action: open | status | release | minimize | restore | frame | orbit | pan |
    zoom | ray | pick | project | render | capture | stop_capture.
    open reserves an unused floating panel once.
    render requires mode=shaded|activeshade|vray_ipr|vray_vfb. ActiveShade uses the assigned
    ActiveShade renderer; renderer_source=production uses the production renderer
    if it supports ActiveShade. vray_ipr uses the current V-Ray CPU/GPU renderer.
    vray_vfb opens VFB IPR locked to the agent view. V-Ray previews temporarily
    enable progressive IPR and a denoiser, restoring settings on stop. Other renderers
    expose different denoisers; ActiveShade does not promise automatic denoising.
    Existing renders in other views or the VFB are refused. shaded stops the owned preview.
    capture chooses the agent viewport or visible VFB automatically. Optional crop
    [x,y,width,height] is relative to the VFB client area in physical pixels.
    stop_capture saves the current image first, then stops the owned preview.
    VFB screenshots include occluding windows; keep it visible and unobstructed.
    V-Ray's menu API briefly requires internal viewport activation, then restores
    the user's view and focus. Status reports render mode and capabilities.
    Render captures show progressive pixels, not proof that rendering is current
    or converged. Switch to shaded and capture again before component targeting.
    start_minimized=true parks it until restore; minimize parks it between uses.
    Minimized panels report capture_ready=false. Restore before navigation or
    capture: hidden/minimized Nitrous cannot supply fresh images. Initial opening
    briefly activates the panel in Max, then restores the user's view and focus.
    After open, set_viewport/capture_viewport/capture_multi_view use it by default.
    release hides only the owned panel. Closed/reconfigured panels fail explicitly.
    frame fits frame_names and descendants, or all visible geometry if omitted.
    orbit uses yaw/pitch degrees about the framing target; pan x/y are view-plane
    scene units; zoom factor<1 moves closer and >1 farther. ray uses normalized
    image x/y (0..1, top-left origin); pass the capture's view_token as expected_view.
    pick returns the nearest geometry or visible thick-spline hit with node handle,
    world point and surface normal. Follow with inspect_mesh near the point for
    base-cage component IDs, or draw_spline get for a spline's editable knots.
    project maps up to 2000 world points into native-viewport pixel coordinates
    (width/height in result), with depth in scene units, in_front and in_frame.
    Use expected_view to match the capture. Resized image coordinates must be
    scaled to native width/height. Projection alone does not test occlusion.
    No scene selection, hiding, camera nodes, or geometry edits. Arbitrary-node
    isolation is not yet supported by this Nitrous panel.
    """
    if action not in {"open","status","release","minimize","restore","frame","orbit","pan","zoom","ray","pick","project","render","capture","stop_capture"}:
        raise ValueError("Unknown agent viewport action")
    if action == "render":
        if mode not in {"shaded", "activeshade", "vray_ipr", "vray_vfb"}:
            raise ValueError("render requires mode=shaded, activeshade, vray_ipr, or vray_vfb")
    elif mode:
        raise ValueError("mode is only valid for action=render")
    if renderer_source not in {"activeshade", "production"}:
        raise ValueError("renderer_source must be activeshade or production")
    if renderer_source != "activeshade" and (action != "render" or mode != "activeshade"):
        raise ValueError("renderer_source=production is only valid for ActiveShade")
    if type(start_minimized) is not bool:
        raise ValueError("start_minimized must be a boolean")
    if start_minimized and action!="open":
        raise ValueError("start_minimized is only valid for open")
    if action=="project":
        if not isinstance(points,(list,tuple)) or not 1<=len(points)<=2000:
            raise ValueError("project requires 1 to 2000 world points")
        for point in points:
            if not isinstance(point,(list,tuple)) or len(point)!=3 or any(
                isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or abs(v)>1e12
                for v in point
            ):
                raise ValueError("points must contain three finite world coordinates each")
    elif points is not None:
        raise ValueError("points is only valid for project")
    if crop is not None:
        if action not in {"capture", "stop_capture"}:
            raise ValueError("crop is only valid for capture or stop_capture")
        _validate_screen_crop(crop)
    for key,value,lo,hi in (("width",width,320,4096),("height",height,240,4096)):
        if type(value) is not int or not lo<=value<=hi: raise ValueError(f"{key} is outside the supported range")
    limits = {"yaw":(yaw,-360,360),"pitch":(pitch,-179,179),"x":(x,-1e9,1e9),
              "y":(y,-1e9,1e9),"factor":(factor,.01,100),"padding":(padding,1,3)}
    for key,(value,lo,hi) in limits.items():
        if isinstance(value,bool) or not math.isfinite(value) or not lo<=value<=hi:
            raise ValueError(f"{key} is outside the supported range")
    if action in {"ray","pick"} and not (0<=x<=1 and 0<=y<=1):
        raise ValueError("ray x/y must be normalized image coordinates in 0..1")
    p={"action":action,"source":"agent","width":width,"height":height,"yaw":yaw,"pitch":pitch,"x":x,"y":y,"factor":factor,"padding":padding}
    if frame_names is not None: p["frame_names"]=list(dict.fromkeys(frame_names))
    if expected_view: p["expected_view"]=expected_view
    if action=="open": p["start_minimized"]=start_minimized
    if points is not None: p["points"]=points
    if action=="render": p.update(mode=mode, renderer_source=renderer_source)
    if action in {"capture", "stop_capture"}:
        state = json.loads(client.send_command('{"action":"status","source":"agent"}',
                                              cmd_type="native:agent_viewport")["result"])
        render = state.get("render", {})
        if render.get("session_state") == "starting":
            return {"status":"starting", "capture":None, "stopped":False,
                    "hint":"Read status after VFB startup, then capture; old VFB pixels are not this preview."}
        if render.get("capture_target") == "vray_vfb":
            capture = capture_screen(enabled=True, target="vray_vfb", crop=crop)
        else:
            if crop is not None:
                raise ValueError("crop requires a VFB preview")
            capture = capture_viewport(source="agent")
        result = {"capture":capture, "render":render, "converged":None}
        if action == "stop_capture":
            stopped = agent_viewport(action="render", mode="shaded")
            result.update(captured_before_stop=True, stopped=stopped.get("render", {}).get("mode")=="shaded")
        return result
    return json.loads(client.send_command(json.dumps(p),cmd_type="native:agent_viewport")["result"])


@mcp.tool()
def set_viewport(
    view: str = "perspective",
    eye: FloatList | None = None,
    target: FloatList | None = None,
    frame_names: StrList | None = None,
    fov: float = 45.0,
    padding: float = 1.15,
    shading: str = "smooth",
    edges: bool = False,
    grid: bool = False,
    source: str = "auto",
) -> dict[str, Any]:
    """Set the agent modeling view when open, otherwise the active viewport.

    view: perspective | orthographic | front | back | left | right | top | bottom.
    eye + target: world coordinates in scene units (Z up), for perspective/orthographic.
    frame_names: fit these exact nodes' world bounds after setting orientation.
    Without eye/target, perspective uses a useful front-right elevated direction.
    fov is perspective horizontal degrees (1-175); orthographic views use zoom.
    padding adds framing margin (1-3).
    shading: smooth | wireframe | flat.
    Follow with capture_viewport to see the result. No rendering is performed.
    source: auto | agent | active. Explicit agent never falls back into a user view.
    """
    view = view.strip().lower()
    shading = shading.strip().lower()
    if view not in _VIEW_TYPES:
        raise ValueError(f"view must be one of {', '.join(_VIEW_TYPES)}")
    levels = {"smooth": "smoothhighlights", "wireframe": "wireframe", "flat": "flat"}
    if shading not in levels:
        raise ValueError("shading must be smooth, wireframe, or flat")
    if not math.isfinite(fov) or not 1 <= fov <= 175:
        raise ValueError("fov must be finite and between 1 and 175 degrees")
    if not math.isfinite(padding) or not 1 <= padding <= 3:
        raise ValueError("padding must be finite and between 1 and 3")
    if (eye is None) != (target is None):
        raise ValueError("eye and target must be supplied together")
    for label, point in (("eye", eye), ("target", target)):
        if point is not None and (len(point) != 3 or any(not math.isfinite(float(v)) for v in point)):
            raise ValueError(f"{label} must contain three finite numbers")
    if eye is not None and view not in {"perspective", "orthographic"}:
        raise ValueError("eye/target require perspective or orthographic")
    if eye is not None and math.dist(eye, target) < 1e-6:
        raise ValueError("eye and target must be different points")
    names = list(dict.fromkeys(frame_names or []))
    _source(source)
    if source!="active" and client.native_available:
        p={"action":"set","source":source,"view":view,"fov":fov,"padding":padding,
           "shading":shading,"edges":edges,"grid":grid}
        if eye is not None: p.update(eye=eye,target=target)
        if frame_names is not None: p["frame_names"]=names
        try:
            data=json.loads(client.send_command(json.dumps(p),cmd_type="native:agent_viewport")["result"])
            if data.get("handled") is not False: return data
        except (MaxBridgeError,RuntimeError) as exc:
            if source=="agent" or not is_missing_native_route_error(exc): raise
    elif source=="agent":
        raise RuntimeError("AGENT VIEWPORT requires the native bridge")
    name_arr = "#(" + ",".join(f'"{safe_string(n)}"' for n in names) + ")"
    orientation = ""
    if view in {"perspective", "orthographic"}:
        eye = list(eye or [1000, -1500, 900])
        target = list(target or [0, 0, 0])
        point = lambda p: "[" + ",".join(format(float(v), ".9g") for v in p) + "]"
        orientation = f'''local eye = {point(eye)}
            local aim = {point(target)}
            local z = normalize (eye - aim)
            local up = if abs z.z > 0.999 then [0,1,0] else [0,0,1]
            local x = normalize (cross up z)
            local y = normalize (cross z x)
            if not (viewport.setTM (inverse (matrix3 x y z eye))) do throw "Could not set view transform"
        '''
    script = f'''(
        local nodes = #()
        local missing = #()
        for nm in {name_arr} do (
            local matches = getNodeByName nm exact:true all:true
            if matches.count != 1 then append missing nm else append nodes matches[1]
        )
        if missing.count > 0 then ("__ERROR__|Framing nodes must resolve uniquely: " + (missing as string))
        else try (
            viewport.setType #{_VIEW_TYPES[view]}
            {'viewport.SetFOV ' + str(fov) if view == 'perspective' else ''}
            {orientation}
            if nodes.count > 0 do (
                local lo = nodes[1].min
                local hi = nodes[1].max
                for n in nodes do (
                    local a = n.min
                    local b = n.max
                    for k = 1 to 3 do (lo[k] = amin lo[k] a[k]; hi[k] = amax hi[k] b[k])
                )
                local center = (lo + hi) * 0.5
                local extent = (hi - lo) * 0.5 * {padding}
                viewport.ZoomToBounds false (center - extent) (center + extent)
                if "{view}" == "perspective" do (
                    -- Fit all eight corners in camera space, preserving the
                    -- requested direction. ZoomToBounds alone can move the eye
                    -- around a stale orbit target in an existing viewport.
                    local size = getViewSize()
                    local tanH = tan ({fov} * 0.5)
                    local tanV = tanH * size.y / size.x
                    local dist = 1.0
                    for a in #(-1,1) do for b in #(-1,1) do for c in #(-1,1) do (
                        local q = [extent.x*a,extent.y*b,extent.z*c]
                        dist = amax dist ((abs (dot q x)) / tanH + dot q z)
                        dist = amax dist ((abs (dot q y)) / tanV + dot q z)
                    )
                    viewport.setTM (inverse (matrix3 x y z (center + z * dist)))
                )
            )
            viewport.SetRenderLevel #{levels[shading]}
            viewport.SetShowEdgeFaces {str(edges).lower()}
            viewport.setGridVisibility viewport.activeViewport {str(grid).lower()}
            completeredraw()
            "OK|" + ((viewport.getType()) as string) + "|" + ((viewport.GetFOV()) as string)
        ) catch ("__ERROR__|" + (getCurrentException() as string))
    )'''
    raw = str(client.send_command(script).get("result", ""))
    if not raw.startswith("OK|"):
        raise RuntimeError(raw.removeprefix("__ERROR__|"))
    _, actual_view, actual_fov = raw.split("|", 2)
    return {"view": view, "actual_view": actual_view, "fov": float(actual_fov) if view == "perspective" else None,
            "framed": names, "shading": shading, "edges": edges, "grid": grid}

# Inline base64 gets JSON-encoded into the envelope's text result, which MCP
# clients cannot render as an image and which overflows their tool-output limit.
_INLINE_DISABLED_HINT = {
    "message": "return_image is deprecated and ignored: inline base64 overflows "
    "MCP clients. The capture is saved to `file` — read that path to view it."
}


def _normalize_path(path: str) -> str:
    return path.replace("/", os.sep)


def _read_image_bytes(path: str) -> bytes:
    with open(_normalize_path(path), "rb") as f:
        return f.read()


def _image_file_result(
    path: str,
    *,
    mime_type: str,
    width: int | None = None,
    height: int | None = None,
    inline: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "image_file",
        "file": _normalize_path(path),
        "mime_type": mime_type,
        "size_bytes": os.path.getsize(_normalize_path(path)),
    }
    if width is not None:
        result["width"] = int(width)
    if height is not None:
        result["height"] = int(height)
    if inline:
        result["hint"] = _INLINE_DISABLED_HINT
    return result


def _capture_viewport_to_file(capture_path: str) -> None:
    maxscript = f"""(
        makeDir "{os.path.dirname(capture_path).replace(os.sep, '/')}" all:true
        completeredraw()
        local vp = gw.getViewportDib()
        vp.filename = "{capture_path}"
        save vp
        "OK"
    )"""
    client.send_command(maxscript)


def _capture_fullscreen_to_file(capture_path: str, max_width: int = 0, max_height: int = 0) -> None:
    maxscript = f"""(
        makeDir "{os.path.dirname(capture_path).replace(os.sep, '/')}" all:true
        bounds = (dotNetClass "System.Windows.Forms.Screen").PrimaryScreen.Bounds
        srcW = bounds.Width
        srcH = bounds.Height
        targetW = srcW
        targetH = srcH
        resizeScale = 1.0

        if {max_width} > 0 and srcW > {max_width} do (
            widthScale = ({max_width} as float) / (srcW as float)
            if widthScale < resizeScale do resizeScale = widthScale
        )

        if {max_height} > 0 and srcH > {max_height} do (
            heightScale = ({max_height} as float) / (srcH as float)
            if heightScale < resizeScale do resizeScale = heightScale
        )

        if resizeScale < 1.0 do (
            targetW = (srcW as float * resizeScale) as integer
            targetH = (srcH as float * resizeScale) as integer
            if targetW < 1 do targetW = 1
            if targetH < 1 do targetH = 1
        )

        srcSize = dotNetObject "System.Drawing.Size" srcW srcH
        srcBmp = dotNetObject "System.Drawing.Bitmap" srcW srcH
        srcGfx = (dotNetClass "System.Drawing.Graphics").FromImage srcBmp
        srcGfx.CopyFromScreen 0 0 0 0 srcSize
        srcGfx.Dispose()

        outBmp = srcBmp
        if targetW != srcW or targetH != srcH do (
            dstBmp = dotNetObject "System.Drawing.Bitmap" targetW targetH
            dstGfx = (dotNetClass "System.Drawing.Graphics").FromImage dstBmp
            dstGfx.InterpolationMode = (dotNetClass "System.Drawing.Drawing2D.InterpolationMode").HighQualityBicubic
            dstGfx.PixelOffsetMode = (dotNetClass "System.Drawing.Drawing2D.PixelOffsetMode").HighQuality
            dstGfx.SmoothingMode = (dotNetClass "System.Drawing.Drawing2D.SmoothingMode").HighQuality
            dstGfx.DrawImage srcBmp 0 0 targetW targetH
            dstGfx.Dispose()
            srcBmp.Dispose()
            outBmp = dstBmp
        )

        outBmp.Save "{capture_path}"
        outBmp.Dispose()
        "OK"
    )"""
    client.send_command(maxscript)


@mcp.tool()
def capture_viewport(
    max_width: int = DEFAULT_MAX_WIDTH,
    max_height: int = 0,
    return_image: bool = False,
    source: str = "auto",
) -> Any:
    """Capture AGENT VIEWPORT when open, otherwise the active view, to a saved file.

    Read the returned `file` path to view the capture. return_image is
    deprecated and ignored — the image is never inlined.
    source: auto | agent | active. Explicit agent never falls back into a user view.

    Use when: quick visual proof after a scene change.
    Not when: the user asked for a full render (render_scene) or a multi-angle grid
    (capture_multi_view).
    """
    max_width = max(0, int(max_width))
    max_height = max(0, int(max_height))
    _source(source)

    if client.native_available:
        p={"max_width":max_width,"max_height":max_height}
        if source!="auto": p["source"]=source
        if source=="agent": p["action"]="capture"
        response = client.send_command(json.dumps(p), cmd_type="native:agent_viewport" if source=="agent" else "native:capture_viewport")
        data = json.loads(response.get("result", "{}"))
        file_path = data.get("file", "")
        if file_path:
            result = _image_file_result(
                file_path,
                mime_type="image/png",
                width=data.get("width"),
                height=data.get("height"),
                inline=return_image,
            )
            for key in ("source","agent_viewport","source_width","source_height"):
                if key in data: result[key]=data[key]
            return result

    if source=="agent": raise RuntimeError("AGENT VIEWPORT returned no capture; active view was not used")

    capture_path = os.path.join(COMMS_DIR, "viewport_capture.png").replace("\\", "/")
    _capture_viewport_to_file(capture_path)
    return _image_file_result(
        capture_path,
        mime_type="image/png",
        inline=return_image,
    )



@mcp.tool()
def capture_screen(
    enabled: bool = False,
    max_width: int = DEFAULT_MAX_WIDTH,
    max_height: int = 0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    min_width: int = DEFAULT_MIN_WIDTH,
    return_image: bool = False,
    target: str = "screen",
    crop: IntList | None = None,
) -> Any:
    """Capture visible desktop pixels, optionally cropped to the V-Ray frame buffer.

    Read the returned `file` path to view the capture. return_image is
    deprecated and ignored — the image is never inlined.
    target=screen captures the primary monitor; vray_vfb finds the visible V-Ray
    Frame Buffer belonging to this Max process, on any monitor, and captures its
    client area. crop=[x,y,width,height] is relative to that area in physical
    pixels BEFORE resizing, for example to exclude VFB toolbars. The window must
    be fully on screen. Overlapping windows appear in the image; this does not
    activate, uncover, start, stop, or wait for a render. Cropped capture requires
    the updated native bridge; it never falls back to a full desktop image.
    """
    if not enabled:
        raise ValueError("capture_screen is disabled by default; set enabled=True to allow fullscreen capture")
    if target not in {"screen", "vray_vfb"}:
        raise ValueError("target must be screen or vray_vfb")
    if crop is not None:
        _validate_screen_crop(crop)

    max_width = max(0, int(max_width))
    max_height = max(0, int(max_height))

    if client.native_available:
        params = {"max_width": max_width, "max_height": max_height}
        if target != "screen": params["target"] = target
        if crop is not None: params["crop"] = list(crop)
        payload = json.dumps(params)
        response = client.send_command(payload, cmd_type="native:capture_screen")
        data = json.loads(response.get("result", "{}"))
        file_path = data.get("file", "")
        if (target != "screen" or crop is not None) and data.get("capture_contract") != "desktop_crop_v1":
            raise RuntimeError("Cropped screen capture requires the updated native bridge; uncropped image was not returned")
        if file_path:
            result = _image_file_result(
                file_path,
                mime_type="image/jpeg",
                width=data.get("width"),
                height=data.get("height"),
                inline=return_image,
            )
            for key in ("capture_contract", "target", "screen_rect", "target_rect", "window", "visible_pixels_only", "occlusion_checked"):
                if key in data: result[key] = data[key]
            return result

    if target != "screen" or crop is not None:
        raise RuntimeError("Cropped screen capture requires the updated native bridge")

    max_bytes = max(0, int(max_bytes))
    min_width = max(1, int(min_width))

    capture_path = os.path.join(COMMS_DIR, "screen_capture.jpg").replace("\\", "/")
    current_width = max_width
    _capture_fullscreen_to_file(capture_path, max_width=current_width, max_height=max_height)
    img_data = _read_image_bytes(capture_path)

    if max_bytes > 0:
        attempts = 0
        while len(img_data) > max_bytes and attempts < 6:
            if current_width <= 0:
                current_width = DEFAULT_MAX_WIDTH
            next_width = max(min_width, int(current_width * 0.8))
            if next_width == current_width:
                break
            current_width = next_width
            _capture_fullscreen_to_file(capture_path, max_width=current_width, max_height=max_height)
            img_data = _read_image_bytes(capture_path)
            attempts += 1

    result: dict[str, Any] = {
        "type": "image_file",
        "file": _normalize_path(capture_path),
        "mime_type": "image/jpeg",
        "size_bytes": len(img_data),
    }
    if return_image:
        result["hint"] = _INLINE_DISABLED_HINT
    return result


@mcp.tool()
def capture_multi_view(
    views: StrList | None = None,
    max_width: int = DEFAULT_MAX_WIDTH,
    max_height: int = 0,
    return_image: bool = False,
    frame_root: str = "",
    source: str = "auto",
) -> Any:
    """Capture multiple viewport angles to a stitched file and return compact metadata.

    Uses AGENT VIEWPORT when open; source=active explicitly uses the user view.
    frame_root frames a named hierarchy. In the agent panel it does not hide other
    nodes; in legacy active mode it also temporarily isolates that hierarchy.
    Read the returned `file` path to view the grid. return_image is deprecated
    and ignored — the image is never inlined.

    Use when: verifying scene changes from several angles (preferred after meaningful edits).
    Not when: a single active viewport is enough (capture_viewport) or the user asked to render.
    """
    payload = {}
    _source(source)
    if source!="auto": payload["source"]=source
    if frame_root:
        payload["frame_root"] = frame_root
    if views:
        payload["views"] = views
    payload["max_width"] = max(0, int(max_width))
    payload["max_height"] = max(0, int(max_height))
    if source=="agent": payload["action"]="capture_multi"
    response = client.send_command(json.dumps(payload), cmd_type="native:agent_viewport" if source=="agent" else "native:capture_multi_view")
    raw = response.get("result", "")
    data = json.loads(raw)
    file_path = data.get("file", "")
    if not file_path:
        raise RuntimeError("No image file returned from multi-view capture")
    result = _image_file_result(
        file_path,
        mime_type="image/png",
        width=data.get("width"),
        height=data.get("height"),
        inline=return_image,
    )
    result["views"] = data.get("views")
    result["grid"] = data.get("grid")
    result["framed_root"] = data.get("framed_root")
    for key in ("source","agent_views","restored_agent_viewport","isolation_applied"):
        if key in data: result[key]=data[key]
    return result
