"""Job handles for long native calls: wait a while, then hand back a handle.

A job call waits up to wait_seconds for Max. If Max is still working, the
client keeps the call running there (it is never sent again) and the tool
returns {"status": "running", "job": ...}. Calling the tool again with that
job waits again and returns the result once Max finishes.

Jobs live in this MCP server process: a restart forgets them, although Max
still finishes the work.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ..max_client import JobPending

# Each wait stays well inside MCP clients' tool-call timeouts.
MAX_WAIT_SECONDS = 50
PROGRESS_TIMEOUT = 5.0

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


@dataclass
class Outcome:
    """A finished call's bridge response, or the report for one still running."""

    response: dict[str, Any] | None = None
    running: dict[str, Any] | None = None
    context: dict[str, Any] = field(default_factory=dict)


def _wait(wait_seconds: float) -> float:
    return max(0.0, min(float(wait_seconds), MAX_WAIT_SECONDS))


def start(client: Any, tool: str, cmd_type: str, payload: dict[str, Any], *, wait_seconds: float,
          timeout: float, progress: bool = False, context: dict[str, Any] | None = None) -> Outcome:
    """Send a native call as a job. progress adds a progress_key the handler reports under."""
    progress_key = uuid4().hex if progress else ""
    if progress_key:
        payload = {**payload, "progress_key": progress_key}
    context = dict(context or {})
    try:
        response = client.send_command(json.dumps(payload), cmd_type=cmd_type, timeout=timeout,
                                       job_wait=_wait(wait_seconds))
    except JobPending as pending:
        handle = f"{tool}:{pending.request_id}"
        entry = {"tool": tool, "request_id": pending.request_id, "progress_key": progress_key,
                 "context": context, "started": time.time()}
        with _lock:
            _jobs[handle] = entry
        return Outcome(running=_report(client, handle, entry, {"status": "running", "state": "running"}),
                       context=context)
    return Outcome(response=response, context=context)


def resume(client: Any, tool: str, job: str, *, wait_seconds: float) -> Outcome:
    """Wait again for a job this server started; the job is forgotten once it finishes."""
    with _lock:
        entry = _jobs.get(job)
    if entry is None or entry["tool"] != tool:
        raise ValueError(f"Unknown job {job!r} for {tool}. Jobs belong to the MCP server process that started "
                         "them; after a server restart, check the result in Max instead of repeating the work.")
    try:
        state = client.wait_job(entry["request_id"], _wait(wait_seconds))
    except KeyError:
        _forget(job)
        raise ValueError(f"Job {job!r} is no longer tracked: its connection to Max was dropped or replaced. "
                         "Check the result in Max instead of repeating the work.") from None
    except Exception:
        _forget(job)
        raise
    if state["status"] == "completed":
        _forget(job)
        return Outcome(response=state["response"], context=entry["context"])
    return Outcome(running=_report(client, job, entry, state), context=entry["context"])


def _forget(job: str) -> None:
    with _lock:
        _jobs.pop(job, None)


def _report(client: Any, job: str, entry: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    tool = entry["tool"]
    report: dict[str, Any] = {
        "status": state["status"],
        "job": job,
        "elapsed_s": round(time.time() - entry["started"], 1),
    }
    if state.get("state"):
        report["state"] = state["state"]  # queued: Max's main thread has not started it yet
    progress = _progress(client, entry.get("progress_key", ""))
    if progress is not None:
        report["progress"] = progress
    if state["status"] == "blocked_by_dialog":
        report["dialogs"] = state.get("dialogs", [])
        report["hint"] = (f"A dialog in Max holds this job. Answer it with max_dialogs (ask the user unless they "
                          f"authorized you to proceed), then call {tool}(job='{job}') to collect the result.")
    else:
        report["hint"] = (f"Still running in 3ds Max. Call {tool}(job='{job}') to wait again (up to "
                          f"{MAX_WAIT_SECONDS} s per call). Do not repeat the original call: it would run twice.")
    return report


def _progress(client: Any, key: str) -> dict[str, Any] | None:
    """What the native handler last reported, or None (no key, or an older bridge)."""
    if not key:
        return None
    try:
        response = client.send_command(json.dumps({"keys": [key]}), cmd_type="native:job_progress",
                                       timeout=PROGRESS_TIMEOUT)
        raw = response.get("result", "")
        data = json.loads(raw) if isinstance(raw, str) else raw
        state = (data or {}).get("progress", {}).get(key)
    except Exception:
        return None
    return state if isinstance(state, dict) else None
