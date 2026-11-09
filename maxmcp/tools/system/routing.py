"""Process-local Max instance selection, available in every tool profile.

The selection belongs to the MCP server process, not to a chat session. When the client app
routes several sessions to one server process, they share it: one session's select or
release moves the others. selection_generation (in these replies and in every tool's
transport) changes on each select, release or re-resolve; pass expected_pid to calls that
must reach one particular Max.
"""
from ...server import client, mcp


@mcp.tool()
def list_max_instances() -> dict:
    """List live Max instances in default order without selecting one."""
    return client.list_max_instances()


@mcp.tool()
def select_max_instance(pid: int) -> dict:
    """Bind this MCP server process to a live Max PID until explicitly changed or released.

    The binding is shared by every session that uses this server process, so another session
    can move it. Note the returned selection_generation and pass expected_pid=pid to calls that
    must not reach another Max; they are refused (nothing sent) if the target moved.
    """
    return client.select_max_instance(pid)


@mcp.tool()
def get_selected_max_instance() -> dict:
    """Read this MCP server process's selected target and availability without switching it.

    selection_generation differing from the one select_max_instance returned means the
    selection changed since, possibly by another session sharing this server.
    """
    return client.get_selected_max_instance()


@mcp.tool()
def release_max_instance() -> dict:
    """Release this MCP server process's target, including startup pinning; next connection uses the default.

    This also releases it for every other session sharing this server process.
    """
    return client.release_max_instance()
