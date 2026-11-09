"""Read-only health probes for a 3ds Max process (hung window, CPU, exit).

Only query rights are requested; nothing here signals, injects into or sends
a message to the target. Every public function returns a verdict instead of
raising.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Any

BLOCKED_CPU_THRESHOLD = 0.05  # CPU-seconds per wall second

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_STILL_ACTIVE = 259
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_ERROR_INVALID_PARAMETER = 87
_GW_OWNER = 4
_TH32CS_SNAPPROCESS = 0x2

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.IsHungAppWindow.restype = wintypes.BOOL
    _user32.IsHungAppWindow.argtypes = [wintypes.HWND]
    # Never sends WM_GETTEXT (GetWindowTextW would, to a possibly hung thread).
    _user32.InternalGetWindowText.restype = ctypes.c_int
    _user32.InternalGetWindowText.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    # Neither sends a message to the window's thread.
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.Process32FirstW.restype = wintypes.BOOL
    _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _kernel32.Process32NextW.restype = wintypes.BOOL
    _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]

def _valid_pid(pid: Any) -> bool:
    return isinstance(pid, int) and not isinstance(pid, bool) and 0 < pid <= 0xFFFFFFFF


def _open_process(pid: int) -> tuple[Any, int]:
    """Return (handle or None, last error). Query rights only."""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    if not handle:
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    return (handle or None), (0 if handle else ctypes.get_last_error())


def _exit_status(handle: Any) -> tuple[bool | None, int | None]:
    """Return (alive, exit_code) for an open process handle."""
    code = wintypes.DWORD()
    if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return None, None
    if code.value != _STILL_ACTIVE:
        return False, int(code.value)
    # STILL_ACTIVE can be a real exit code; SYNCHRONIZE (if granted) settles it.
    if _kernel32.WaitForSingleObject(handle, 0) == _WAIT_OBJECT_0:
        return False, int(code.value)
    return True, None


def _cpu_seconds(handle: Any) -> float | None:
    times = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        return None
    kernel, user = times[2], times[3]
    total = 0
    for ft in (kernel, user):
        total += (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    return total / 1e7


def process_start_time(pid: int) -> int | None:
    """Creation FILETIME of a live process (guards verdicts against PID reuse); None if unknown/gone."""
    if not _IS_WINDOWS or not _valid_pid(pid):
        return None
    handle = None
    try:
        handle, _err = _open_process(pid)
        if handle is None or _exit_status(handle)[0] is False:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    except Exception:
        return None
    finally:
        if handle is not None:
            try:
                _kernel32.CloseHandle(handle)
            except Exception:
                pass


def process_gone(pid: int, started: int | None = None) -> bool:
    """True only when `pid` has certainly exited, or now names a process created after `started`.

    False when it is alive (busy or hung included) or when that can't be told (e.g. access denied).
    """
    if not _IS_WINDOWS or not _valid_pid(pid):
        return False
    handle = None
    try:
        handle, err = _open_process(pid)
        if handle is None:
            return err == _ERROR_INVALID_PARAMETER  # no such process
        alive, _code = _exit_status(handle)
        if alive is False:
            return True
        if alive is None or started is None:
            return False
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return False
        return ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) != started
    except Exception:
        return False
    finally:
        if handle is not None:
            try:
                _kernel32.CloseHandle(handle)
            except Exception:
                pass


def _top_level_windows(pid: int) -> list[int]:
    """Visible, unowned top-level windows of a process."""
    found: list[int] = []

    def _callback(hwnd: Any, _lparam: Any) -> bool:
        try:
            owner = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if (owner.value == pid and _user32.IsWindowVisible(hwnd)
                    and not _user32.GetWindow(hwnd, _GW_OWNER)):
                found.append(hwnd)
        except Exception:
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(_callback), 0)
    return found


def _main_thread_windows(pid: int) -> list[int]:
    """Visible, unowned top-level windows on the thread of Max's main window.

    That thread owns the window titled "... 3ds Max ..."; when no title matches,
    every visible unowned window counts. A modeless window on another thread,
    such as a Cosmos browser stuck on its importer thread, never makes Max look hung.
    """
    windows = _top_level_windows(pid)
    named = [h for h in windows if "3ds max" in _window_title(h).casefold()]
    if not named:
        return windows
    threads = {_user32.GetWindowThreadProcessId(h, None) for h in named}
    return [h for h in windows if _user32.GetWindowThreadProcessId(h, None) in threads]


def _windows_hung(pid: int) -> tuple[bool, bool | None]:
    """Return (main_window_found, any_main_thread_window_hung)."""
    windows = _main_thread_windows(pid)
    if not windows:
        return False, None
    return True, any(bool(_user32.IsHungAppWindow(hwnd)) for hwnd in windows)


def quick_hung_check(pid: int) -> bool | None:
    """Cheap IsHungAppWindow check; None when unknown (no window, not Windows, error)."""
    if not _IS_WINDOWS or not _valid_pid(pid):
        return None
    try:
        return _windows_hung(pid)[1]
    except Exception:
        return None


def diagnose_process(pid: int, cpu_sample_s: float = 1.0) -> dict[str, Any]:
    """Classify a process as exited / blocked / busy / responsive / unknown.

    blocked: main window hung and CPU below BLOCKED_CPU_THRESHOLD (deadlock,
    stalled I/O, or a long blocking call). busy: hung but burning CPU. Never raises.
    """
    result: dict[str, Any] = {
        "pid": pid,
        "alive": True,
        "exit_code": None,
        "main_window_found": False,
        "window_hung": None,
        "cpu_seconds_per_second": None,
        "cpu_sample_s": cpu_sample_s,
        "state": "unknown",
    }
    if not _IS_WINDOWS or not _valid_pid(pid):
        if not _valid_pid(pid):
            result["alive"] = False
            result["error"] = "invalid pid"
        return result
    handle = None
    try:
        handle, err = _open_process(pid)
        if handle is None:
            if err == _ERROR_INVALID_PARAMETER:
                result["alive"] = False
                result["state"] = "exited"
            else:
                result["error"] = f"OpenProcess failed: Win32 error {err}"
            return result
        alive, exit_code = _exit_status(handle)
        if alive is False:
            result.update(alive=False, exit_code=exit_code, state="exited")
            return result
        found, hung = _windows_hung(pid)
        result["main_window_found"] = found
        result["window_hung"] = hung
        cpu_before = _cpu_seconds(handle)
        started = time.perf_counter()
        time.sleep(max(0.0, cpu_sample_s))
        cpu_after = _cpu_seconds(handle)
        elapsed = time.perf_counter() - started
        if cpu_before is not None and cpu_after is not None and elapsed > 0:
            result["cpu_seconds_per_second"] = round(max(0.0, cpu_after - cpu_before) / elapsed, 4)
        alive, exit_code = _exit_status(handle)
        if alive is False:
            result.update(alive=False, exit_code=exit_code, state="exited")
            return result
        if hung is None:
            result["state"] = "unknown"
        elif not hung:
            result["state"] = "responsive"
        elif result["cpu_seconds_per_second"] is None:
            result["state"] = "unknown"
        elif result["cpu_seconds_per_second"] < BLOCKED_CPU_THRESHOLD:
            result["state"] = "blocked"
        else:
            result["state"] = "busy"
        return result
    except Exception as exc:  # diagnosis must never take the caller down
        result["state"] = "unknown"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if handle is not None:
            try:
                _kernel32.CloseHandle(handle)
            except Exception:
                pass


def _window_title(hwnd: Any) -> str:
    buf = ctypes.create_unicode_buffer(512)
    _user32.InternalGetWindowText(hwnd, buf, 512)
    return buf.value


def list_windows(pid: int) -> list[dict[str, Any]]:
    """Every top-level window of `pid` (hidden ones too) with the thread that owns it.

    Sends no message, so it is safe against a hung process: title via
    InternalGetWindowText, class via GetClassNameW, hung via IsHungAppWindow.
    Each entry: hwnd, tid, title, class, visible, owned, hung, area. [] on error.
    """
    if not _IS_WINDOWS or not _valid_pid(pid):
        return []
    found: list[dict[str, Any]] = []

    def _callback(hwnd: Any, _lparam: Any) -> bool:
        try:
            owner = wintypes.DWORD()
            tid = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != pid:
                return True
            cls = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls, 256)
            rect = wintypes.RECT()
            area = 0
            if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            found.append({"hwnd": int(hwnd), "tid": int(tid), "title": _window_title(hwnd), "class": cls.value,
                          "visible": bool(_user32.IsWindowVisible(hwnd)),
                          "owned": bool(_user32.GetWindow(hwnd, _GW_OWNER)),
                          "hung": bool(_user32.IsHungAppWindow(hwnd)), "area": area})
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(_callback), 0)
    except Exception:
        return []
    return found


def list_processes(image_names: tuple[str, ...] = ("3dsmax.exe",)) -> list[dict[str, Any]]:
    """Running processes whose image file name is one of `image_names` (case-insensitive).

    Toolhelp snapshot only; nothing is opened or signalled. [] on error.
    """
    if not _IS_WINDOWS:
        return []
    wanted = {name.casefold() for name in image_names}
    invalid = wintypes.HANDLE(-1).value
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == invalid:
        return []
    found: list[dict[str, Any]] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.casefold() in wanted:
                found.append({"pid": int(entry.th32ProcessID), "image": entry.szExeFile})
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
    except Exception:
        return []
    finally:
        _kernel32.CloseHandle(snap)
    return sorted(found, key=lambda item: item["pid"])


def describe(diagnosis: dict[str, Any] | None) -> str:
    """Short evidence string, e.g. 'main window hung, 0.01 CPU-s/s over 1.0 s'."""
    if not diagnosis:
        return "process state unknown"
    if diagnosis.get("state") == "exited":
        code = diagnosis.get("exit_code")
        return "process exited" + (f" (exit code {code})" if code is not None else "")
    parts = []
    hung = diagnosis.get("window_hung")
    if hung is None:
        parts.append("main window not found" if not diagnosis.get("main_window_found") else "window state unknown")
    else:
        parts.append("main window hung" if hung else "main window responding")
    cpu = diagnosis.get("cpu_seconds_per_second")
    if cpu is not None:
        parts.append(f"{cpu:.2f} CPU-s/s over {diagnosis.get('cpu_sample_s', 0):.1f} s")
    return ", ".join(parts)
