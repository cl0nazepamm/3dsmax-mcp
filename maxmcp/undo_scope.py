"""Undo for tools that change the scene through plain MAXScript.

Native handlers each run in an undo step of their own. A MAXScript request runs in none,
so a tool that edits the scene through MAXScript alone could not be undone, and a
run_python script that failed after calling it could not roll it back. While one of the
tools below runs, each of its MAXScript requests asks the bridge for an undo step (the
request's `undo` label, see command_dispatcher.cpp). Scripts that manage theHold
themselves are sent as they are; the bridge never wraps a file command. Bridges that
predate the label ignore it.
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Iterator

# tyFlow graph tools are left out on purpose: with tyFlow 2.101 in 3ds Max 2027, a
# create_tyflow made inside an undo step crashes 3ds Max (pure virtual call) at the next
# save or hold once that step's restore records are freed, whether by a rollback or by a
# redo flush after undo. Outside an undo step, as before, nothing is recorded.
MAXSCRIPT_UNDO_TOOLS = frozenset({
    # Data Channel stacks
    "add_data_channel",
    "set_data_channel_operator",
    "manage_data_channel_stack",
    "add_dc_script_operator",
    "load_dc_preset",
    # construction
    "draw_spline",
    "scatter_forest_pack",
})

_label: contextvars.ContextVar[str | None] = contextvars.ContextVar("maxmcp_undo_label", default=None)


@contextlib.contextmanager
def tool_scope(tool: str) -> Iterator[None]:
    """While `tool` runs, its MAXScript requests carry an undo label if it is listed."""
    token = _label.set(f"MCP {tool}" if tool in MAXSCRIPT_UNDO_TOOLS else None)
    try:
        yield
    finally:
        _label.reset(token)


def request_fields(command: str, cmd_type: str) -> dict[str, Any]:
    """The undo field for one request of the running tool, or nothing."""
    label = _label.get()
    if not label or cmd_type != "maxscript" or "thehold" in command.lower():
        return {}
    return {"undo": label}
