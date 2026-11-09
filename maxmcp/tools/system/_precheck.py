"""Read-only pre-checks that a dialog blocks before a tool has changed anything."""

from __future__ import annotations

import json

from ...core.max_client import ALWAYS_ASK, DialogBlocked


class PrecheckBlocked(Exception):
    """A tool's read-only pre-check is waiting on a dialog in Max, so its change was never sent.

    Unlike DialogBlocked, the tool call is safe to repeat once the dialog is answered;
    the queued check finishes harmlessly and its max_dialogs result can be ignored.
    """

    code = DialogBlocked.code
    retryable = True

    def __init__(self, blocked: DialogBlocked, tool: str, outcome: str) -> None:
        self.request_id = blocked.request_id
        self.dialogs = blocked.dialogs
        titles = ", ".join(repr(d.get("title", "")) for d in blocked.dialogs) or "a dialog"
        message = (f"{outcome}: 3ds Max is waiting on {titles}. Only the read-only check that {tool} runs "
                   "first was queued; it finishes harmlessly after the dialog is answered.")
        self.bridge_message = json.dumps({
            "type": "PrecheckBlocked", "code": self.code, "retryable": True, "message": message,
            "hint": ("Read the dialog with max_dialogs(action='inspect'). If the user has authorized you to proceed "
                     "unattended, answer it with max_dialogs(action='respond', dialog_id, expected_dialog, button) as "
                     "the task intends; otherwise ask the user which button to press. " + ALWAYS_ASK + " Then repeat "
                     f"{tool}; ignore the check's result that max_dialogs reports."),
            "details": {"request_id": blocked.request_id, "dialogs": blocked.dialogs,
                        "request_sent": True, "changed": False},
        })
        super().__init__(message)
