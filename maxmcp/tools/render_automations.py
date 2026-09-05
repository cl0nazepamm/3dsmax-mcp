"""Render-done signal ("the pinger") for 3ds Max — completion detection only.

A production render blocks Max's main thread (and the bridge with it), so
"finished" can't be learned by asking Max, and polling Max just lags the
viewport. The native bridge registers Max's own NOTIFY_POST_RENDER and writes a
small signal file at the exact completion event (render_handlers.cpp).

This tool does NOT fire the render. Firing a render from the bridge made Max
loop (a second render auto-starting after the first finished), so firing now
lives entirely outside the bridge. You trigger the render yourself — hit Render
in Max, or run ``execute_maxscript`` with ``max quick render`` — and this tool
only reports when it finishes:

  * ``start``  — arm the done-signal for the NEXT render (reads Render Setup for
                 the report; records the signal path). Returns a ``signal_path``
                 and a ready-to-run background ``watcher`` command. Does NOT
                 render — trigger the render yourself right after arming.
  * ``status`` — read that one signal file (never touches Max).
  * ``cancel`` — raise the render abort flag (works mid-render).

The waiting is an external, event-driven watcher (scripts/render_signal_wait.ps1)
the agent runs in the background; it exits when the signal lands, pinging the
agent to continue. Nothing the bridge does starts a render.
"""

import json
import os
from pathlib import Path
from uuid import uuid4

from ..server import mcp, client
from ..coerce import IntList


def _signal_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    base = Path(root) if root else Path.home() / "AppData" / "Local"
    d = base / "3dsmax-mcp" / "render_signals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _watcher_command(signal_path: str, timeout_sec: int) -> str:
    """The background command the agent runs to get pinged on completion."""
    # parents[1] is the package dir (wheel install), parents[2] the repo root (checkout).
    relative = Path("scripts") / "render_signal_wait.ps1"
    parents = Path(__file__).resolve().parents
    script = next(
        (parents[i] / relative for i in (1, 2) if (parents[i] / relative).is_file()),
        parents[2] / relative,
    )
    cmd = (
        f'powershell -NoProfile -ExecutionPolicy Bypass '
        f'-File "{script}" -SignalPath "{signal_path}"'
    )
    if timeout_sec > 0:
        cmd += f" -TimeoutSec {int(timeout_sec)}"
    return cmd


def _do_start(watch_timeout_sec: int) -> dict:
    job_id = uuid4().hex[:12]
    sig = _signal_dir() / f"{job_id}.done.json"
    try:
        sig.unlink()  # a stale file would read as instant-done
    except FileNotFoundError:
        pass

    payload = {"job_id": job_id, "signal_path": str(sig)}
    response = client.send_command(json.dumps(payload), cmd_type="native:render_start")
    raw = response.get("result", "")
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        result = {}

    if isinstance(result, dict) and result.get("status") == "error":
        return result

    return {
        "status": "armed",
        "job_id": job_id,
        "signal_path": str(sig),
        "output": result.get("output") if isinstance(result, dict) else None,
        "watcher": _watcher_command(str(sig), watch_timeout_sec),
        "hint": "ARMED — no render was started. Trigger the render yourself NOW: "
                "hit Render in Max, or call execute_maxscript with `max quick "
                "render`. Then run `watcher` in the background; it exits when the "
                "render finishes (the pinger writes the signal), and you read it "
                "with action=status. Do NOT arm again before this one completes — "
                "a second arm while one is pending returns an error.",
    }


def _do_cancel() -> dict:
    response = client.send_command("{}", cmd_type="native:render_cancel")
    raw = response.get("result", "")
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        result = {}
    if isinstance(result, dict):
        return result
    return {"status": "unknown", "raw": raw}


def _read_signal(job_id: str, signal_path: str) -> dict:
    sig = Path(signal_path) if signal_path else (_signal_dir() / f"{job_id}.done.json")
    if not sig.exists():
        return {"status": "rendering", "done": False, "signal_path": str(sig),
                "hint": "signal not written yet — render still in progress"}
    try:
        doc = json.loads(sig.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": f"could not read signal file: {exc}", "signal_path": str(sig)}
    doc.setdefault("status", "complete")
    doc["done"] = True
    doc["signal_path"] = str(sig)
    return doc


@mcp.tool()
def render_automations(
    action: str = "status",
    vfb: bool = True,
    job_id: str = "",
    signal_path: str = "",
    watch_timeout_sec: int = 3600,
    crop: IntList | None = None,
    capture_target: str = "vray_vfb",
) -> dict:
    """Arm a done-signal for the NEXT render, then report when it finishes.

    This does NOT start the render — firing a render from the bridge made Max
    loop, so you trigger the render yourself (hit Render, or execute_maxscript
    `max quick render`) after arming. The signal honors whatever Render Setup's
    Time Output produces; the bridge only detects completion.

    action:
      start    ARM the NOTIFY_POST_RENDER done-signal for the next render (does
               NOT render). Returns signal_path plus a background `watcher`
               command. After this, trigger the render yourself. Errors if a
               job is already armed/pending — never arm twice.
      status   Read the signal file for `job_id` (or `signal_path`). Never touches
               Max — "rendering" until it exists, else the completion record
               (the post-render event confirms the render ended, not its outcome;
               a crash or failure before that event can leave no signal).
      cancel   Request cancellation through Max's abort flag using a separate
               native pipe connection pinned to the same Max instance. It bypasses
               the normal connection's in-flight render. Cancellation is cooperative:
               the renderer must check the flag. A cancelling response is not proof
               that rendering has stopped; the completion signal does not reliably
               distinguish cancellation from success.
      cancel_capture  Save the visible VFB image, then request cancellation for
               the matching armed job_id, through one independent connection.
               Use only for a render you armed and started. crop=[x,y,width,height]
               trims physical client-area pixels; capture_target=screen is an
               explicit desktop fallback for another renderer's visible framebuffer.
               Requires the updated bridge. Does not wait for Max or denoising.
               Configure progressive sampling and the renderer's denoiser BEFORE
               starting a production preview; this cannot retrofit a blocked render.
               The result is partial evidence, never proof of a stopped/denoised render.

    watch_timeout_sec caps the watcher (default 3600; 0 = wait forever). On cap
    it prints {"status":"timeout"} and exits 2 — check action=status before
    deciding anything. (`vfb` is accepted but ignored — the bridge no longer
    fires the render, so it can't set the VFB flag.)
    """
    action = (action or "status").strip().lower()

    if action == "cancel_capture":
        from .viewport import _validate_screen_crop
        if not job_id:
            raise ValueError("cancel_capture requires the job_id you armed before starting this render")
        if capture_target not in {"vray_vfb", "screen"}:
            raise ValueError("capture_target must be vray_vfb or screen")
        if crop is not None:
            _validate_screen_crop(crop)
        payload = {"job_id":job_id, "target":capture_target, "max_width":1600}
        if crop is not None: payload["crop"] = list(crop)
        # A dedicated route makes old bridges fail BEFORE sending any global abort.
        response = client.send_command(json.dumps(payload), cmd_type="native:render_cancel_capture")
        raw = response.get("result", "")
        return json.loads(raw) if isinstance(raw, str) else raw

    if action == "start":
        return _do_start(watch_timeout_sec)

    if action == "cancel":
        return _do_cancel()

    if action == "status":
        if not job_id and not signal_path:
            return {"status": "error", "error": "provide job_id or signal_path"}
        return _read_signal(job_id, signal_path)

    return {"status": "error", "error": f"unknown action: {action} (use start|status|cancel|cancel_capture)"}
