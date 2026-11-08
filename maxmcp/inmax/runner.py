"""run_python inside 3ds Max: mx in-process, pymxs at hand, and the whole script one undo step."""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any, Iterator

from . import binding

UNDO_LABEL = "MCP run_python"


@contextlib.contextmanager
def _one_undo_step(rt: Any, label: str) -> Iterator[None]:
    """Group every guarded tool call of the script into one undo entry; roll all back on error.

    theHold.SuperBegin does not set Holding(), so each handler still opens, accepts or
    cancels its own hold (and keeps refusing to join the user's). SuperAccept merges them.
    """
    rt.theHold.SuperBegin()
    try:
        yield
    except BaseException:
        rt.theHold.SuperCancel()
        raise
    rt.theHold.SuperAccept(label)


def run_script(code: str, timeout_s: float = 300, label: str = UNDO_LABEL) -> dict[str, Any]:
    """Run an mx script on Max's main thread with mx, rt (pymxs.runtime) and pymxs in scope.

    The script's guarded tool calls form one undo step and are rolled back if the script
    raises. Raw rt edits are not recorded by Max's undo unless the script wraps them in
    pymxs.undo(True), inside which tool calls refuse with USER_BUSY.
    """
    import pymxs

    from .. import mx
    from ..mx import _script
    from .host import activate

    rt = pymxs.runtime
    if rt.theHold.Holding():
        return {"status": "error", "code": "USER_BUSY",
                "error": "3ds Max has an open undo operation; finish it, then run the script again."}
    activate()
    namespace: dict[str, Any] = {"__name__": "__mx_script__", "mx": mx, "rt": rt, "pymxs": pymxs}
    reply = _script.execute(code, namespace, timeout_s, transaction=lambda: _one_undo_step(rt, label))
    reply["ran_in"] = "max"
    if reply.get("status") == "error":
        reply.setdefault("details", {})["rolled_back"] = True
    return reply


def launch(code: str, timeout_s: float, server_python: tuple[int, int]) -> None:
    """Entry point for run_python's MAXScript launcher; the reply goes to MCP_RunPython_Result."""
    import pymxs

    if tuple(sys.version_info[:2]) != tuple(server_python):
        reply: dict[str, Any] = {"unavailable": (
            f"3ds Max runs Python {sys.version_info[0]}.{sys.version_info[1]} and the MCP server "
            f"{server_python[0]}.{server_python[1]}, so the tool layer cannot load inside Max")}
    elif not binding.available():
        reply = {"unavailable": "this 3ds Max's MCP bridge has no in-process entry point; update the plugin"}
    else:
        try:
            reply = run_script(code, timeout_s)
        except Exception as exc:  # noqa: BLE001 - the launcher must always answer
            reply = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "ran_in": "max"}
    pymxs.runtime.MCP_RunPython_Result = json.dumps(reply, ensure_ascii=True, default=repr)
