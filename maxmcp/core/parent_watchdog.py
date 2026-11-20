"""Exit the MCP server when its client (parent) process goes away.

On Windows, child processes survive their parent, so a crashed or killed MCP
client leaves orphaned stdio servers holding pipe connections to 3ds Max. The
watchdog waits on the parent process handle and exits this process when the
parent exits. When the parent is a pass-through launcher (venv python.exe stub,
py.exe, uv, the 3dsmax-mcp.exe console script, cmd.exe) that would outlive a
crashed client, the launcher's ancestors are watched too. It never touches any
other process.
"""

from __future__ import annotations

import os
import sys
import threading

_ENV_VAR = "MAXMCP_PARENT_WATCHDOG"
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0x0
_TH32CS_SNAPPROCESS = 0x2
_EXIT_GRACE_SECONDS = 2.0
_MAX_LAUNCHER_DEPTH = 4
_LAUNCHER_NAMES = frozenset({"uv.exe", "uvx.exe", "py.exe", "pyw.exe", "3dsmax-mcp.exe", "cmd.exe"})

_kernel32 = None


def _log(message: str) -> None:
    stream = sys.stderr
    if stream is None:
        return
    try:
        stream.write(f"[maxmcp] parent watchdog: {message}\n")
        stream.flush()
    except Exception:
        pass


def _load_kernel32():
    global _kernel32
    if _kernel32 is not None:
        return _kernel32
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
    k32.WaitForMultipleObjects.restype = wintypes.DWORD
    k32.GetCurrentProcess.argtypes = []
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    lpft = ctypes.POINTER(wintypes.FILETIME)
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE, lpft, lpft, lpft, lpft]
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_process_entry_type())]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_process_entry_type())]
    k32.Process32NextW.restype = wintypes.BOOL
    _kernel32 = k32
    return k32


_PROCESSENTRY32W = None


def _process_entry_type():
    global _PROCESSENTRY32W
    if _PROCESSENTRY32W is None:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        _PROCESSENTRY32W = PROCESSENTRY32W
    return _PROCESSENTRY32W


def _creation_time(k32, handle) -> int | None:
    import ctypes
    from ctypes import wintypes

    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    ok = k32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    )
    if not ok:
        return None
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def _image_path(k32, handle) -> str | None:
    import ctypes
    from ctypes import wintypes

    size = wintypes.DWORD(32768)
    buf = ctypes.create_unicode_buffer(size.value)
    if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return None
    return buf.value


def _parent_pids(k32) -> dict[int, int]:
    import ctypes

    invalid = ctypes.c_void_p(-1).value
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == invalid:
        return {}
    try:
        entry = _process_entry_type()()
        entry.dwSize = ctypes.sizeof(entry)
        parents: dict[int, int] = {}
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return parents
    finally:
        k32.CloseHandle(snap)


def _is_launcher(image: str | None) -> bool:
    """True when the image is a pass-through launcher that waits on its child."""
    if not image:
        return False
    if os.path.basename(image).lower() in _LAUNCHER_NAMES:
        return True
    # A venv's Scripts/python.exe is a redirector stub that spawns the base
    # interpreter; inside the child, sys.executable still names the stub.
    if sys.prefix != sys.base_prefix and sys.executable:
        return os.path.normcase(os.path.abspath(image)) == os.path.normcase(os.path.abspath(sys.executable))
    return False


def _open_older_process(k32, pid: int, younger_than: int) -> tuple[object, int] | None:
    """Open ``pid`` if it is alive and was created before ``younger_than``."""
    handle = k32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    created = None
    if k32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0:
        created = _creation_time(k32, handle)
    if created is None or created > younger_than:
        k32.CloseHandle(handle)
        return None
    return handle, created


def _add_launcher_ancestors(k32, watched: list[tuple[int, object]], created: int) -> None:
    """Append the ancestors of a launcher parent, up to the first non-launcher."""
    pid, handle = watched[-1]
    parents = None
    for _ in range(_MAX_LAUNCHER_DEPTH):
        if not _is_launcher(_image_path(k32, handle)):
            break
        if parents is None:
            parents = _parent_pids(k32)
        next_pid = parents.get(pid)
        if not next_pid:
            break
        opened = _open_older_process(k32, next_pid, created)
        if opened is None:
            break
        handle, created = opened
        pid = next_pid
        watched.append((pid, handle))


def _exit_process() -> None:
    # A Max thread suspended by capture_hang_diagnostics must never outlive us,
    # but nothing (an import error, a stuck release) may keep us from exiting.
    try:
        from .suspend_guard import release_all

        release_all()
    finally:
        os._exit(0)


def _shutdown(pid: int) -> None:
    # Logging can block on a full stderr pipe whose reader is gone; the timer
    # guarantees the exit anyway. stdout is not flushed: its reader (the client)
    # is gone, and another thread blocked in a stdout write would hold its lock.
    fallback = threading.Timer(_EXIT_GRACE_SECONDS, _exit_process)
    fallback.daemon = True
    fallback.start()
    _log(f"parent process {pid} exited; shutting down")
    _exit_process()


def _watch(k32, watched: list[tuple[int, object]]) -> None:
    try:
        import ctypes
        from ctypes import wintypes

        handles = (wintypes.HANDLE * len(watched))(*(h for _, h in watched))
        result = k32.WaitForMultipleObjects(len(watched), handles, False, _INFINITE)
        if _WAIT_OBJECT_0 <= result < _WAIT_OBJECT_0 + len(watched):
            _shutdown(watched[result - _WAIT_OBJECT_0][0])
        else:
            pids = [pid for pid, _ in watched]
            _log(f"wait on parents {pids} failed (result={result}, error={ctypes.get_last_error()}); watchdog stopped")
    except Exception as exc:
        _log(f"watchdog stopped: {exc}")
    finally:
        for _, handle in watched:
            try:
                k32.CloseHandle(handle)
            except Exception:
                pass


def start_parent_watchdog(parent_pid: int | None = None) -> threading.Thread | None:
    """Start a daemon thread that exits this process when the parent exits.

    Returns the thread, or None when the watchdog is disabled, unsupported, or
    the parent cannot be verified. Never raises.
    """
    watched: list[tuple[int, object]] = []
    k32 = None
    try:
        if os.environ.get(_ENV_VAR, "").strip() == "0":
            return None
        if sys.platform != "win32":
            return None
        if parent_pid is None:
            parent_pid = os.getppid()
        parent_pid = int(parent_pid)
        if parent_pid <= 0 or parent_pid > 0xFFFFFFFF:
            _log(f"invalid parent pid {parent_pid}; not started")
            return None

        k32 = _load_kernel32()
        handle = k32.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, parent_pid)
        if not handle:
            import ctypes
            _log(f"cannot open parent {parent_pid} (error={ctypes.get_last_error()}); not started")
            return None
        watched.append((parent_pid, handle))

        if k32.WaitForSingleObject(handle, 0) == _WAIT_OBJECT_0:
            _log(f"parent {parent_pid} already exited at startup; not started")
            return None

        parent_created = _creation_time(k32, handle)
        own_created = _creation_time(k32, k32.GetCurrentProcess())
        if parent_created is None or own_created is None:
            _log(f"cannot read process times for parent {parent_pid}; not started")
            return None
        if parent_created > own_created:
            _log(f"pid {parent_pid} is younger than this process (pid reused); not started")
            return None

        try:
            _add_launcher_ancestors(k32, watched, parent_created)
        except Exception as exc:
            _log(f"launcher ancestry not resolved: {exc}")

        thread = threading.Thread(
            target=_watch,
            args=(k32, list(watched)),
            name="maxmcp-parent-watchdog",
            daemon=True,
        )
        thread.start()
        watched = []  # owned by the thread now
        return thread
    except Exception as exc:
        _log(f"not started: {exc}")
        return None
    finally:
        if k32 is not None:
            for _, handle in watched:
                try:
                    k32.CloseHandle(handle)
                except Exception:
                    pass
