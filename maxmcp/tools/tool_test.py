"""Direct in-Max tool invocation and live smoke testing."""

from __future__ import annotations

import json

from ..server import mcp, client


def _parse_native_result(response: dict) -> str:
    result = response.get("result", "")
    if isinstance(result, str):
        return result
    return json.dumps(result)


@mcp.tool()
def invoke_tool(tool: str, tool_input: dict | None = None) -> str:
    """Invoke any registered MCP tool inside 3ds Max via the native bridge."""
    payload = {"tool": tool, "input": tool_input or {}}
    response = client.send_command(
        json.dumps(payload),
        cmd_type="native:invoke_tool",
    )
    return _parse_native_result(response)


@mcp.tool()
def run_tool_smoke(
    tier: str = "read",
    include_skipped: bool = False,
    dry_run: bool = False,
) -> str:
    """Run generated live smoke cases against the native bridge inside 3ds Max."""
    payload = {
        "tier": tier,
        "includeSkipped": include_skipped,
        "dryRun": dry_run,
    }
    response = client.send_command(
        json.dumps(payload),
        cmd_type="native:tool_smoke",
        timeout=600.0,
    )
    return _parse_native_result(response)
