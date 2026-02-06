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
