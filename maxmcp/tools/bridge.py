"""Bridge/transport status tools for the live 3ds Max connection."""

from __future__ import annotations

import json

from ..server import mcp, client


def _legacy_bridge_status() -> str:
    maxscript = r"""(
        local esc = MCP_Server.escapeJsonString
        local maxYear = ((1998 + ((maxVersion())[1] / 1000)) as integer)
        local rendererName = try ((classOf renderers.current) as string) catch "unknown"
        "{\"pong\":true" + \
        ",\"server\":\"3dsmax-mcp\"" + \
        ",\"protocolVersion\":1" + \
        ",\"maxVersion\":" + (maxYear as string) + \
        ",\"renderer\":\"" + (esc rendererName) + "\"" + \
        ",\"objectCount\":" + (objects.count as string) + \
        ",\"selectionCount\":" + (selection.count as string) + \
        ",\"safeMode\":" + (if MCP_Server.safeMode then "true" else "false") + \
        ",\"port\":" + (MCP_Server.port as string) + \
        "}"
    )"""
    response = client.send_command(maxscript, timeout=5.0)
    payload = json.loads(response.get("result", "{}"))
    payload["requestId"] = response.get("requestId")
    payload["meta"] = response.get("meta", {})
    payload["connected"] = True
    payload["legacyTransport"] = True
    return json.dumps(payload)


@mcp.tool()
def get_bridge_status() -> str:
    """Ping the MCP bridge for protocol/transport metadata.

    Use when: a tool failed with a connection/transport/claim error and you need to diagnose.
    Not when: starting a session or before every task — prefer query_scene for scene work.
    """
    try:
        response = client.send_command("", cmd_type="ping", timeout=5.0)
    except RuntimeError as exc:
        error = str(exc)
        if "Empty command" in error or "Unknown command type" in error:
            return _legacy_bridge_status()
        raise

    payload = json.loads(response.get("result", "{}"))
    payload["requestId"] = response.get("requestId")
    payload["meta"] = response.get("meta", {})
    payload["connected"] = True
    payload["legacyTransport"] = False
    return json.dumps(payload)
