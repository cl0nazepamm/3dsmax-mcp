from ..server import mcp, client


@mcp.tool()
def execute_maxscript(code: str) -> str:
    """Execute arbitrary MAXScript code in 3ds Max and return the result.

    The code is run via MAXScript's execute() function. The return value
    is the string representation of whatever the last expression evaluates to.

    Examples:
        execute_maxscript("objects.count")
        execute_maxscript("sphere radius:25 pos:[0,0,0]")
        execute_maxscript("for o in selection collect o.name")
    """
    response = client.send_command(code, cmd_type="maxscript")
    return response.get("result", "")


@mcp.tool()
def toggle_safe_execute(enabled: bool) -> str:
    """Toggle safe execution mode for MAXScript commands.

    When enabled, the server uses safeExecute instead of execute,
    which blocks potentially dangerous operations like DOSCommand,
    ShellLaunch, deleteFile, and python.Execute.

    Safe mode is OFF by default.

    Args:
        enabled: True to enable safe mode, False to disable.
    """
    value = "true" if enabled else "false"
    response = client.send_command(
        f"MCP_Server.safeMode = {value}", cmd_type="maxscript"
    )
    state = "enabled" if enabled else "disabled"
    return f"Safe execution mode {state}. Result: {response.get('result', '')}"
