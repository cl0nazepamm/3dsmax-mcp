import ctypes
import ctypes.wintypes as wintypes
import json
import os
import re
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

from .process_health import describe as describe_process
from .process_health import diagnose_process, process_gone, process_start_time, quick_hung_check
from .process_health import list_processes

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 120.0
DEFAULT_PIPE_NAME = r"\\.\pipe\3dsmax-mcp"
MCP_PIPE_ENV = "MCP_MAX_PIPE"
# A call still unanswered after this long is checked for a blocking dialog.
DIALOG_CHECK_AFTER = 1.0
DIALOG_POLL_INTERVAL = 1.0
MAX_BLOCKED_CALLS = 16
# Reply deadline of every sent request (see MaxClient._await_response). Max
# cancels main-thread work that has not started within its executor timeout
# (120 s unless the handler sets more) and replies, so an ordinary call waits a
# little past that. A probe's timeout is its deadline, as is every control
# request's. Probes are marked explicitly (probe=True, _PROBE_CMD_TYPES), not by
# a short timeout.
NATIVE_QUEUE_TIMEOUT = 120.0
REPLY_GRACE = 10.0
# native:core control (hot-reloadable core). Only CORE_BUSY is retried: the
# shell kept the old core and changed nothing. Waits above CORE_RETRY_MAX_WAIT_MS
# get one attempt, so a long drain is never repeated.
CORE_RELOAD_ATTEMPTS = 3
CORE_RETRY_MAX_WAIT_MS = 20_000
CORE_DEFAULT_WAIT_MS = 5_000  # the shell's default when wait_ms is omitted
CORE_BUSY_BACKOFF = 0.5  # seconds before the first retry; doubles after each
CORE_MAX_WAIT_MS = 600_000  # kMcpCoreMaxWaitMs, native/include/mcp_bridge/shell_control.h
CORE_STATUS_TIMEOUT = 5.0


class RequestOutcomeUnknown(Exception):
    """The request reached Max but its response was lost; never replay it."""

    code = "REQUEST_OUTCOME_UNKNOWN"
    retryable = False

    def __init__(self, message: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = {"request_sent": True, **(details or {})}


# Wording shared by every report of a request whose reply was lost after it was sent.
OUTCOME_UNKNOWN_ADVICE = "The request may have committed; inspect before retrying."


class MaxTcpConnectTimeoutError(TimeoutError):
    """TCP connect timed out before sendall; no request reached the listener."""


class MaxBridgeUnavailableError(ConnectionError):
    """Auto transport found neither a ready native bridge nor a TCP listener."""

    code = "BRIDGE_DOWN"
    retryable = True

    def __init__(self, native_error: str, tcp_error: str, host: str, port: int,
                 tcp_failure: str = "connection_refused") -> None:
        candidates = [p["pid"] for p in list_processes()]
        self.details = {
            "requested_transport": "auto", "preferred_transport": "namedpipe",
            "attempted_transports": ["namedpipe", "tcp"], "running_max_pids": candidates,
            "native_state": "unavailable", "native_error": native_error, "tcp_error": tcp_error,
            "tcp_endpoint": f"{host}:{port}", "request_sent": False,
            "tcp_failure": tcp_failure,
        }
        if candidates:
            message = (f"No native MCP bridge is ready/reachable; running Max candidate PIDs: {candidates}. "
                       "Max may still be starting, or its MCP Bridge plugin may be missing/stopped. "
                       "Wait and recheck list_max_instances; if startup is complete, check the native plugin. ")
        else:
            message = "No native MCP bridge is ready/reachable and no running Max process was found. Start Max and recheck. "
        failure = "timed out before connecting" if tcp_failure == "connect_timeout" else "refused the connection"
        super().__init__(message + f"Requested auto transport (namedpipe preferred); TCP fallback at {host}:{port} "
                         f"{failure}. Native error: {native_error}.")


ALWAYS_ASK = ("Always ask the user before Save, Don't Save or any other discard, overwrite, Fetch or Reset "
              "choice, even when working unattended.")


class DialogBlocked(Exception):
    """A call is waiting on a modal dialog in Max and keeps running there.

    The client keeps its connection and reports the eventual result through
    blocked_calls(); the call must not be repeated.
    """

    code = "BLOCKED_BY_DIALOG"
    retryable = False

    def __init__(self, request_id: str, command: str, dialogs: list[dict[str, Any]]) -> None:
        self.request_id = request_id
        self.dialogs = dialogs
        titles = ", ".join(repr(d.get("title", "")) for d in dialogs) or "a dialog"
        message = (f"3ds Max is waiting on {titles}. The call is still running inside Max "
                   "and finishes after the dialog is answered.")
        self.bridge_message = json.dumps({
            "type": "DialogBlocked", "code": self.code, "retryable": False, "message": message,
            "hint": ("Read the dialog. If the user has authorized you to proceed unattended, answer it with "
                     "max_dialogs(action='respond', dialog_id, expected_dialog, button) as the task intends; "
                     "otherwise ask the user which button to press. " + ALWAYS_ASK + " Do not repeat the "
                     "call: max_dialogs reports its result after the dialog closes."),
            "details": {"request_id": request_id, "command": command, "dialogs": dialogs},
        })
        super().__init__(message)


class MaxHealthError(Exception):
    """Max cannot take a request right now; carries code/retryable/details."""

    code = "MAX_BUSY"
    retryable = True

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class MaxBusyError(MaxHealthError):
    """Max is occupied (pipe held or main thread busy); the request did not run, so it may be retried."""


class MaxNotRespondingError(MaxHealthError):
    """Max is hung or gone. Raised before sending unless it is also RequestOutcomeUnknown."""

    code = "MAX_NOT_RESPONDING"
    retryable = False


class MaxNotRespondingAfterDispatch(MaxNotRespondingError, RequestOutcomeUnknown):
    """Max stopped responding after the request was written; never replay it."""


BLOCKED_CAUSE = "deadlock, stalled I/O, or a long blocking call (possibly from another MCP client)"
HANG_ADVICE = ("Some stalls (e.g. a V-Ray Material Editor preview render) clear after several minutes, but a "
               "cross-thread deadlock never does: wait up to ~10 min, re-checking with get_bridge_status. If "
               "Max is still blocked after that, treat it as a deadlock: the user must end the process "
               "(unsaved work is lost).")


class MaxBusy(RequestOutcomeUnknown):
    """A call got no reply within its deadline and may still be running in Max.

    The client drops the connection the call holds and never replays it. An
    ordinary call is tracked like a DialogBlocked one: blocked_calls() reports
    its eventual result. Control requests are not tracked.

    With a PID, the deadline diagnosis picks the error: MaxNotRespondingAfterDispatch
    once Max exited or stayed blocked, this error while Max is busy, responsive or
    can't be judged (process then carries the evidence). Unlike MaxBusyError, the
    request was sent, so it is never retryable.
    """

    code = "MAX_BUSY"
    retryable = False

    def __init__(self, request_id: str, command: str, waited: float,
                 state: str | None = None, tracked: bool = True,
                 process: dict[str, Any] | None = None) -> None:
        self.request_id = request_id
        evidence = f" ({describe_process(process)})" if process else ""
        if tracked:
            where = "waiting for Max's main thread" if state == "queued" else "running inside 3ds Max"
            message = f"3ds Max has not answered after {waited:g} s{evidence}. The call is still {where}."
            hint = ("Do not repeat the call: it is still in progress and would run twice. Check "
                    "max_dialogs(action='inspect'): blocked_calls reports this call's result once Max "
                    "finishes it, and dialogs lists anything holding Max.")
        else:
            message = f"3ds Max has not answered {command} after {waited:g} s{evidence}."
            hint = "The request may still finish inside Max. Check its effect with a fresh read before repeating it."
        details: dict[str, Any] = {"request_id": request_id, "command": command, "waited_s": round(waited, 1),
                                   "request_sent": True}
        if state:
            details["state"] = state
        if process:
            details["process"] = process
        self.bridge_message = json.dumps({
            "type": "MaxBusy", "code": self.code, "retryable": False, "message": message,
            "hint": {"message": hint, "suggested_tools": ["max_dialogs"]} if tracked else hint,
            "details": details,
        })
        super().__init__(message)


class JobPending(Exception):
    """A job-mode call outlived its wait and keeps running in Max.

    Not a failure: the client keeps the call (it is never sent again), and
    wait_job() collects its result. blocked_calls() leaves it to its tool.
    """

    def __init__(self, request_id: str, cmd_type: str, waited: float) -> None:
        super().__init__(f"{cmd_type} is still running in 3ds Max after {waited:.1f} s")
        self.request_id = request_id
        self.cmd_type = cmd_type
        self.waited = waited


class TargetUnavailable(ConnectionError):
    """The bound Max's pipe is gone, e.g. after a crash or restart.

    An explicit or environment target never moves to another Max on its own;
    retrying cannot succeed until the target is selected again or released.
    (A default or claimed target whose Max exited re-resolves before sending
    instead, on every path: see MaxClient._route.)
    """

    code = "TARGET_UNAVAILABLE"
    retryable = False

    def __init__(self, target: dict[str, Any], reason: str, *, awaiting_restart: list[int] | None = None) -> None:
        if awaiting_restart is not None:
            # A default target whose Max exited while other Max instances, already open then, still run.
            self.retryable = True
            pids = ", ".join(map(str, awaiting_restart))
            message = (f"3ds Max PID {target.get('target_pid')}, which this server was using, has exited. "
                       f"Nothing was sent. 3ds Max PID {pids} was already open while it was in use, so it may be "
                       "another session's and is never taken over unasked.")
            hint = (f"Retry once 3ds Max has restarted: the restarted Max is picked up automatically. To work in "
                    f"PID {pids} instead, call select_max_instance. Re-inspect the scene either way: handles from "
                    "the old session are invalid.")
            tools = ["list_max_instances", "select_max_instance"]
        else:
            message = (f"Selected 3ds Max target {target['target_pipe']} is unavailable. "
                       f"Select another instance or release it explicitly. {reason}")
            hint = ("The 3ds Max this server was bound to is gone (exited or restarted). Do not retry. Check "
                    "list_max_instances, then select_max_instance(pid) or release_max_instance "
                    "to use the default Max. Re-inspect the scene: handles from the old session "
                    "are invalid.")
            tools = ["list_max_instances", "select_max_instance", "release_max_instance"]
        self.bridge_message = json.dumps({
            "type": "TargetUnavailable", "code": self.code, "retryable": self.retryable, "message": message,
            "hint": {"message": hint, "suggested_tools": tools},
            "details": {"target_pid": target.get("target_pid"), "target_source": target.get("target_source"),
                        **({"other_pids": awaiting_restart} if awaiting_restart is not None else {})},
        })
        super().__init__(message)


# Win32 constants for named pipe
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_ERROR_BROKEN_PIPE = 109
_ERROR_SEM_TIMEOUT = 121
_ERROR_PIPE_BUSY = 231

# CreateFileW returns HANDLE; set proper return type for correct comparison
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_kernel32.WaitNamedPipeW.restype = wintypes.BOOL
_kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_kernel32.WriteFile.restype = wintypes.BOOL
_kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_kernel32.ReadFile.restype = wintypes.BOOL
_kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_kernel32.PeekNamedPipe.restype = wintypes.BOOL
_kernel32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.GetNamedPipeServerProcessId.restype = wintypes.BOOL
_kernel32.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
_INVALID_HANDLE = wintypes.HANDLE(-1).value

INSTANCE_LOCK_TIMEOUT = 10.0
_BLOCKED_CONFIRM_S = 20.0
_CPU_SAMPLE_S = 1.0
_RECHECK_SAMPLE_S = 0.25
# Read-only probes: safe to drop at their deadline instead of waiting out a hung Max.
_PROBE_CMD_TYPES = frozenset({"ping", "health"})
# Answered on a bridge pipe thread without touching Max's main thread. Sent on the
# control channel so a stuck request holding the pipe lock cannot delay them.
_MAIN_THREAD_FREE_CMD_TYPES = frozenset({"health", "native:max_dialogs", "native:job_progress"})
# Separate connection: bypasses the pipe lock and the hung-PID latch.
# native:core is the bridge shell's hot-reloadable core control (status / reload).
_CONTROL_CMD_TYPES = frozenset({"native:render_cancel", "native:render_cancel_capture",
                                "native:capture_screen", "native:core"}) | _MAIN_THREAD_FREE_CMD_TYPES
# Native ExecuteSync cancels work the main thread never started after 120 s.
_QUEUE_TIMEOUT_MARKER = "main thread execution timed out"
# Modeless tool windows, never dialogs: the auto dialog check neither reports nor
# inspects them (messaging a Cosmos browser on a deadlocked importer thread blocks).
# Same titles as DialogWatch's ToolWindow(); this covers older bridges without it.
_NOT_DIALOG_TITLE = re.compile(r"^(?:Chaos Cosmos Browser$|Material Editor(?:$| - )|Slate Material Editor|"
                               r"AGENT VIEWPORT|Floating Viewport)")


def real_dialogs(dialogs: Any) -> list[dict[str, Any]]:
    """The listed dialogs without modeless tool windows (_NOT_DIALOG_TITLE), for bridges that list them."""
    return [d for d in dialogs or [] if isinstance(d, dict) and not _NOT_DIALOG_TITLE.match(str(d.get("title", "")))]


def _grace(timeout: float) -> float:
    return max(10.0, 0.1 * timeout)


class AmbiguousMaxInstanceError(ConnectionError):
    """Raised when multiple live Max native bridges exist and none is claimed."""


class MaxTargetMismatchError(ConnectionError):
    """The call carried expected_pid and the routed Max is another one; nothing was sent."""

    code = "TARGET_MISMATCH"
    retryable = False

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = {"request_sent": False, **(details or {})}


# A call that raised one of these may still be running in Max, Max can't take it
# (busy or hung), or its target is gone or not the
# expected one. Tools pass them through unchanged so their code and hint reach the
# agent, instead of reporting an ordinary failure that invites a retry or a fallback.
CALL_STATE_ERRORS = (DialogBlocked, RequestOutcomeUnknown, TargetUnavailable, MaxHealthError,
                     MaxTargetMismatchError)


# Targets the server picked by itself; they re-resolve once their Max exits.
# Explicit and environment targets keep failing hard instead: a session that
# chose a Max must never reach another one silently.
_REBINDABLE_SOURCES = frozenset({"default", "claim"})


def _filetime_now() -> int:
    """Now as a Windows FILETIME (100 ns since 1601), comparable with process_start_time."""
    return int(time.time() * 10_000_000) + 116_444_736_000_000_000

# expected_pid of the tool call running on this thread, shared by every MaxClient.
_call_guard = threading.local()


@contextmanager
def expect_pid(pid: int | None) -> Iterator[None]:
    """Refuse every bridge call made on this thread unless it reaches Max `pid` (None: no check)."""
    if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0):
        raise ValueError("expected_pid must be a positive process ID")
    previous = getattr(_call_guard, "pid", None)
    _call_guard.pid = pid
    try:
        yield
    finally:
        _call_guard.pid = previous


class MaxBridgeError(Exception):
    """Raised when the native/TCP bridge returns a structured error response."""

    def __init__(self, message: str, response: dict[str, Any]) -> None:
        self.bridge_message = message
        self.bridge_response = response
        super().__init__(f"MAXScript error: {message}")


def _bridge_error_code(exc: MaxBridgeError) -> str:
    """Return the structured NativeError code carried by a bridge error, if any."""
    try:
        payload = json.loads(exc.bridge_message)
    except (TypeError, ValueError):
        return ""
    return str(payload.get("code") or "") if isinstance(payload, dict) else ""


class _PipeReader:
    """Reads one newline-terminated response on its own thread.

    The caller may stop waiting while Max sits in a modal dialog; the reader
    then owns the pipe handle and closes it once the response arrives.
    """

    def __init__(self, handle: int) -> None:
        self.handle = handle
        self.done = threading.Event()
        self.data = b""
        self.error: BaseException | None = None
        self._detached = False
        self._lock = threading.Lock()
        threading.Thread(target=self._run, name="max-pipe-reader", daemon=True).start()

    def _run(self) -> None:
        response = bytearray()
        buf = ctypes.create_string_buffer(65536)
        try:
            while True:
                bytes_read = wintypes.DWORD()
                ok = _kernel32.ReadFile(self.handle, buf, len(buf), ctypes.byref(bytes_read), None)
                if bytes_read.value > 0:
                    response.extend(buf.raw[:bytes_read.value])
                    if b"\n" in response:
                        self.data = bytes(response)
                        return
                if not ok:
                    err = ctypes.get_last_error()
                    if err == _ERROR_BROKEN_PIPE:
                        raise BrokenPipeError("Pipe closed while reading response.")
                    raise ConnectionError(f"Failed reading from pipe: Win32 error {err}")
                if bytes_read.value == 0:
                    raise BrokenPipeError("Pipe closed before response terminator.")
        except BaseException as exc:
            self.error = exc
        finally:
            with self._lock:
                self.done.set()
                if self._detached:
                    _kernel32.CloseHandle(self.handle)

    def detach(self) -> None:
        with self._lock:
            self._detached = True
            if self.done.is_set():
                _kernel32.CloseHandle(self.handle)

    def result(self) -> bytes:
        if self.error is not None:
            raise self.error
        return self.data


class MaxClient:
    """Client that sends commands to 3ds Max via named pipe or TCP."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        transport: str = "auto",
        pipe_name: str = DEFAULT_PIPE_NAME,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.transport = transport
        self.pipe_name = pipe_name
        self._pipe_handle: Optional[int] = None
        self._selected_pipe_name: Optional[str] = None
        self._pipe_lock = threading.Lock()
        self._local = threading.local()
        self._control_channel = False
        self._diagnose_probes = True  # off when the caller discards a dropped probe's error
        self._pinned_pipe_name: str | None = None
        self._bound_target: dict[str, Any] | None = None
        self._bound_started: int | None = None  # creation time of the bound Max, against PID reuse
        # FILETIME of the bound Max's last reply. A Max started after it can be that Max's restart;
        # one already running then is another session's and is never taken over unasked.
        self._bound_alive_at: int | None = None
        # Bumped on every select, release, default bind and exited-default re-resolve. The selection
        # is per server process, so a session that sees it change unasked shares this server.
        self._selection_generation = 0
        self._retargeted: dict[str, Any] | None = None  # re-resolve note for the next reply
        self._inflight: dict[str, Any] | None = None
        self._hung_pids: dict[int, dict[str, Any]] = {}
        self._blocked: dict[str, dict[str, Any]] = {}
        self._blocked_lock = threading.Lock()
        self._no_dialog_watch: set[str] = set()  # pipes whose bridge has no native:max_dialogs
        env_pipe = os.environ.get(MCP_PIPE_ENV)
        env_pid = os.environ.get("MCP_MAX_PID")
        if env_pid and (not env_pid.isdecimal() or int(env_pid) <= 0):
            raise ValueError("MCP_MAX_PID must be a positive process ID")
        self._startup_pipe = env_pipe or (fr"\\.\pipe\3dsmax-mcp-pid-{int(env_pid)}" if env_pid else None)
        self._startup_source = "environment" if self._startup_pipe else "explicit"
        if self._startup_pipe is None and pipe_name != DEFAULT_PIPE_NAME:
            self._startup_pipe = pipe_name

    def clear_last_response(self) -> None:
        """Start a tool call: forget the previous call's metadata, including a re-route note."""
        self._reset_response()
        self._local.retargeted = None

    def _reset_response(self) -> None:
        """Before each request: clear the last request's metadata.

        A re-route note survives until the next tool call (clear_last_response), so a
        tool that sends several requests still reports that its Max changed.
        """
        self._local.last_response = None
        self._local.last_error = None

    def get_last_transport(self) -> dict[str, Any] | None:
        """Return compact transport metadata from the last command on this thread."""
        response = getattr(self._local, "last_response", None)
        if isinstance(response, dict):
            meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
            transport = {
                "transport": meta.get("transport"),
                "requested_transport": meta.get("requestedTransport"),
                "request_id": response.get("requestId"),
                "protocol_version": meta.get("protocolVersion"),
                "client_round_trip_ms": meta.get("clientRoundTripMs"),
                "fallback_error": meta.get("fallbackError"),
                **meta.get("target", {}),
            }
            dialogs = real_dialogs(meta.get("openDialogs"))
            if dialogs:
                transport["open_dialogs"] = dialogs
            if meta.get("quietOverride"):
                # Quiet mode was off for this script (it may reset, open or quit), so its prompts were shown.
                transport["quiet_override"] = meta["quietOverride"]
            return self._with_retarget(transport)
        error = getattr(self._local, "last_error", None)
        if isinstance(error, dict):
            return self._with_retarget(error)
        return self._with_retarget(None)

    def _with_retarget(self, transport: dict[str, Any] | None) -> dict[str, Any] | None:
        """Add this tool call's re-route note when an earlier request carried it."""
        note = getattr(self._local, "retargeted", None)
        if not note or (transport and transport.get("retargeted_from")):
            return transport
        return {**note, **(transport or {}), "retargeted_from": note["retargeted_from"]}

    @property
    def native_available(self) -> bool:
        """Check whether the native C++ bridge is currently available."""
        if self.transport == "pipe":
            return True
        if self.transport == "tcp":
            return False
        try:
            # Routed like the call that follows, so a restarted Max is not mistaken for a missing bridge.
            return self._probe_pipe_available(self._next_pipe())
        except (ConnectionError, TimeoutError):
            return False

    def _config_dir(self) -> Path:
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "3dsmax-mcp"
        return Path.home() / "AppData" / "Local" / "3dsmax-mcp"

    def _load_instance(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("pipe"), str):
            return None
        return data

    def _active_instance(self) -> dict[str, Any] | None:
        return self._load_instance(self._config_dir() / "active_instance.json")

    @staticmethod
    def _target(pipe: str, source: str) -> dict[str, Any]:
        match = re.search(r"-pid-(\d+)$", pipe)
        return {"target_pid": int(match[1]) if match else None,
                "target_pipe": pipe, "target_source": source, "pinned": True}

    def _live_instances(self) -> list[dict[str, Any]]:
        live = []
        for path in (self._config_dir() / "instances").glob("*.json"):
            data = self._load_instance(path)
            try:
                updated = path.stat().st_mtime_ns
            except OSError:
                continue
            if data and self._probe_pipe_available(data["pipe"]):
                live.append({**data, "updated": updated})
            elif data:
                self._prune_exited_instance(path, data, updated)
        return sorted(live, key=lambda item: item["updated"], reverse=True)

    def _prune_exited_instance(self, path: Path, data: dict[str, Any], updated: int) -> None:
        """Remove only an unchanged native PID record with a certainly exited owner.

        Missing pipe alone is not proof: startup, shutdown, a hung process and
        access failures must retain their registration. Never prune claims or
        custom records; the native bridge removes its own records on clean exit.
        """
        pid = data.get("pid")
        if type(pid) is not int or pid <= 0:
            return
        if (path.name != f"pid-{pid}.json" or data.get("instance_id") != f"pid-{pid}"
                or data["pipe"].casefold() != rf"\\.\pipe\3dsmax-mcp-pid-{pid}".casefold()):
            return
        if not process_gone(pid):
            return
        try:
            # Another bridge/client may have refreshed or removed it since the
            # scan. Recheck both identity and liveness immediately before unlink.
            if path.stat().st_mtime_ns != updated or self._load_instance(path) != data:
                return
            if self._probe_pipe_available(data["pipe"]) or not process_gone(pid):
                return
            path.unlink()
        except OSError:
            pass  # best effort: racing cleanup or read-only registry never breaks routing

    def _default_target(self) -> dict[str, Any]:
        live = self._live_instances()
        active = self._active_instance()
        try:
            claimed = (self._config_dir() / "active_instance.json").stat().st_mtime_ns
        except OSError:
            claimed = 0
        if active and self._probe_pipe_available(active["pipe"]) and (not live or claimed >= live[0]["updated"]):
            return self._target(active["pipe"], "claim")
        return self._target(live[0]["pipe"] if live else DEFAULT_PIPE_NAME, "default")

    def _resolve_pipe_name(self) -> str:
        target = self._bound_target
        if self._pinned_pipe_name is not None:
            target = self._target(self._pinned_pipe_name, "explicit")
        if target is None and self._startup_pipe:
            target = self._target(self._startup_pipe, self._startup_source)
        if target is None:
            target = self._default_target()
        self._local.route_candidate = dict(target)
        return target["target_pipe"]

    def selected_pid_nowait(self) -> dict[str, Any]:
        """PID of the Max this client is talking to, from memory only: {pid, source, pipe}.

        Never takes the pipe lock, opens a pipe or sends, so it is safe while a
        request is stuck in a hung Max. Order: the in-flight request's target,
        the bound/pinned target, then the environment. pid is None when unknown.
        """
        inflight = self._inflight
        if inflight and inflight.get("target_pid"):
            return {"pid": int(inflight["target_pid"]), "source": "inflight", "pipe": inflight.get("target_pipe")}
        candidates = []
        if self._pinned_pipe_name is not None:
            candidates.append((self._pinned_pipe_name, "explicit"))
        if self._bound_target:
            bound = (self._bound_target.get("target_pipe"), self._bound_target.get("target_source"))
            if self._bound_exited():
                # Where the next call goes (_drop_exited_default): the one Max started since this
                # one last answered. Otherwise report the exited selection, never another session's Max.
                gone = self._bound_target.get("target_pid")
                restarted = [p["pid"] for p in list_processes() if p["pid"] != gone and self._started_since_alive(p["pid"])]
                if len(restarted) == 1:
                    bound = (fr"\\.\pipe\3dsmax-mcp-pid-{restarted[0]}", "restarted")
            candidates.append(bound)
        if self._startup_pipe:
            candidates.append((self._startup_pipe, self._startup_source))
        for pipe, source in candidates:
            handle = self._pipe_handle if pipe and pipe == self._selected_pipe_name else None
            pid = self._pid_for_pipe(pipe, handle)
            if pid:
                return {"pid": pid, "source": source or "selected", "pipe": pipe}
        return {"pid": None, "source": None, "pipe": None}

    def list_max_instances(self) -> dict[str, Any]:
        with self._instance_lock():
            default = self._default_target()
            instances = self._live_instances()
            instances.sort(key=lambda item: item["pipe"] != default["target_pipe"])
            return {"instances": [{**item, "default": item["pipe"] == default["target_pipe"],
                                   "selected": bool(self._bound_target and item["pipe"] == self._bound_target["target_pipe"])}
                                  for item in instances]}

    def _bind(self, target: dict[str, Any]) -> dict[str, Any]:
        """Bind `target` as this server's selection under a new selection_generation."""
        self._selection_generation += 1
        self._bound_target = {**target, "selection_generation": self._selection_generation}
        pid = target.get("target_pid")
        self._bound_started = process_start_time(pid) if pid else None
        self._bound_alive_at = _filetime_now()
        return self._bound_target

    def _mark_alive(self, pid: int | None) -> None:
        """The bound Max answered: a Max started before now is not its restart."""
        if pid and self._bound_target and self._bound_target.get("target_pid") == pid:
            self._bound_alive_at = _filetime_now()

    def _started_since_alive(self, pid: int | None) -> bool:
        """`pid` started after the bound Max last answered, so it can be that Max's restart."""
        started = process_start_time(pid) if pid else None
        return bool(started and self._bound_alive_at and started > self._bound_alive_at)

    def _drop_exited_default(self) -> None:
        """Follow a default/claim target whose Max has exited to its restart, never to another session's Max.

        The restart is the Max that started after the old one last answered. A Max that was
        already running then belongs to another session and is never taken over unasked:
          - a live claim wins (the user chose it);
          - exactly one Max started since: bind it, and the reply warns;
          - several started since: AmbiguousMaxInstanceError;
          - none started since, other Max running: TargetUnavailable, retryable, binding kept,
            so the restart is picked up once it is up;
          - no other Max at all: forget the target; the next call resolves the Max that starts.
        Explicit and environment targets never move, and a busy or hung Max is never dropped.
        Nothing is sent before this decision.
        """
        target = self._bound_target
        if self._control_channel or self._pinned_pipe_name is not None or not self._bound_exited():
            return
        pid = target["target_pid"]
        others = [item for item in self._live_instances() if item["pipe"] != target.get("target_pipe")]
        chosen: dict[str, Any] | None = None
        if others:
            default = self._default_target()
            if default["target_source"] == "claim":
                chosen = default
            else:
                pid_of = lambda item: item.get("pid") or self._pid_for_pipe(item["pipe"])  # noqa: E731
                restarted = [item for item in others if self._started_since_alive(pid_of(item))]
                if len(restarted) > 1:
                    pids = ", ".join(str(pid_of(item)) for item in restarted)
                    raise AmbiguousMaxInstanceError(
                        f"The default 3ds Max target (PID {pid}) has exited and {len(restarted)} instances started "
                        f"since (PIDs {pids}). Nothing was sent. Call select_max_instance with the one you mean.")
                if not restarted:
                    raise TargetUnavailable(target, "", awaiting_restart=[pid_of(item) for item in others])
                chosen = self._target(restarted[0]["pipe"], "default")
        self._close_pipe_handle()
        self._selected_pipe_name = None
        self._bound_target = None
        self._bound_started = None
        self._bound_alive_at = None
        self._selection_generation += 1
        self._retargeted = {"pid": pid, "pipe": target.get("target_pipe"), "source": target.get("target_source")}
        if chosen is not None:
            self._bind(chosen)

    def _route(self) -> str:
        """The pipe a call reaches. The single routing step for every path (main, control,
        native_available), with the pipe lock held: an exited default/claim target is dropped
        first, so no path keeps aiming at a Max that is gone after a restart."""
        self._drop_exited_default()
        return self._resolve_pipe_name()

    def _next_pipe(self) -> str:
        """The pipe the next call reaches. A call in flight holds the lock on a live target."""
        if not self._pipe_lock.acquire(blocking=False):
            return self._resolve_pipe_name()
        try:
            return self._route()
        finally:
            self._pipe_lock.release()

    def _bound_exited(self) -> bool:
        """The bound default/claim target's Max has exited, so the next call re-resolves."""
        target = self._bound_target
        return bool(target and target.get("target_source") in _REBINDABLE_SOURCES and target.get("target_pid")
                    and process_gone(target["target_pid"], self._bound_started))

    def _unreachable_target(self, target: dict[str, Any], exc: Exception) -> Exception:
        """The error for a selected target that could not be reached. Nothing was sent.

        TargetUnavailable (not retryable) when its pipe is gone, as after a crash or
        restart; otherwise a retryable ConnectionError (busy pipe, slow start).
        """
        self._local.last_error = {**target, "transport": "namedpipe", "error": str(exc)}
        if not self._probe_pipe_available(target["target_pipe"]):
            return TargetUnavailable(target, str(exc))
        return ConnectionError(f"Selected 3ds Max target {target['target_pipe']} is unavailable. "
                               f"Select another instance or release it explicitly. {exc}")

    def get_selected_max_instance(self) -> dict[str, Any]:
        with self._instance_lock():
            target = self._bound_target or (self._target(self._startup_pipe, self._startup_source) if self._startup_pipe else None)
            if target is None:
                return {"target_pid": None, "target_pipe": None, "target_source": None, "pinned": False,
                        "available": False, "selection_generation": self._selection_generation}
            result = {"selection_generation": self._selection_generation, **target,
                      "available": self._probe_pipe_available(target["target_pipe"])}
            if target is self._bound_target and self._bound_exited():
                result["exited"] = True
                result["note"] = ("This default target's Max has exited. The next call follows its restart; "
                                  "a Max that was already open is never taken over unasked.")
            return result

    def select_max_instance(self, pid: int) -> dict[str, Any]:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("pid must be a positive process ID")
        pipe = fr"\\.\pipe\3dsmax-mcp-pid-{pid}"
        with self._instance_lock():
            if not self._probe_pipe_available(pipe):
                raise ConnectionError(f"3ds Max PID {pid} is unavailable; selection was not changed")
            previous = self._bound_target
            self._close_pipe_handle()
            self._selected_pipe_name = None
            result = {**self._bind(self._target(pipe, "explicit")), "available": True}
            if previous and previous.get("target_pipe") != pipe:
                result["previous"] = {"target_pid": previous.get("target_pid"),
                                      "target_source": previous.get("target_source")}
                if previous.get("target_source") == "explicit":
                    result["warning"] = (
                        f"This replaced an explicit selection of PID {previous.get('target_pid')}. The selection "
                        "belongs to this MCP server process, so any other session that shares the server now "
                        "reaches PID %d too. Pass expected_pid to calls that must reach one Max." % pid)
            return result

    def release_max_instance(self) -> dict[str, Any]:
        with self._instance_lock():
            self._close_pipe_handle()
            self._selected_pipe_name = None
            self._bound_target = None
            self._bound_started = None
            self._startup_pipe = None
            self._selection_generation += 1
            return {"target_pid": None, "target_pipe": None, "target_source": None, "pinned": False,
                    "selection_generation": self._selection_generation}

    # ── Hang diagnosis ───────────────────────────────────────────
    @contextmanager
    def _instance_lock(self, timeout: float | None = None) -> Iterator[None]:
        timeout = INSTANCE_LOCK_TIMEOUT if timeout is None else timeout
        if not self._pipe_lock.acquire(timeout=max(0.001, timeout)):
            raise self._busy_error(timeout)
        try:
            yield
        finally:
            self._pipe_lock.release()

    def inflight(self) -> dict[str, Any] | None:
        """This client's in-flight request (cmd_type, request_id, target, running_s), if any."""
        return self._inflight_snapshot()

    def hung_verdict(self, pid: int | None) -> dict[str, Any] | None:
        """The remembered not-responding verdict for `pid` (incl. the abandoned inflight), if any."""
        verdict = self._hung_pids.get(pid) if pid else None
        return dict(verdict) if verdict else None

    def _inflight_snapshot(self) -> dict[str, Any] | None:
        inflight = self._inflight
        if not inflight:
            return None
        snapshot = {k: v for k, v in inflight.items() if k != "started"}
        snapshot["running_s"] = round(time.perf_counter() - inflight["started"], 1)
        return snapshot

    @staticmethod
    def _pid_for_pipe(pipe_name: str | None, handle: Any = None) -> int | None:
        match = re.search(r"-pid-(\d+)$", pipe_name or "")
        if match:
            return int(match[1])
        if handle in (None, 0, _INVALID_HANDLE):
            return None
        pid = wintypes.ULONG()
        try:
            if _kernel32.GetNamedPipeServerProcessId(handle, ctypes.byref(pid)) and pid.value:
                return int(pid.value)
        except Exception:
            pass
        return None

    @staticmethod
    def _label(pid: int | None, pipe: str | None = None) -> str:
        return f"3ds Max (PID {pid})" if pid else f"3ds Max ({pipe or 'unknown pipe'})"

    def _busy_error(self, waited: float) -> MaxHealthError:
        """Build the error for a pipe lock that stayed held for ``waited`` seconds."""
        inflight = self._inflight_snapshot()
        pid = (inflight or {}).get("target_pid") or (self._bound_target or {}).get("target_pid")
        process = diagnose_process(pid, _CPU_SAMPLE_S) if pid else None
        state = (process or {}).get("state")
        details = {"inflight": inflight, "process": process, "waited_s": round(waited, 1), "request_sent": False}
        label = self._label(pid, (inflight or {}).get("target_pipe"))
        running = (f"request '{inflight['cmd_type']}' has been running for {inflight['running_s']:.0f} s"
                   if inflight else "another call holds the connection")
        limit = (inflight or {}).get("timeout_s")
        overdue = limit is not None and inflight["running_s"] >= limit + _grace(limit)
        # One sample is not enough to call a healthy-but-stalled op hung; only an overdue request is.
        if state == "exited" or (state == "blocked" and overdue):
            what = ("Max has exited." if state == "exited"
                    else "Max is blocked: " + BLOCKED_CAUSE + ".")
            return MaxNotRespondingError(
                f"{label} is not responding: {describe_process(process)}, {running}. {what} "
                "Nothing was sent. Do not retry yet. " + HANG_ADVICE,
                details,
            )
        evidence = f" ({describe_process(process)})" if process else ""
        advice = ("Max may be blocked; call get_bridge_status again shortly to confirm." if state == "blocked"
                  else "Retry later, or call get_bridge_status to check whether Max is responding.")
        return MaxBusyError(
            f"{label} is still busy with another request{evidence}: {running}; waited {waited:.0f} s. "
            f"Nothing was sent. {advice}",
            details,
        )

    def _remember_hung(self, pid: int, process: dict[str, Any], inflight: dict[str, Any] | None,
                       source: str) -> None:
        self._hung_pids[pid] = {"state": process.get("state"), "process": process, "inflight": inflight,
                                "source": source, "started": process_start_time(pid)}

    def _check_expected_pid(self, pid: int | None, pipe_name: str, connected: bool = False) -> None:
        """Refuse (nothing sent) when this thread's tool call expects another Max."""
        expected = getattr(_call_guard, "pid", None)
        if expected is None or pid == expected or (pid is None and not connected):
            return
        target = getattr(self._local, "route_candidate", None) or self._bound_target or {}
        raise MaxTargetMismatchError(
            f"expected_pid is {expected}, but this call would reach {self._label(pid, pipe_name)} "
            f"(target_source {target.get('target_source')!r}, selection_generation {self._selection_generation}). "
            "Nothing was sent. The Max selection belongs to the MCP server process, and another session sharing "
            f"this server may have changed it. Call select_max_instance({expected}) and retry.",
            {"expected_pid": expected, "target_pid": pid, "target_pipe": pipe_name,
             "target_source": target.get("target_source"), "selection_generation": self._selection_generation},
        )

    @staticmethod
    def _refuse_unverifiable_pid() -> None:
        expected = getattr(_call_guard, "pid", None)
        if expected is not None:
            raise MaxTargetMismatchError(
                f"expected_pid is {expected}, but this call would go over the TCP bridge, which can't tell which "
                "Max answers. Nothing was sent. Load the native bridge in that Max, or call without expected_pid.",
                {"expected_pid": expected, "transport": "tcp"},
            )

    def _check_known_hung(self, pid: int | None, pipe_name: str) -> None:
        """Fail fast (nothing sent) while a PID previously judged not responding is still blocked."""
        verdict = self._hung_pids.get(pid) if pid else None
        if verdict is None:
            return
        if verdict.get("started") != process_start_time(pid) or quick_hung_check(pid) is not True:
            self._hung_pids.pop(pid, None)  # PID reused/gone, or the window pumps messages again
            return
        process = diagnose_process(pid, _RECHECK_SAMPLE_S)
        # The window is still hung. A short CPU sample alone must not clear the verdict. Other Max
        # threads wake now and then, so a 0.25 s sample of a hung Max sometimes reads "busy" and the
        # call would go to a Max that cannot serve it. Only a window that pumps again, an exited
        # process or a reused PID ends the verdict.
        if process.get("state") not in ("blocked", "busy"):
            self._hung_pids.pop(pid, None)
            return
        why = ("an earlier request was abandoned" if verdict.get("source") == "abandoned"
               else "the bridge cancelled an earlier request its main thread never picked up")
        raise MaxNotRespondingError(
            f"{self._label(pid, pipe_name)} is still not responding ({describe_process(process)}; {why}). "
            "Nothing was sent. " + HANG_ADVICE,
            {"inflight": None, "process": process, "previous": verdict.get("inflight"),
             "previous_process": verdict.get("process"), "request_sent": False},
        )

    def _probe_pipe_available(self, pipe_name: str | None = None) -> bool:
        """Best-effort probe that treats a busy pipe as available."""
        pipe_name = pipe_name or self.pipe_name
        handle = _kernel32.CreateFileW(
            pipe_name,
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_EXISTING,
            0,
            None,
        )
        if handle != _INVALID_HANDLE:
            _kernel32.CloseHandle(handle)
            return True

        err = ctypes.get_last_error()
        if err in (_ERROR_PIPE_BUSY, _ERROR_ACCESS_DENIED):
            return True
        if err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
            return False

        if _kernel32.WaitNamedPipeW(pipe_name, 0):
            return True
        wait_err = ctypes.get_last_error()
        if wait_err in (_ERROR_SEM_TIMEOUT, _ERROR_PIPE_BUSY, _ERROR_ACCESS_DENIED):
            return True
        return False

    def _close_pipe_handle(self) -> None:
        handle = self._pipe_handle
        if handle not in (None, 0, _INVALID_HANDLE):
            _kernel32.CloseHandle(handle)
        self._pipe_handle = None

    def _ensure_pipe_handle(self, deadline: float, pipe_name: str) -> int:
        handle = self._pipe_handle
        if handle not in (None, 0, _INVALID_HANDLE):
            return handle

        while True:
            handle = _kernel32.CreateFileW(
                pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                0,
                None,
            )
            if handle != _INVALID_HANDLE:
                self._pipe_handle = handle
                return handle

            err = ctypes.get_last_error()
            if err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
                raise ConnectionError(
                    f"Named pipe {pipe_name} not found. "
                    "Is the MCP Bridge plugin loaded in 3ds Max?"
                )
            if err != _ERROR_PIPE_BUSY:
                raise ConnectionError(f"Failed to open pipe: Win32 error {err}")

            remaining_ms = int((deadline - time.perf_counter()) * 1000)
            if remaining_ms <= 0:
                raise TimeoutError(
                    f"Timed out waiting for named pipe {pipe_name} after "
                    f"{self.timeout}s."
                )

            wait_ms = min(remaining_ms, 250)
            if _kernel32.WaitNamedPipeW(pipe_name, wait_ms):
                continue
            wait_err = ctypes.get_last_error()
            if wait_err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
                raise ConnectionError(
                    f"Named pipe {pipe_name} disappeared while waiting."
                )
            if wait_err in (_ERROR_SEM_TIMEOUT, _ERROR_PIPE_BUSY):
                continue
            raise ConnectionError(
                f"Failed waiting for named pipe {self.pipe_name}: "
                f"Win32 error {wait_err}"
            )

    def _send_control_command(self, command: str, cmd_type: str, timeout: Optional[float],
                              probe: bool = False, diagnose: bool = True) -> dict[str, Any]:
        """Bypass an in-flight request's pipe lock; routed like any other call.

        Cancellation, dialog recovery, pure desktop capture, the main-thread-free
        health probe, job progress and core control use this channel. Without a call
        in flight it routes through _route(), so a default whose Max restarted moves
        to the new default (with the same warning) and a selected Max that is gone
        fails with TargetUnavailable. A call in flight keeps its own, live, target.
        It never falls back to TCP. probe=True drops a read-only request at its
        deadline instead of waiting out a stuck bridge; diagnose=False skips the
        process diagnosis on that drop, for callers that discard the error.
        """
        acquired = self._pipe_lock.acquire(blocking=False)
        try:
            if not acquired and self._bound_target is None:
                raise ConnectionError("Max target selection is in progress; retry the control request")
            pipe = self._route() if acquired else self._bound_target["target_pipe"]
            selected = self._bound_target is not None or bool(self._startup_pipe)
            target = dict(self._bound_target or getattr(self._local, "route_candidate", self._target(pipe, "default")))
            self._local.control_pipe = pipe
            control = MaxClient(host=self.host, port=self.port, timeout=timeout or min(self.timeout, 15.0),
                                transport="pipe", pipe_name=pipe)
            control._control_channel = True
            control._diagnose_probes = diagnose
            control._bound_target = target
            if acquired:
                control._retargeted, self._retargeted = self._retargeted, None  # the reply carries the warning
            self._reset_response()
            try:
                response = control.send_command(command, cmd_type=cmd_type, timeout=timeout, probe=probe)
                if acquired and self._bound_target is None:
                    self._bind(target)
                self._mark_alive(target.get("target_pid"))
                return response
            except (AmbiguousMaxInstanceError, MaxTargetMismatchError, TargetUnavailable):
                raise
            except (ConnectionError, TimeoutError) as exc:
                # Nothing was sent: a request lost after sending raises RequestOutcomeUnknown.
                if selected and not self._probe_pipe_available(target["target_pipe"]):
                    raise self._unreachable_target(target, exc) from exc
                raise
            finally:
                if acquired and self._bound_target is None and getattr(control._local, "request_target", None):
                    self._bind(target)
                self._local.last_response = getattr(control._local, "last_response", None)
                if getattr(control._local, "retargeted", None):
                    self._local.retargeted = control._local.retargeted
                self._local.last_error = getattr(control._local, "last_error", None)
                control._close_pipe_handle()
        finally:
            if acquired:
                self._pipe_lock.release()

    def send_command(
        self,
        command: str,
        cmd_type: str = "maxscript",
        timeout: Optional[float] = None,
        request_fields: Optional[dict[str, Any]] = None,
        *,
        probe: bool = False,
        job_wait: float | None = None,
    ) -> dict[str, Any]:
        """Send a command to 3ds Max and return the parsed JSON response.

        request_fields adds handler options beside the command, e.g. quiet.
        probe=True marks a read-only command that may be dropped at its deadline.
        job_wait: over the named pipe, wait this many seconds for the reply, then
        raise JobPending and keep the call running in Max for wait_job().
        """
        if cmd_type in _CONTROL_CMD_TYPES and self.transport != "tcp" and not self._control_channel:
            return self._send_control_command(command, cmd_type, timeout, probe=probe)
        effective_timeout = timeout or self.timeout
        request_id = uuid4().hex
        started_at = time.perf_counter()
        transport_used = self.transport
        fallback_error: str | None = None
        self._reset_response()

        request = json.dumps({
            **(request_fields or {}),
            "command": command,
            "type": cmd_type,
            "requestId": request_id,
            "protocolVersion": 2,
        }, ensure_ascii=True)

        if self.transport == "pipe":
            transport_used = "namedpipe"
            response_data = self._send_via_pipe(request, effective_timeout, cmd_type=cmd_type, request_id=request_id,
                                                probe=probe, job_wait=job_wait)
        elif self.transport == "tcp":
            transport_used = "tcp"
            self._refuse_unverifiable_pid()
            response_data = self._send_via_tcp(request, effective_timeout)
        else:
            try:
                transport_used = "namedpipe"
                response_data = self._send_via_pipe(request, effective_timeout, cmd_type=cmd_type,
                                                    request_id=request_id, probe=probe, job_wait=job_wait)
            except (AmbiguousMaxInstanceError, MaxBusyError, MaxNotRespondingError, MaxTargetMismatchError,
                    TargetUnavailable):
                raise
            except (ConnectionError, TimeoutError) as exc:
                if self._bound_target or self._startup_pipe or self._pinned_pipe_name:
                    target = self._bound_target or self._target(self._startup_pipe or self._pinned_pipe_name, self._startup_source)
                    raise self._unreachable_target(target, exc) from exc
                fallback_error = str(exc)
                transport_used = "tcp"
                self._refuse_unverifiable_pid()
                try:
                    response_data = self._send_via_tcp(request, effective_timeout)
                except (ConnectionRefusedError, MaxTcpConnectTimeoutError) as tcp_exc:
                    failure = "connect_timeout" if isinstance(tcp_exc, MaxTcpConnectTimeoutError) else "connection_refused"
                    unavailable = MaxBridgeUnavailableError(fallback_error, str(tcp_exc), self.host, self.port, failure)
                    self._local.last_error = {"transport": "tcp", **unavailable.details, "error": str(unavailable)}
                    raise unavailable from tcp_exc

        try:
            response = self._parse_response(response_data, request_id, started_at)
        except Exception as exc:
            self._local.last_error = {
                "transport": transport_used,
                "requested_transport": self.transport,
                "request_id": request_id,
                "error": str(exc),
                "fallback_error": fallback_error,
            }
            raise

        meta = response.setdefault("meta", {})
        meta.setdefault("transport", transport_used)
        meta.setdefault("requestedTransport", self.transport)
        if transport_used == "namedpipe":
            meta["target"] = getattr(self._local, "request_target", None) or self._bound_target or {}
        if fallback_error:
            meta.setdefault("fallbackError", fallback_error)
        self._local.last_response = response
        return response

    # ── Named Pipe transport ─────────────────────────────────────
    def _send_via_pipe(
        self,
        request: str,
        timeout: float,
        *,
        cmd_type: str | None = None,
        request_id: str | None = None,
        probe: bool = False,
        job_wait: float | None = None,
    ) -> bytes:
        data = (request + "\n").encode("utf-8")
        # Nothing is sent while another call holds the pipe, so giving up early is safe to retry.
        lock_wait = min(timeout, INSTANCE_LOCK_TIMEOUT)
        if not self._pipe_lock.acquire(timeout=max(0.001, lock_wait)):
            raise self._busy_error(lock_wait)
        deadline = time.perf_counter() + timeout  # the call's own time starts once it holds the pipe
        try:
            pipe_name = self._route()
            if self._selected_pipe_name != pipe_name:
                self._close_pipe_handle()
                self._selected_pipe_name = pipe_name
            self._check_expected_pid(self._pid_for_pipe(pipe_name), pipe_name)
            self._check_known_hung(self._pid_for_pipe(pipe_name), pipe_name)

            for attempt in range(2):
                handle = self._ensure_pipe_handle(deadline, pipe_name)
                pid = self._pid_for_pipe(pipe_name, handle)
                self._check_expected_pid(pid, pipe_name, connected=True)
                self._check_known_hung(pid, pipe_name)
                if self._bound_target is None:
                    self._bind(getattr(self._local, "route_candidate", None) or self._target(pipe_name, "default"))
                self._local.request_target = dict(self._bound_target)
                if self._retargeted:
                    self._local.request_target["retargeted_from"] = self._retargeted
                    self._local.retargeted = dict(self._local.request_target)
                    self._retargeted = None
                self._inflight = {"cmd_type": cmd_type, "request_id": request_id, "target_pipe": pipe_name,
                                  "target_pid": pid, "timeout_s": timeout, "started": time.perf_counter()}
                try:
                    total_written = 0
                    while total_written < len(data):
                        written = wintypes.DWORD()
                        ok = _kernel32.WriteFile(
                            handle,
                            data[total_written:],
                            len(data) - total_written,
                            ctypes.byref(written),
                            None,
                        )
                        total_written += written.value
                        if not ok:
                            err = ctypes.get_last_error()
                            if err == _ERROR_BROKEN_PIPE:
                                raise BrokenPipeError("Pipe closed while writing request.")
                            raise ConnectionError(
                                f"Failed writing to pipe: Win32 error {err}"
                            )
                        if written.value == 0:
                            raise ConnectionError(
                                "Pipe write returned 0 bytes written."
                            )

                    reply = self._await_response(handle, request_id or "", cmd_type or "", deadline=deadline,
                                                 timeout=timeout, pid=pid,
                                                 probe=probe or cmd_type in _PROBE_CMD_TYPES, job_wait=job_wait)
                    if pid:
                        self._hung_pids.pop(pid, None)  # Max answered, so any old verdict is stale
                        self._mark_alive(pid)
                    self._check_queue_timeout(reply, pid)
                    return reply
                except BrokenPipeError:
                    self._close_pipe_handle()
                    if total_written:
                        raise RequestOutcomeUnknown("Pipe closed after dispatch. The request may have committed; inspect before retrying.") from None
                    if attempt == 0 and time.perf_counter() < deadline:
                        continue
                    raise ConnectionError("Named pipe connection closed during request.")
                except ConnectionError:
                    self._close_pipe_handle()
                    if total_written:
                        raise RequestOutcomeUnknown("Connection lost after dispatch. The request may have committed; inspect before retrying.") from None
                    if attempt == 0 and time.perf_counter() < deadline:
                        continue
                    raise
                finally:
                    self._inflight = None
        finally:
            self._pipe_lock.release()

    def _release_reader(self, reader: _PipeReader) -> None:
        """Stop waiting on a reply without closing its handle under a pending ReadFile.

        CloseHandle on a synchronous pipe handle waits for the reader's ReadFile, i.e.
        for Max to answer. The reader closes the handle once its read returns.
        """
        self._pipe_handle = None
        reader.detach()

    def _abandon_request(self, pid: int, process: dict[str, Any], blocked_since: float | None,
                         reader: _PipeReader, *, request_id: str = "", cmd_type: str = "",
                         sent_at: float | None = None, timeout: float | None = None) -> None:
        """Give up the connection and raise MaxNotRespondingAfterDispatch (never replay).

        The call stays tracked, so blocked_calls() reports its result if Max ever answers.
        """
        inflight = self._inflight_snapshot()
        self._detach(reader, request_id, cmd_type, dialog=False, sent_at=sent_at, timeout=timeout)
        details = {"inflight": inflight, "process": process, "request_sent": True}
        running = (f"request '{inflight['cmd_type']}' running for {inflight['running_s']:.0f} s"
                   if inflight else "request in flight")
        if process.get("state") == "exited":
            raise MaxNotRespondingAfterDispatch(
                f"{self._label(pid)} is not responding: {describe_process(process)} while {running}. "
                "The request may have partly committed before Max exited; do not replay it blindly. "
                "Ask the user to restart Max, then inspect the scene.",
                details,
            )
        details["blocked_for_s"] = round(time.perf_counter() - blocked_since, 1) if blocked_since else None
        self._remember_hung(pid, process, inflight, "abandoned")
        raise MaxNotRespondingAfterDispatch(
            f"{self._label(pid)} is not responding: {describe_process(process)}, {running}. "
            "Max is blocked: " + BLOCKED_CAUSE + ". Do not replay this request. " + HANG_ADVICE,
            details,
        )

    def _abandon_probe(self, pid: int | None, timeout: float, reader: _PipeReader) -> None:
        """Drop a read-only probe that missed its deadline (safe: it changes nothing).

        Returns without raising when the reply arrived during the diagnosis. Without
        _diagnose_probes the CPU sample is skipped: the caller discards the error.
        """
        process = diagnose_process(pid, _CPU_SAMPLE_S) if pid and self._diagnose_probes else None
        if reader.done.is_set():
            return
        inflight = self._inflight_snapshot()
        self._release_reader(reader)
        state = (process or {}).get("state")
        details = {"inflight": inflight, "process": process, "request_sent": True}
        label = self._label(pid, (inflight or {}).get("target_pipe"))
        probe = f"'{(inflight or {}).get('cmd_type') or 'probe'}'"
        if state in ("blocked", "exited"):
            what = "Max has exited." if state == "exited" else "Max is blocked: " + BLOCKED_CAUSE + "."
            raise MaxNotRespondingError(
                f"{label} is not responding: {describe_process(process)}; {probe} got no answer within "
                f"{timeout:g} s. {what} The probe was dropped (it changes nothing). " + HANG_ADVICE,
                details,
            )
        evidence = f" ({describe_process(process)})" if process else ""
        raise MaxBusyError(
            f"{label} did not answer {probe} within {timeout:g} s{evidence}: its main thread is busy. "
            "The probe was dropped (it changes nothing). Retry later.",
            details,
        )

    def _check_queue_timeout(self, reply: bytes, pid: int | None) -> None:
        """Explain a native queue timeout: the main thread never started the request in 120 s."""
        if not pid or _QUEUE_TIMEOUT_MARKER.encode() not in reply.lower():
            return
        try:
            response = json.loads(reply.decode("utf-8-sig", errors="replace"))
        except ValueError:
            return
        if (not isinstance(response, dict) or response.get("success", False)
                or _QUEUE_TIMEOUT_MARKER not in str(response.get("error", "")).lower()):
            return
        process = diagnose_process(pid, _CPU_SAMPLE_S)
        state = process.get("state")
        if state not in ("blocked", "busy", "exited"):
            return
        inflight = self._inflight_snapshot()
        details = {"inflight": inflight, "process": process, "request_sent": True, "executed": False,
                   "bridge_error": str(response.get("error"))}
        what = (f"Its main thread never picked up request '{(inflight or {}).get('cmd_type')}', "
                "so the bridge cancelled it before it ran.")
        if state == "busy":
            raise MaxBusyError(
                f"{self._label(pid)} is busy: {describe_process(process)}. {what} "
                "Retry later, or call get_bridge_status to check whether Max is responding.",
                details,
            )
        if state == "blocked":
            self._remember_hung(pid, process, inflight, "queue_timeout")
        cause = "Max has exited." if state == "exited" else "Max is blocked: " + BLOCKED_CAUSE + "."
        raise MaxNotRespondingError(
            f"{self._label(pid)} is not responding: {describe_process(process)}. {what} {cause} "
            "Do not retry yet. " + HANG_ADVICE,
            details,
        )

    def _reply_wait(self, timeout: float, probe: bool = False) -> float:
        """Seconds a sent request waits for its reply before it is left running in Max.

        The reply deadline (since 1.7.6): Max cancels main-thread work that has not
        started within its executor timeout (120 s unless the handler sets more) and
        replies, so an ordinary call waits a little past that. A probe's timeout, and
        every control request's, is its deadline. With a known PID the deadline
        diagnosis in _await_response picks the error.
        """
        if probe or self._control_channel:
            return timeout
        return max(timeout, NATIVE_QUEUE_TIMEOUT) + REPLY_GRACE

    def _await_response(self, handle: int, request_id: str, cmd_type: str, *, deadline: float,
                        timeout: float, pid: int | None, probe: bool = False,
                        job_wait: float | None = None) -> bytes:
        """Wait for the reply on a reader thread; this thread never blocks in ReadFile.

        Precedence while waiting (the pre-send refusals - pipe lock, hung-PID latch -
        already passed, so the request was written):
        1. From DIALOG_CHECK_AFTER, every DIALOG_POLL_INTERVAL (not on the control
           channel): a dialog holding this request raises DialogBlocked
           (BLOCKED_BY_DIALOG). Max keeps running the call; blocked_calls() reports it.
        2. At its deadline a probe is dropped (MAX_BUSY / MAX_NOT_RESPONDING).
        3. At the reply deadline (_reply_wait) anything else stops waiting. With a
           PID, outside the control channel, Max is diagnosed first: an exited Max,
           or one still blocked _BLOCKED_CONFIRM_S later, gives
           MaxNotRespondingAfterDispatch, and later calls fail fast while it stays
           hung. A busy, responsive or undiagnosable Max gives MaxBusy (MAX_BUSY,
           sent, never replayed). Either way blocked_calls() reports the call's
           result if Max ever answers (control requests are not tracked).
        A job (job_wait set, tracked) skips 2-3: after job_wait it raises JobPending
        and its tool collects the result with wait_job(). A long call is expected,
        so it is not diagnosed.
        A reply that arrives meanwhile always wins, and no wait is unbounded: a
        runaway script on a Max that keeps burning CPU ends in MAX_BUSY.
        """
        reader = _PipeReader(handle)
        sent_at = time.perf_counter()
        watch = bool(request_id) and not self._control_channel
        job = watch and job_wait is not None and not probe
        next_dialog = time.perf_counter() + DIALOG_CHECK_AFTER if watch else float("inf")
        next_check = (deadline if probe else sent_at + max(0.0, job_wait) if job
                      else sent_at + self._reply_wait(timeout))
        blocked_since: float | None = None
        while not reader.done.wait(max(0.0, min(next_dialog, next_check) - time.perf_counter())):
            if time.perf_counter() >= next_dialog:
                dialogs = self._dialogs_blocking(request_id)
                if reader.done.is_set():
                    break
                if dialogs:
                    # Max keeps running the call; its connection now belongs to the reader.
                    self._detach(reader, request_id, cmd_type, dialog=True, sent_at=sent_at, timeout=timeout,
                                 probe=probe)
                    raise DialogBlocked(request_id, cmd_type, dialogs)
                next_dialog = time.perf_counter() + DIALOG_POLL_INTERVAL
            now = time.perf_counter()
            if now < next_check:
                continue
            if probe:
                self._abandon_probe(pid, timeout, reader)
                continue  # the reply arrived during the diagnosis
            if job:
                self._detach(reader, request_id, cmd_type, dialog=False, sent_at=sent_at, timeout=timeout, job=True)
                raise JobPending(request_id, cmd_type, now - sent_at)
            process = diagnose_process(pid, _CPU_SAMPLE_S) if pid and not self._control_channel else None
            if reader.done.is_set():
                break
            state = (process or {}).get("state")
            now = time.perf_counter()
            if state == "exited" or (state == "blocked" and blocked_since is not None
                                     and now - blocked_since >= _BLOCKED_CONFIRM_S):
                self._abandon_request(pid, process, blocked_since, reader, request_id=request_id,
                                      cmd_type=cmd_type, sent_at=sent_at, timeout=timeout)
            if state == "blocked":
                # One sample is not enough to call Max hung: confirm it before giving up on the call.
                if blocked_since is None:
                    blocked_since = now
                next_check = blocked_since + _BLOCKED_CONFIRM_S
                continue
            # The status read can take a moment; a reply that lands meanwhile is used.
            request_state = self._request_state(request_id) if watch else None
            if reader.done.is_set():
                break
            self._detach(reader, request_id, cmd_type, dialog=False, sent_at=sent_at, timeout=timeout)
            raise MaxBusy(request_id, cmd_type, time.perf_counter() - sent_at, request_state,
                          tracked=not self._control_channel, process=process)
        return reader.result()

    def _detach(self, reader: _PipeReader, request_id: str, cmd_type: str, *, dialog: bool,
                sent_at: float | None = None, timeout: float | None = None, probe: bool = False,
                job: bool = False) -> None:
        """Leave a sent call running in Max and keep its reader for blocked_calls().

        Max keeps running the call; its connection now belongs to the reader, and the
        next call opens a fresh one. dialog marks a BLOCKED_BY_DIALOG release (the
        calls blocked_calls(wait) waits for); MAX_BUSY and MAX_NOT_RESPONDING releases
        are only reported. A control client serves one request: nothing asks it for
        results later, so it tracks nothing.
        """
        self._release_reader(reader)
        if self._control_channel or not request_id:
            return
        now = time.perf_counter()
        with self._blocked_lock:
            while len(self._blocked) >= MAX_BLOCKED_CALLS:
                # Read-only probes go first: a real call's result must stay reportable.
                victim = next((rid for rid, entry in self._blocked.items() if entry.get("probe")),
                              next(iter(self._blocked)))
                self._blocked.pop(victim)
            self._blocked[request_id] = {"command": cmd_type, "reader": reader, "blocked_at": now,
                                         "sent_at": now if sent_at is None else sent_at, "timeout_s": timeout,
                                         "probe": probe, "dialog": dialog, "job": job}

    def _request_state(self, request_id: str) -> str | None:
        """The dialog monitor's state for a request (queued, running...), if it tracks it."""
        try:
            requests = self._dialog_control("status").get("requests", [])
        except Exception:
            return None
        return next((r.get("state") for r in requests if r.get("request_id") == request_id), None)

    def _dialog_control(self, action: str) -> dict[str, Any]:
        """Query the dialog monitor over the control channel, keeping this call's metadata.

        A read-only probe: dropped at its 3 s deadline, undiagnosed, since every
        caller treats a failed read as no dialogs. A bridge without the dialog
        monitor is remembered per pipe and not asked again.
        """
        pipe = (self._bound_target or {}).get("target_pipe")
        if pipe and pipe in self._no_dialog_watch:
            return {}
        saved = getattr(self._local, "last_response", None), getattr(self._local, "last_error", None)
        try:
            response = self._send_control_command(json.dumps({"action": action}), "native:max_dialogs", 3.0,
                                                  probe=True, diagnose=False)
            result = json.loads(response.get("result") or "{}")
            return result if isinstance(result, dict) else {}
        except MaxBridgeError as exc:
            pipe = getattr(self._local, "control_pipe", None) or pipe  # the pipe the call was routed to
            if pipe and "unknown command type" in exc.bridge_message.lower():
                self._no_dialog_watch.add(pipe)
            raise
        finally:
            self._local.last_response, self._local.last_error = saved

    def core_control(
        self,
        action: str = "status",
        *,
        wait_ms: int | None = None,
        path: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Report or reload the hot-reloadable native core through the bridge shell.

        Uses the control channel, so it runs beside an in-flight request and never
        falls back to TCP. reload retries CORE_BUSY up to CORE_RELOAD_ATTEMPTS
        times with backoff when wait_ms (default 5000) is at most
        CORE_RETRY_MAX_WAIT_MS (20000); a longer wait gets a single attempt and
        CORE_BUSY is raised directly. Any other error, and a lost response, is
        raised at once. A raised MaxBridgeError records the attempts made in .attempts.
        """
        if action not in {"status", "reload"}:
            raise ValueError("action must be status or reload")
        if self.transport == "tcp":
            raise ValueError("native core control requires the native bridge control channel")
        if action == "status" and (wait_ms is not None or path is not None):
            raise ValueError("wait_ms and path apply to reload only")
        request: dict[str, Any] = {"action": action}
        if wait_ms is not None:
            if isinstance(wait_ms, bool) or not isinstance(wait_ms, int) or not 0 <= wait_ms <= CORE_MAX_WAIT_MS:
                raise ValueError(f"wait_ms must be an integer from 0 to {CORE_MAX_WAIT_MS}")
            request["wait_ms"] = wait_ms
        if path is not None:
            if not isinstance(path, str) or not path.strip():
                raise ValueError("path must be a non-empty core DLL path")
            request["path"] = path
        wait = CORE_DEFAULT_WAIT_MS if wait_ms is None else wait_ms
        if timeout is None:
            timeout = CORE_STATUS_TIMEOUT if action == "status" else wait / 1000.0 + 30.0
        command = json.dumps(request)
        retry = action == "reload" and wait <= CORE_RETRY_MAX_WAIT_MS
        attempts = CORE_RELOAD_ATTEMPTS if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                # status is a read-only probe: dropped at its deadline (MAX_BUSY / MAX_NOT_RESPONDING).
                response = self._send_control_command(command, "native:core", timeout, probe=action == "status")
                break
            except MaxBridgeError as exc:
                exc.attempts = attempt
                if attempt == attempts or _bridge_error_code(exc) != "CORE_BUSY":
                    raise
                time.sleep(CORE_BUSY_BACKOFF * 2 ** (attempt - 1))
        raw = response.get("result")
        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            result = None
        if not isinstance(result, dict):
            raise RuntimeError(f"native:core {action} returned no status object")
        return result

    def _dialogs_blocking(self, request_id: str) -> list[dict[str, Any]]:
        """Main-thread dialogs holding this request: its own, or any while it is queued.

        Nothing while the bridge reports that Max's main thread stopped pumping: it
        is then busy or hung whatever dialog is open (the rule get_bridge_status
        applies), and the deadline diagnosis decides.
        """
        try:
            status = self._dialog_control("status")
            if status.get("main_thread_pumping") is False:
                return []
            listed = [d for d in status.get("dialogs", []) if isinstance(d, dict)]
            # Of any thread: inspect on a bridge without the native exclusion would read them too.
            tool_windows = [d for d in listed if _NOT_DIALOG_TITLE.match(str(d.get("title", "")))]
            dialogs = [d for d in listed if d.get("main_thread") and d not in tool_windows]
            state = next((r.get("state") for r in status.get("requests", [])
                          if r.get("request_id") == request_id), None)
            if state == "running":
                dialogs = [d for d in dialogs if request_id in d.get("during_requests", [])]
            elif state != "queued":
                return []
            if not dialogs:
                return []
            if tool_windows:
                return dialogs  # inspect would read those windows too: report the summaries only
            wanted = {d.get("dialog_id") for d in dialogs}
            try:
                inspected = self._dialog_control("inspect").get("dialogs", [])
            except Exception:
                inspected = []  # An inspect past its deadline still leaves the summaries.
            return [d for d in inspected if d.get("dialog_id") in wanted] or dialogs
        except Exception:
            return []

    def blocked_request_ids(self) -> list[str]:
        """Request ids of BLOCKED_BY_DIALOG calls still waiting in Max. Never consumes results."""
        with self._blocked_lock:
            return [rid for rid, entry in self._blocked.items()
                    if entry.get("dialog", True) and not entry["reader"].done.is_set()]

    def blocked_call(self, request_id: str) -> dict[str, Any] | None:
        """A BLOCKED_BY_DIALOG call still waiting in Max, shaped like inflight(): cmd_type,
        request_id, timeout_s, running_s (since it was sent), waiting_s (since it was released)."""
        with self._blocked_lock:
            entry = self._blocked.get(request_id)
            if entry is None or not entry.get("dialog", True) or entry["reader"].done.is_set():
                return None
            now = time.perf_counter()
            return {"cmd_type": entry["command"], "request_id": request_id, "timeout_s": entry.get("timeout_s"),
                    "running_s": round(now - entry.get("sent_at", entry["blocked_at"]), 1),
                    "waiting_s": round(now - entry["blocked_at"], 1)}

    def wait_job(self, request_id: str, wait: float) -> dict[str, Any]:
        """Wait up to `wait` seconds for a call that raised JobPending.

        Returns {"status": "running"} (with Max's queued/running state) while Max
        works on it, {"status": "blocked_by_dialog", "dialogs": [...]} when a dialog
        holds it, or {"status": "completed", "response": ...} once it finished; the
        job is then forgotten. A bridge error raises as from send_command, a lost
        connection RequestOutcomeUnknown. KeyError: the client does not hold it.
        """
        with self._blocked_lock:
            entry = self._blocked.get(request_id)
        if entry is None or not entry.get("job"):
            raise KeyError(request_id)
        reader = entry["reader"]
        deadline = time.perf_counter() + max(0.0, wait)
        while not reader.done.wait(max(0.0, min(DIALOG_POLL_INTERVAL, deadline - time.perf_counter()))):
            if time.perf_counter() >= deadline:
                break
            dialogs = self._dialogs_blocking(request_id)
            if dialogs and not reader.done.is_set():
                return {"status": "blocked_by_dialog", "dialogs": dialogs,
                        "running_s": round(time.perf_counter() - entry["sent_at"], 1)}
        if not reader.done.is_set():
            return {"status": "running", "state": self._request_state(request_id),
                    "running_s": round(time.perf_counter() - entry["sent_at"], 1)}
        with self._blocked_lock:
            self._blocked.pop(request_id, None)
        try:
            response = self._parse_response(reader.result(), request_id, entry["sent_at"])
        except MaxBridgeError:
            raise
        except Exception as exc:
            raise RequestOutcomeUnknown(f"{exc} {OUTCOME_UNKNOWN_ADVICE}") from exc
        return {"status": "completed", "response": response}

    def blocked_calls(self, wait: float = 0.0) -> list[dict[str, Any]]:
        """Report calls released while still running in Max; finished ones are reported once.

        Those are calls that returned BLOCKED_BY_DIALOG, MAX_NOT_RESPONDING after
        dispatch, or MAX_BUSY at the reply deadline (each error carries the request id).

        status: "waiting" (still in Max), "completed" (ok with its result, or the
        bridge's error), or "lost": the connection broke or the reply was unreadable
        after the call was sent (outcome "unknown", request_sent true), so it may
        have committed; inspect before retrying.
        wait: seconds to wait for the dialog-blocked calls to complete. Waiting
        stops early when one of them is blocked by a dialog again. A call released
        for any other reason may run for minutes; answering a dialog never waits for it.
        """
        deadline = time.perf_counter() + max(0.0, wait)
        while True:
            with self._blocked_lock:
                # A job's result belongs to its tool (wait_job), so it is never consumed here.
                pending = [(rid, entry) for rid, entry in self._blocked.items() if not entry.get("job")]
            # A MAX_BUSY call may run for minutes; answering a dialog does not wait for it.
            waiting = [(rid, entry) for rid, entry in pending
                       if entry.get("dialog", True) and not entry["reader"].done.is_set()]
            if not waiting or time.perf_counter() >= deadline:
                break
            waiting[0][1]["reader"].done.wait(min(0.5, max(0.0, deadline - time.perf_counter())))
            if any(self._dialogs_blocking(rid) for rid, entry in waiting if not entry["reader"].done.is_set()):
                break
        report = []
        for request_id, entry in pending:
            reader = entry["reader"]
            item: dict[str, Any] = {"request_id": request_id, "command": entry["command"]}
            if not reader.done.is_set():
                item.update(status="waiting", waiting_s=round(time.perf_counter() - entry["blocked_at"], 1))
                report.append(item)
                continue
            with self._blocked_lock:
                self._blocked.pop(request_id, None)
            try:
                response = self._parse_response(reader.result(), request_id, entry["blocked_at"])
                raw = response.get("result", "")
                try:
                    value = json.loads(raw) if isinstance(raw, str) and raw[:1] in ("{", "[") else raw
                except ValueError:
                    value = raw
                item.update(status="completed", ok=True, result=value)
            except MaxBridgeError as exc:
                item.update(status="completed", ok=False, error=exc.bridge_message)
            except Exception as exc:
                # The connection broke (Max exited, the bridge dropped the pipe) or the reply
                # was unreadable after the call was sent: whether it ran is unknown.
                item.update(status="lost", ok=False, outcome="unknown", request_sent=True,
                            error=f"{exc} {OUTCOME_UNKNOWN_ADVICE}")
            if _QUEUE_TIMEOUT_MARKER in str(item.get("error", "")).lower():
                item["executed"] = False  # the bridge cancelled it before it started
            report.append(item)
        return report

    # ── TCP transport (legacy) ───────────────────────────────────
    def _send_via_tcp(self, request: str, timeout: float) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        connected = False

        try:
            sock.connect((self.host, self.port))
            connected = True
            sock.sendall((request + "\n").encode("utf-8"))

            response_data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b"\n" in response_data:
                    break

            return response_data

        except socket.timeout:
            if not connected:
                raise MaxTcpConnectTimeoutError(
                    f"TCP connection to 3ds Max on {self.host}:{self.port} timed out within {timeout}s. "
                    "Is the MCP TCP listener running in 3ds Max?"
                )
            raise TimeoutError(
                f"3ds Max did not respond within {timeout}s. "
                "Is the MCP TCP listener running in 3ds Max?"
            )
        except ConnectionRefusedError:
            raise ConnectionRefusedError(
                f"Could not connect to 3ds Max on {self.host}:{self.port}. "
                "Is the MCP TCP listener running in 3ds Max?"
            )
        finally:
            sock.close()

    # ── Response parsing (shared) ────────────────────────────────
    def _parse_response(
        self, response_data: bytes, request_id: str, started_at: float
    ) -> dict[str, Any]:
        # Strip UTF-8 BOM if present
        if response_data.startswith(b'\xef\xbb\xbf'):
            response_data = response_data[3:]
        response_str = response_data.decode("utf-8", errors="replace").strip()

        if not response_str:
            raise RuntimeError("Empty response from 3ds Max")

        response = json.loads(response_str)
        response_request_id = response.get("requestId")
        if response_request_id not in (None, "", request_id):
            raise RuntimeError(
                f"Mismatched response requestId: expected {request_id}, got {response_request_id}"
            )

        response["requestId"] = request_id
        meta = response.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            response["meta"] = meta
        meta.setdefault(
            "clientRoundTripMs",
            round((time.perf_counter() - started_at) * 1000.0, 3),
        )

        if not response.get("success", False):
            error_msg = response.get("error", "Unknown error")
            raise MaxBridgeError(str(error_msg), response)

        return response
