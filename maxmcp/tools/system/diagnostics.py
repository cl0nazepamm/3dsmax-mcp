"""Hang diagnostics from outside 3ds Max: native thread stacks plus heuristics.

OS-level only (toolhelp, EnumWindows, dbghelp): nothing is sent to Max, so it
works while Max's main thread is blocked.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ...core import process_health
from ...core import stackdump
from ...server import client, mcp

MAX_IMAGES = ("3dsmax.exe",)
# Saved captures kept in the diagnostics folder (a .txt/.json pair each); older ones are pruned.
MAX_SAVED_CAPTURES = 20
_SAVED_RE = re.compile(r"^hang-\d+-\d{8}-\d{6}(?:-\d+)?\.(?:txt|json)$")


def diagnostics_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    base = Path(root) if root else Path.home() / "AppData" / "Local"
    return base / "3dsmax-mcp" / "diagnostics"


def _failure(message: str, code: str, running: list[int], **details: Any) -> dict[str, Any]:
    return {"error": message, "code": code, "details": {"max_pids": running, **details}}


def resolve_pid(pid: int | None) -> dict[str, Any]:
    """{pid, source} of the Max to diagnose, or an error dict.

    Uses running 3dsmax.exe processes and the client's in-memory selection
    only: no bridge call, no pipe, nothing sent to Max.
    """
    running = [p["pid"] for p in process_health.list_processes(MAX_IMAGES)]
    listing = ", ".join(map(str, running)) or "none"
    if pid is not None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return _failure(f"pid must be a positive process ID, got {pid!r}.", "BAD_PARAM", running)
        if pid not in running:
            return _failure(f"PID {pid} is not a running 3ds Max (3dsmax.exe PIDs: {listing}).", "NOT_FOUND", running)
        return {"pid": pid, "source": "explicit"}
    selected = client.selected_pid_nowait()
    if selected.get("pid"):
        if selected["pid"] in running:
            return {"pid": selected["pid"], "source": selected.get("source") or "selected"}
        return _failure(f"The selected 3ds Max (PID {selected['pid']}) is not running (3dsmax.exe PIDs: {listing}). "
                        "Pass pid.", "NOT_FOUND", running, selected_pid=selected["pid"])
    if len(running) == 1:
        return {"pid": running[0], "source": "only_running"}
    if not running:
        return _failure("No 3ds Max (3dsmax.exe) process is running.", "NOT_FOUND", running)
    return _failure(f"Several 3ds Max processes are running ({listing}) and none is selected. "
                    "Pass pid=<one of them>.", "AMBIGUOUS", running)


def _save(pid: int, capture: dict[str, Any], summary: dict[str, Any], process: dict[str, Any]) -> dict[str, str]:
    folder = diagnostics_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"hang-{pid}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    name, n = stem, 1
    while (folder / f"{name}.txt").exists() or (folder / f"{name}.json").exists():
        n += 1
        name = f"{stem}-{n}"
    text_path, json_path = folder / f"{name}.txt", folder / f"{name}.json"
    text = stackdump.format_text(capture, summary) + f"# process: {process_health.describe(process)}\n"
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps({"capture": capture, "summary": summary, "process": process},
                                    indent=2, ensure_ascii=False), encoding="utf-8")
    _prune(folder, keep={text_path.name, json_path.name})
    return {"text": str(text_path), "json": str(json_path)}


def _prune(folder: Path, keep: set[str], limit: int | None = None) -> None:
    """Delete the oldest saved captures beyond `limit` pairs; never the ones in `keep`, never raises."""
    limit = MAX_SAVED_CAPTURES if limit is None else limit
    try:
        captures: dict[str, list[Path]] = {}
        for path in folder.iterdir():
            if _SAVED_RE.match(path.name) and path.is_file():
                captures.setdefault(path.stem, []).append(path)

        def newest(stem: str) -> float:
            return max((p.stat().st_mtime for p in captures[stem]), default=0.0)

        stems = sorted(captures, key=newest, reverse=True)
        for stem in stems[max(limit, 1):]:
            for path in captures[stem]:
                if path.name not in keep:
                    try:
                        path.unlink()
                    except OSError:
                        pass
    except OSError:
        pass


@mcp.tool()
def capture_hang_diagnostics(pid: int | None = None, all_threads: bool = False, depth: int = 48,
                             save: bool = True) -> dict:
    """Explain a hung 3ds Max from native thread stacks (what blocks the main thread).

    OS-only: never sends anything to Max, so it is safe while Max is hung; each
    thread is paused for a few milliseconds while its stack is read (on Windows 11
    the OS resumes it even if this server is killed meanwhile).
    Use when: a tool returned MAX_NOT_RESPONDING, or get_bridge_status reports
    not_responding.
    Not when: Max is responsive; this is not a profiler.
    pid defaults to the selected Max, else the only running one. all_threads=True
    dumps every thread (default: main thread, window owners, busy threads).
    save writes the full .txt/.json under %LOCALAPPDATA%/3dsmax-mcp/diagnostics
    (the newest 20 captures are kept).
    Returns findings, the main-thread stack preview, hung windows (visible ones;
    hidden ones are counted per thread) and process health.
    """
    target = resolve_pid(pid)
    if "error" in target:
        return target
    pid = target["pid"]
    process = process_health.diagnose_process(pid)
    capture = stackdump.capture_stacks(pid, all_threads=all_threads, depth=depth)
    errors = capture.get("errors") or []
    if not capture.get("threads"):
        reason = errors[0]["message"] if errors else "no thread could be read"
        return {"error": f"No stack could be captured for 3ds Max PID {pid}: {reason}", "code": "DIAGNOSTICS_FAILED",
                "details": {"process": process, "capture_errors": errors[:5]}}
    summary = stackdump.summarize(capture)
    suspended = [t["suspended_ms"] for t in capture["threads"] if t.get("suspended_ms") is not None]
    result: dict[str, Any] = {
        "pid": pid,
        "target_source": target["source"],
        "process": process,
        **summary,
        "threads": {"total": capture.get("thread_count"), "captured": len(capture["threads"]),
                    "omitted": capture.get("omitted", 0), "max_suspended_ms": max(suspended, default=None),
                    "elapsed_ms": capture.get("elapsed_ms")},
    }
    if errors:
        result["capture_errors"] = {"count": len(errors), "first": [e.get("message") for e in errors[:3]]}
    if save:
        try:
            result["saved"] = _save(pid, capture, summary, process)
        except OSError as exc:
            result["save_failed"] = f"{type(exc).__name__}: {exc}"
    return result
