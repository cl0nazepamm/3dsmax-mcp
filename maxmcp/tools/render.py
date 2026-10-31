from typing import Any

from ..server import mcp, client
from ..helpers import jobs

@mcp.tool()
def render_scene(
    width: int = 1920,
    height: int = 1080,
    output_path: str = "",
    wait_seconds: int = 50,
    job: str = "",
) -> Any:
    """Render the current viewport in 3ds Max.

    Waits up to wait_seconds (max 50). A render still running then returns
    {"status": "running", "job": ...}; call render_scene(job=...) to wait again.
    Never repeat the original call or start another render while its job runs:
    it would render twice. An armed render_automations done-signal also
    completes when this render ends.
    """
    if job:
        outcome = jobs.resume(client, "render_scene", job, wait_seconds=wait_seconds)
    elif client.native_available:
        payload = {"width": width, "height": height, "output_path": output_path}
        outcome = jobs.start(client, "render_scene", "native:render_scene", payload,
                             wait_seconds=wait_seconds, timeout=300)
    else:
        outcome = None
    if outcome is not None:
        if outcome.running:
            return outcome.running
        return outcome.response.get("result", "")

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
