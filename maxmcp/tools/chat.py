"""External MCP access to the in-Max standalone chat (work in progress).

Experimental — the Win32 chat window, registry sync, and chat_ui native path
may change. Prefer external MCP (Cursor, Claude Desktop, Codex) for production.
"""

import json as _json

from ..max_client import DEFAULT_TIMEOUT
from ..server import mcp, client


def _parse_chat_result(response: dict) -> str:
    payload = response.get("result", "{}")
    if isinstance(payload, str):
        data = _json.loads(payload or "{}")
    elif isinstance(payload, dict):
        data = payload
    else:
        raise RuntimeError(f"Unexpected chat payload type: {type(payload).__name__}")

    if not isinstance(data, dict):
        raise RuntimeError("Unexpected chat payload shape")

    error = data.get("error")
    if isinstance(error, str) and error:
        raise RuntimeError(f"Chat error: {error}")

    data["requestId"] = response.get("requestId")
    data["meta"] = response.get("meta", {})
    return _json.dumps(data)


@mcp.tool()
def send_to_chat(message: str, timeout_ms: int = 180000, silent: bool = False) -> str:
    """Send a message to the in-Max standalone chat (WIP) and block until the turn completes."""
    payload = _json.dumps({
        "action": "send",
        "message": message,
        "timeout_ms": timeout_ms,
        "silent": silent,
    })
    # Python pipe read must outlast the C++ deadline, otherwise we abandon the
    # in-flight turn and the next call hits "Chat is busy" while the C++ side
    # finishes silently.
    pipe_timeout = max(timeout_ms / 1000.0 + 5.0, DEFAULT_TIMEOUT)
    if pipe_timeout > DEFAULT_TIMEOUT:
        response = client.send_command(payload, cmd_type="native:chat_ui", timeout=pipe_timeout)
    else:
        response = client.send_command(payload, cmd_type="native:chat_ui")
    return _parse_chat_result(response)


@mcp.tool()
def chat_status() -> str:
    """Report the in-Max standalone chat status (visible/configured/model)."""
    payload = _json.dumps({"action": "status"})
    response = client.send_command(payload, cmd_type="native:chat_ui")
    return _parse_chat_result(response)


@mcp.tool()
def chat_reload() -> str:
    """Re-read local chat config and environment without restarting Max."""
    payload = _json.dumps({"action": "reload"})
    response = client.send_command(payload, cmd_type="native:chat_ui")
    return _parse_chat_result(response)


@mcp.tool()
def chat_clear() -> str:
    """Drop the in-Max chat's conversation history."""
    payload = _json.dumps({"action": "clear"})
    response = client.send_command(payload, cmd_type="native:chat_ui")
    return _parse_chat_result(response)
