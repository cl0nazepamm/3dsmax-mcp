from ..server import mcp, client


@mcp.tool()
def render_scene(
    width: int = 1920,
    height: int = 1080,
    output_path: str = "",
) -> str:
    """Render the current viewport in 3ds Max.

    Args:
        width: Render width in pixels (default 1920)
        height: Render height in pixels (default 1080)
        output_path: File path to save the render (e.g. "C:/renders/test.png").
                     If empty, renders to the frame buffer only.

    Returns confirmation with the output path or render status.
    """
    safe_path = output_path.replace("\\", "/")
    output_clause = f'outputFile:"{safe_path}"' if safe_path else ""
    save_msg = f' - saved to: {safe_path}' if safe_path else ""

    maxscript = f"""(
        local bmp = render outputWidth:{width} outputHeight:{height} {output_clause} vfb:true
        if bmp != undefined then
            "Render completed ({width}x{height}){save_msg}"
        else
            "Render returned undefined - check render settings"
    )"""
    response = client.send_command(maxscript, timeout=300.0)
    return response.get("result", "")
