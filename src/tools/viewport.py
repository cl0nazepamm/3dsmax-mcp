import os
import tempfile

from mcp.server.fastmcp import Image

from ..server import mcp, client


COMMS_DIR = os.path.join(tempfile.gettempdir(), "3dsmax-mcp")
DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_MAX_WIDTH = 1600
DEFAULT_MIN_WIDTH = 640


def _read_image_bytes(path: str) -> bytes:
    with open(path.replace("/", os.sep), "rb") as f:
        return f.read()


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
def capture_viewport() -> Image:
    """Capture the current 3ds Max viewport and return it as an image.

    Returns the viewport screenshot as a PNG image that can be displayed
    directly in the chat.
    """
    capture_path = os.path.join(COMMS_DIR, "viewport_capture.png").replace("\\", "/")
    _capture_viewport_to_file(capture_path)
    img_data = _read_image_bytes(capture_path)
    return Image(data=img_data, format="png")


@mcp.tool()
def capture_model() -> Image:
    """Backward-compatible alias for fast viewport-only capture."""
    return capture_viewport()


@mcp.tool()
def capture_screen(
    enabled: bool = False,
    max_width: int = DEFAULT_MAX_WIDTH,
    max_height: int = 0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    min_width: int = DEFAULT_MIN_WIDTH,
) -> Image:
    """Capture fullscreen only when explicitly enabled.

    Args:
        enabled: Must be True to allow fullscreen capture.
        max_width: Maximum output width in pixels. Use 0 to keep full width.
        max_height: Maximum output height in pixels. Use 0 to keep full height.
        max_bytes: Soft byte cap for output size (default 1MB). Use 0 to disable.
        min_width: Smallest auto-resize width used when shrinking to hit max_bytes.

    Returns the screenshot as a JPEG image. Aspect ratio is always preserved.
    """
    if not enabled:
        raise ValueError("capture_screen is disabled by default; set enabled=True to allow fullscreen capture")

    max_width = max(0, int(max_width))
    max_height = max(0, int(max_height))
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

    return Image(data=img_data, format="jpeg")
