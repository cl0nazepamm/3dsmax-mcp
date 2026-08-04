import json
import os
import tempfile
from typing import Any

from ..server import mcp, client
from ..coerce import StrList


COMMS_DIR = os.path.join(tempfile.gettempdir(), "3dsmax-mcp")
DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_MAX_WIDTH = 1600
DEFAULT_MIN_WIDTH = 640

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
) -> Any:
    """Capture the current 3ds Max viewport and return the saved file path.

    Read the returned `file` path to view the capture. return_image is
    deprecated and ignored — the image is never inlined.

    Use when: quick visual proof after a scene change.
    Not when: the user asked for a full render (render_scene) or a multi-angle grid
    (capture_multi_view).
    """
    max_width = max(0, int(max_width))
    max_height = max(0, int(max_height))

    if client.native_available:
        payload = json.dumps({"max_width": max_width, "max_height": max_height})
        response = client.send_command(payload, cmd_type="native:capture_viewport")
        data = json.loads(response.get("result", "{}"))
        file_path = data.get("file", "")
        if file_path:
            return _image_file_result(
                file_path,
                mime_type="image/png",
                width=data.get("width"),
                height=data.get("height"),
                inline=return_image,
            )

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
) -> Any:
    """Capture fullscreen to a file only when explicitly enabled.

    Read the returned `file` path to view the capture. return_image is
    deprecated and ignored — the image is never inlined.
    """
    if not enabled:
        raise ValueError("capture_screen is disabled by default; set enabled=True to allow fullscreen capture")

    max_width = max(0, int(max_width))
    max_height = max(0, int(max_height))

    if client.native_available:
        payload = json.dumps({"max_width": max_width, "max_height": max_height})
        response = client.send_command(payload, cmd_type="native:capture_screen")
        data = json.loads(response.get("result", "{}"))
        file_path = data.get("file", "")
        if file_path:
            return _image_file_result(
                file_path,
                mime_type="image/jpeg",
                width=data.get("width"),
                height=data.get("height"),
                inline=return_image,
            )

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
) -> Any:
    """Capture multiple viewport angles to a stitched file and return compact metadata.

    Read the returned `file` path to view the grid. return_image is deprecated
    and ignored — the image is never inlined.

    Use when: verifying scene changes from several angles (preferred after meaningful edits).
    Not when: a single active viewport is enough (capture_viewport) or the user asked to render.
    """
    payload = {}
    if views:
        payload["views"] = views
    payload["max_width"] = max(0, int(max_width))
    payload["max_height"] = max(0, int(max_height))
    response = client.send_command(json.dumps(payload), cmd_type="native:capture_multi_view")
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
    return result
