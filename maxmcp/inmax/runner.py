"""run_python inside 3ds Max: mx and sdk in-process, pymxs at hand, the whole script one undo step."""

from __future__ import annotations

import json
import sys
from typing import Any
from uuid import uuid4

from . import binding

UNDO_LABEL = "MCP run_python"


class _BridgeTooOld(Exception):
    """The bridge has no script_hold: the script falls back to SuperBegin grouping."""


class _HoldError(RuntimeError):
    """script_hold failed; code and message are the bridge's own when it gave them."""

    def __init__(self, action: Any, error: Any) -> None:
        text = error if isinstance(error, str) else json.dumps(error)
        if isinstance(error, str):
            try:
                error = json.loads(error)
            except ValueError:
                pass
        structured = error if isinstance(error, dict) else {}
        self.code = str(structured.get("code") or "")
        self.message = str(structured.get("message") or text)
        super().__init__(f"script_hold {action}: {text}")


def _script_hold(**args: Any) -> dict[str, Any]:
    request = json.dumps({"command": json.dumps(args), "type": "native:script_hold",
                          "requestId": uuid4().hex, "protocolVersion": 2})
    reply = json.loads(binding.bridge_call(request) or b"{}")
    if not reply.get("success"):
        error = reply.get("error")
        text = json.dumps(error) if not isinstance(error, str) else error
        if "Unknown command type" in text:
            raise _BridgeTooOld(text)
        raise _HoldError(args.get("action"), error)
    result = reply.get("result")
    return json.loads(result) if isinstance(result, str) and result else (result or {})


def _unrecorded(end: dict[str, Any], commit: bool) -> str:
    """What script_hold's end reply says the step could not roll back (or, committed, undo)."""
    fate = "cannot be undone" if commit else "was not rolled back"
    parts = []
    # Records lost in a step committed early matter to undo, never to rolling back the last step.
    if end.get("records_lost") or (commit and end.get("records_lost_earlier")):
        parts.append(
            "the script's undo records were discarded while it ran (a full MAXScript gc() does that, and so "
            f"does theHold.Cancel()), so what it changed before that point {fate}; use gc light:true inside "
            "a script")
    if end.get("left_suspended"):
        parts.append(
            "the script left the undo system suspended (theHold.Suspend() without Resume()), so what it "
            f"changed after that was never recorded and {fate}")
    return "; ".join(parts)


class ScriptStep:
    """Everything the script changes is one undo step, rolled back if the script raises.

    The bridge opens a hold for the script (script_hold), so raw pymxs and _mx edits are
    recorded too; each tool call steps out of it, since handlers refuse to join an open
    hold. Undo, reset and fetch inside the script commit the step so far and open a new
    one (`breaks`); a later failure rolls back only that last step. An older bridge
    groups only the tool calls (SuperBegin), and raw edits are not recorded.
    """

    def __init__(self, rt: Any, label: str = UNDO_LABEL) -> None:
        self.rt, self.label = rt, label
        self.legacy = False
        self.breaks = 0
        # Why the bridge could not close the step, when 3ds Max failed inside it.
        self.close_error = ""
        # What the step never recorded or no longer held when it closed: those edits
        # survive a rollback and cannot be undone.
        self.unrecorded = ""
        # The bridge's refusal to open the step: the script did not run.
        self.refused: _HoldError | None = None

    def __enter__(self) -> "ScriptStep":
        try:
            _script_hold(action="begin", label=self.label)
        except _HoldError as error:
            self.refused = error
            raise
        except _BridgeTooOld:
            self.legacy = True
            self.rt.theHold.SuperBegin()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        commit = exc_type is None
        if self.legacy:
            if commit:
                self.rt.theHold.SuperAccept(self.label)
            else:
                self.rt.theHold.SuperCancel()
            return False
        try:
            end = _script_hold(action="end", commit=commit)
            self.breaks = int(end.get("breaks") or 0)
            self.unrecorded = _unrecorded(end, commit)
        except RuntimeError as error:
            # A failed rollback must not hide the script's own error; a failed commit is the error.
            self.close_error = str(error)
            if commit:
                raise
        finally:
            # A reloaded core forgets the step: never leave Max holding.
            for _ in range(32):
                if not self.rt.theHold.Holding():
                    break
                if commit:
                    self.rt.theHold.Accept(self.label)
                else:
                    self.rt.theHold.Cancel()
        return False


def run_script(code: str, timeout_s: float = 300, label: str = UNDO_LABEL) -> dict[str, Any]:
    """Run an mx script on Max's main thread with mx, sdk (_mx), rt (pymxs.runtime) and pymxs in scope.

    Everything the script changes is one undo step, rolled back if it raises (ScriptStep).
    """
    import pymxs

    from .. import mx
    from ..mx import _script
    from . import sdk as sdk_loader
    from .host import activate

    rt = pymxs.runtime
    if rt.theHold.Holding():
        return {"status": "error", "code": "USER_BUSY",
                "error": "3ds Max has an open undo operation; finish it, then run the script again."}
    activate()
    namespace: dict[str, Any] = {"__name__": "__mx_script__", "mx": mx, "rt": rt, "pymxs": pymxs}
    note = ""
    try:
        sdk = sdk_loader.load()
    except Exception as exc:  # noqa: BLE001 - the script still runs with mx
        sdk, note = None, f"sdk (_mx) did not load: {type(exc).__name__}: {exc}"
    if sdk is not None:
        namespace["sdk"] = sdk
        mx.sdk = sdk  # type: ignore[attr-defined]
    step = ScriptStep(rt, label)
    reply = _script.execute(code, namespace, timeout_s, transaction=lambda: step)
    if step.refused is not None:
        # Nothing ran, so there is nothing to roll back: the refusal is the whole reply.
        return {"status": "error", "code": step.refused.code or "USER_BUSY", "error": step.refused.message}
    reply["ran_in"] = "max"
    if note:
        reply["note"] = note
    if reply.get("status") == "error":
        details = reply.setdefault("details", {})
        details["rolled_back"] = not (step.close_error or step.unrecorded)
        if step.close_error or step.unrecorded:
            details["rollback_error"] = step.close_error or f"Only part of the script was rolled back: {step.unrecorded}."
        if step.breaks:
            details["rolled_back_scope"] = (
                f"only what the script changed after its last undo/reset/fetch; the {step.breaks} step(s) "
                "before it were committed")
    elif step.unrecorded:
        reply.setdefault("warnings", []).append(f"Only part of the script can be undone: {step.unrecorded}.")
    elif step.legacy:
        reply.setdefault("warnings", []).append(
            "This 3ds Max bridge predates script undo steps: raw rt and sdk edits are not undoable and survive "
            "a failed script; update the 3dsmax-mcp plugin.")
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
