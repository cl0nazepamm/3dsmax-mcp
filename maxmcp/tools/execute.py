import json

from ..helpers.error_hints import suggest_tools_for_maxscript
from ..server import mcp, client


_MAXSCRIPT_ERROR_SENTINEL = "__MCP_MS_ERR__:"


@mcp.tool()
def execute_maxscript(code: str = "", command: str = "") -> str:
    """Execute arbitrary MAXScript in 3ds Max and return the result.

    Use when: no dedicated MCP tool covers the operation (custom one-offs, rare APIs).
    Not when: objects, materials, selection, transforms, modifiers, layers, or scene queries —
    prefer the matching dedicated tool instead of raw MAXScript.
    """
    script = code or command
    if not script:
        return "Error: provide MAXScript code in the 'code' parameter"
    response = client.send_command(script, cmd_type="maxscript")
    result = response.get("result", "")

    if isinstance(result, str) and result.startswith(_MAXSCRIPT_ERROR_SENTINEL):
        message = result[len(_MAXSCRIPT_ERROR_SENTINEL):].strip()
        payload: dict[str, object] = {
            "status": "error",
            "error_type": "MAXScriptError",
            "error": message,
        }
        suggested = suggest_tools_for_maxscript(script)
        if suggested:
            payload["hint"] = {
                "message": (
                    "execute_maxscript is a fallback. Before retrying the same "
                    "script, consider using a dedicated MCP tool that handles "
                    "this intent directly."
                ),
                "suggested_tools": suggested,
            }
        return json.dumps(payload)

    return result
