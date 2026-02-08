import os
import tempfile

from mcp.server.fastmcp import Image

from ..server import mcp, client


COMMS_DIR = os.path.join(tempfile.gettempdir(), "3dsmax-mcp")


@mcp.tool()
def capture_viewport() -> Image:
    """Capture the current 3ds Max viewport and return it as an image.

    Returns the viewport screenshot as a PNG image that can be displayed
    directly in the chat.
    """
    capture_path = os.path.join(COMMS_DIR, "viewport_capture.png").replace("\\", "/")

    maxscript = f"""(
        makeDir "{os.path.dirname(capture_path).replace(os.sep, '/')}" all:true
        completeredraw()
        local vp = gw.getViewportDib()
        vp.filename = "{capture_path}"
        save vp
        "OK"
    )"""
    client.send_command(maxscript)

    img_path = capture_path.replace("/", os.sep)
    with open(img_path, "rb") as f:
        img_data = f.read()

    return Image(data=img_data, format="png")


@mcp.tool()
def capture_screen(
    x: int = 0,
    y: int = 0,
    width: int = 0,
    height: int = 0,
) -> Image:
    """Capture a region of the screen (for UI panels, render results, dialogs).

    Use this when you need to see something outside the 3D viewport, such as
    the material editor, render output, script UI panels, or dialogs.

    Args:
        x: Left edge of capture region in pixels (default 0)
        y: Top edge of capture region in pixels (default 0)
        width: Width of capture region in pixels (0 = auto-detect full screen)
        height: Height of capture region in pixels (0 = auto-detect full screen)
    """
    capture_path = os.path.join(COMMS_DIR, "screen_capture.png").replace("\\", "/")

    maxscript = f"""(
        makeDir "{os.path.dirname(capture_path).replace(os.sep, '/')}" all:true
        captureW = {width}
        captureH = {height}
        if captureW == 0 or captureH == 0 do (
            bounds = (dotNetClass "System.Windows.Forms.Screen").PrimaryScreen.Bounds
            if captureW == 0 do captureW = bounds.Width
            if captureH == 0 do captureH = bounds.Height
        )
        sz = dotNetObject "System.Drawing.Size" captureW captureH
        screenBmp = dotNetObject "System.Drawing.Bitmap" captureW captureH
        gfx = (dotNetClass "System.Drawing.Graphics").FromImage screenBmp
        gfx.CopyFromScreen {x} {y} 0 0 sz
        screenBmp.Save "{capture_path}"
        gfx.Dispose()
        screenBmp.Dispose()
        "OK"
    )"""
    client.send_command(maxscript)

    img_path = capture_path.replace("/", os.sep)
    with open(img_path, "rb") as f:
        img_data = f.read()

    return Image(data=img_data, format="png")
