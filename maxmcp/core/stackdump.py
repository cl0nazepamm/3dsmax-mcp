"""Native call stacks of a (hung) x64 process, read from outside with dbghelp.

Nothing is written to the target: each thread is suspended only while its
context is read and its stack walked (milliseconds) and is always resumed;
frames are resolved after it runs again. On Windows 11 / Server 2022 and later
the suspension goes through a thread state-change object, which the kernel
reverts if this process dies, even by TerminateProcess. No debugger and no symbol server:
frames are module+offset, plus the export name inside core OS modules (or
any name a local PDB gives). Needs the same user and integrity level as the
target. Windows only; importing is safe anywhere, and calls report error
entries instead of raising.

Written to investigate 3ds Max hangs: it attributed one to a V-Ray Material
Editor sample-slot render and another to a deadlock with the Cosmos
importer's hidden browser window.

CLI: python -m maxmcp.core.stackdump <pid> [tid ...] [--all] [--depth N] [--json]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Any, Iterable

from . import process_health, suspend_guard

_IS_WINDOWS = sys.platform == "win32"
MAIN_WINDOW_TITLE = "Autodesk 3ds Max"
DEFAULT_DEPTH = 48
MAX_DEPTH = 256

_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_THREAD_SUSPEND_RESUME = 0x0002
_THREAD_GET_CONTEXT = 0x0008
_THREAD_QUERY_INFORMATION = 0x0040
_THREAD_SET_INFORMATION = 0x0020  # NtCreateThreadStateChange needs it on the thread handle
_THREAD_STATE_CHANGE_ALL_ACCESS = 0x001F0001
_THREAD_STATE_CHANGE_SUSPEND, _THREAD_STATE_CHANGE_RESUME = 0, 1
_THREAD_QUERY_LIMITED_INFORMATION = 0x0800
_TH32CS_SNAPTHREAD = 0x00000004
_IMAGE_FILE_MACHINE_AMD64 = 0x8664
_ADDR_MODE_FLAT = 3
_SUSPEND_FAILED = 0xFFFFFFFF
# Longest a thread stays suspended: the walk stops (stack marked truncated) once
# this has elapsed; a single StackWalk64 step cannot be interrupted.
_MAX_SUSPEND_S = 0.25
_ERROR_ACCESS_DENIED = 5
_INVALID_HANDLE = ctypes.c_void_p(-1).value
# x64 CONTEXT: 1232 bytes, 16-byte aligned. Offsets of ContextFlags, Rsp, Rbp, Rip.
_CONTEXT_SIZE = 1232
_CONTEXT_FULL = 0x0010000B
_CTX_FLAGS, _CTX_RSP, _CTX_RBP, _CTX_RIP = 0x30, 0x98, 0xA0, 0xF8
# UNDNAME | IGNORE_CVREC | FAIL_CRITICAL_ERRORS | IGNORE_NT_SYMPATH | NO_PROMPTS, and no
# DEFERRED_LOADS: SymInitialize loads every module up front while all threads run, so
# StackWalk64's callbacks only read cached data while a thread is suspended. Never
# chase a build-machine/UNC PDB path or a symbol server (_NT_SYMBOL_PATH); the search
# path is empty (not the CWD), so only a PDB next to its image is used.
_SYM_OPTIONS = 0x2 | 0x80 | 0x200 | 0x1000 | 0x80000
_SYM_SEARCH_PATH = ""
_SYM_REAL_TYPES = (2, 3, 7)  # SymCv, SymPdb, SymDia: names are exact at any displacement
_EXPORT_MAX_DISPLACEMENT = 0x800  # farther from an export the name is a guess: keep module+offset only
_MAX_SYMBOL_NAME = 511

# Modules that are the OS or the C/C++ runtime: never "the culprit" in a summary.
SYSTEM_MODULE_RE = re.compile(
    r"(ntdll|kernelbase|kernel32|win32u|user32|gdi32|gdi32full|ucrtbase|msvcrt|msvcp\w*|vcruntime\w*|concrt\w*"
    r"|combase|rpcrt4|ole32|oleaut32|sechost|advapi32|ws2_32|mswsock|shell32|shcore|shlwapi|uxtheme|dwmapi"
    r"|imm32|msctf|bcrypt\w*|crypt32|cfgmgr32|setupapi|winmm|version|powrprof|dbghelp|wow64\w*)$",
    re.IGNORECASE)
# Their exports cover the entry points that matter (waits, USER32 calls), so a nearby
# export name is trustworthy there; elsewhere it is a guess.
_EXPORT_NAMED_RE = re.compile(r"(ntdll|kernelbase|kernel32|win32u|user32)$", re.IGNORECASE)
# Every GUI thread owns hidden IME windows; they say nothing about the thread.
_NOISE_WINDOW_CLASSES = frozenset({"IME", "MSCTFIME UI"})
_MESSAGE_WAIT_RE = re.compile(
    r"NtUserGetMessage|NtUserPeekMessage|NtUserMsgWaitForMultipleObjectsEx|NtUserWaitMessage|"
    r"NtUserRealWaitMessageEx|GetMessage|PeekMessage|MsgWaitForMultipleObjects|WaitMessage", re.IGNORECASE)

# Findings, evaluated in order (see summarize). Keys, all optional and all required to hold:
#   thread       "main" (the main thread) or "any" (every thread that matches)
#   cause        explains why the main thread is stuck; "fallback" rules run only if none did
#   state        the thread's state_hint (running, kernel_wait, user_call, message_wait)
#   first_module regex for the first non-system module on the stack (the code that called the OS)
#   below        regex for a module deeper than that frame (who asked for it)
#   near         regex for one of the first `near_depth` (8) non-system modules
#   any_module   regex for any module on the stack
#   owns_hung_window  the thread owns a top-level window that IsHungAppWindow reports hung
#   partner      the same conditions for a second thread (main rules only)
# Regexes match module names case-insensitively, from the start. Message fields: tid, state,
# module, top_modules, partner_tid, partner_module, window, count, tids, windows_note.
RULES: tuple[dict[str, Any], ...] = (
    {"kind": "mcp_shutdown_join", "thread": "main", "cause": True, "state": "kernel_wait",
     "any_module": r"mcp_bridge", "below": r"gup$", "any_symbol": r"Thrd_join|thread::join|_Thrd_join",
     "message": "The main thread is waiting for a bridge-owned thread join during GUP shutdown. "
                "This is plugin teardown; native symbols are needed to identify the joined worker"},
    {"kind": "mcp_teardown_wait", "thread": "main", "cause": True, "state": "kernel_wait",
     "any_module": r"mcp_bridge", "below": r"gup$", "unless_kind": "mcp_shutdown_join",
     "message": "The main thread waits in the MCP bridge with a GUP caller, consistent with plugin teardown. "
                "Without resolved shutdown/join symbols this does not prove the lifecycle phase or joined worker"},
    {"kind": "medit_vray_render", "thread": "main", "cause": True,
     "first_module": r"vray|vrender", "below": r"mtl$",
     "message": "Material Editor sample-slot render (V-Ray) is blocking the main thread"},
    {"kind": "cross_thread_window_deadlock", "thread": "main", "cause": True, "state": "user_call",
     "partner": {"owns_hung_window": True, "state": "kernel_wait"},
     "message": "Cross-thread window deadlock with '{window}' (thread {partner_tid}, module {partner_module}): "
                "the main thread is stuck in a USER32 call while that window's thread waits"},
    {"kind": "maxscript", "thread": "main", "cause": True, "fallback": True, "near": r"maxscrpt$",
     "message": "A long-running MAXScript is executing on the main thread ({state})"},
    {"kind": "main_thread_modules", "thread": "main", "cause": True, "fallback": True,
     "message": "Main thread is in {top_modules} ({state})"},
    {"kind": "cosmos_importer", "thread": "any", "any_module": r"galaxyimporter",
     "message": "Chaos Cosmos importer ({module}) is on the stack of {count} thread(s): {tids}{windows_note}"},
    {"kind": "mcp_bridge_call", "thread": "main", "any_module": r"mcp_bridge",
     "unless_kind": ["mcp_shutdown_join", "mcp_teardown_wait"],
     "message": "The MCP bridge is on the main-thread stack; module presence alone does not establish an active request"},
)
# Default (small) dumps also keep threads that an "any" rule looks for.
_WATCHED_MODULES = tuple(r["any_module"] for r in RULES if r.get("thread") == "any" and r.get("any_module"))


class _THREADENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ThreadID", ctypes.c_uint32),
                ("th32OwnerProcessID", ctypes.c_uint32), ("tpBasePri", ctypes.c_int32),
                ("tpDeltaPri", ctypes.c_int32), ("dwFlags", ctypes.c_uint32)]


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class _ADDRESS64(ctypes.Structure):
    _fields_ = [("Offset", ctypes.c_uint64), ("Segment", ctypes.c_uint16), ("Mode", ctypes.c_uint32)]


class _STACKFRAME64(ctypes.Structure):
    _fields_ = [("AddrPC", _ADDRESS64), ("AddrReturn", _ADDRESS64), ("AddrFrame", _ADDRESS64),
                ("AddrStack", _ADDRESS64), ("AddrBStore", _ADDRESS64), ("FuncTableEntry", ctypes.c_void_p),
                ("Params", ctypes.c_uint64 * 4), ("Far", ctypes.c_int32), ("Virtual", ctypes.c_int32),
                ("Reserved", ctypes.c_uint64 * 3),
                ("KdHelp", ctypes.c_ubyte * 256)]  # KDHELP64 is 112 bytes in current dbghelp; padded


class _SYMBOL_INFOW(ctypes.Structure):
    """Fixed part of SYMBOL_INFOW; SizeOfStruct must be exactly its size (88)."""
    _fields_ = [("SizeOfStruct", ctypes.c_uint32), ("TypeIndex", ctypes.c_uint32), ("Reserved", ctypes.c_uint64 * 2),
                ("Index", ctypes.c_uint32), ("Size", ctypes.c_uint32), ("ModBase", ctypes.c_uint64),
                ("Flags", ctypes.c_uint32), ("Value", ctypes.c_uint64), ("Address", ctypes.c_uint64),
                ("Register", ctypes.c_uint32), ("Scope", ctypes.c_uint32), ("Tag", ctypes.c_uint32),
                ("NameLen", ctypes.c_uint32), ("MaxNameLen", ctypes.c_uint32), ("Name", ctypes.c_uint16 * 1)]


class _IMAGEHLP_MODULEW64(ctypes.Structure):
    _fields_ = [("SizeOfStruct", ctypes.c_uint32), ("BaseOfImage", ctypes.c_uint64), ("ImageSize", ctypes.c_uint32),
                ("TimeDateStamp", ctypes.c_uint32), ("CheckSum", ctypes.c_uint32), ("NumSyms", ctypes.c_uint32),
                ("SymType", ctypes.c_int), ("ModuleName", ctypes.c_wchar * 32), ("ImageName", ctypes.c_wchar * 256),
                ("LoadedImageName", ctypes.c_wchar * 256), ("LoadedPdbName", ctypes.c_wchar * 256),
                ("CVSig", ctypes.c_uint32), ("CVData", ctypes.c_wchar * 780), ("PdbSig", ctypes.c_uint32),
                ("PdbSig70", ctypes.c_byte * 16), ("PdbAge", ctypes.c_uint32), ("PdbUnmatched", ctypes.c_int32),
                ("DbgUnmatched", ctypes.c_int32), ("LineNumbers", ctypes.c_int32), ("GlobalSymbols", ctypes.c_int32),
                ("TypeInfo", ctypes.c_int32), ("SourceIndexed", ctypes.c_int32), ("Publics", ctypes.c_int32),
                ("MachineType", ctypes.c_uint32), ("Reserved", ctypes.c_uint32)]


_capture_lock = threading.Lock()  # dbghelp is single-threaded: one capture at a time
_api: Any = None


class _Api:
    """kernel32/dbghelp entry points with prototypes (loaded on first capture)."""

    def __init__(self) -> None:
        handle, dword, boolean, u64, vp = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint64, ctypes.c_void_p
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        system_dbghelp = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "dbghelp.dll")
        dbg = ctypes.WinDLL(system_dbghelp if os.path.isfile(system_dbghelp) else "dbghelp", use_last_error=True)

        def proto(fn: Any, restype: Any, *argtypes: Any) -> Any:
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        self.OpenProcess = proto(k32.OpenProcess, handle, dword, boolean, dword)
        self.OpenThread = proto(k32.OpenThread, handle, dword, boolean, dword)
        self.CloseHandle = proto(k32.CloseHandle, boolean, handle)
        self.SuspendThread = proto(k32.SuspendThread, dword, handle)
        self.GetProcessIdOfThread = proto(k32.GetProcessIdOfThread, dword, handle)
        self.ResumeThread = proto(k32.ResumeThread, dword, handle)
        self.GetThreadContext = proto(k32.GetThreadContext, boolean, handle, vp)
        self.GetThreadTimes = proto(k32.GetThreadTimes, boolean, handle, *[ctypes.POINTER(_FILETIME)] * 4)
        self.IsWow64Process = proto(k32.IsWow64Process, boolean, handle, ctypes.POINTER(boolean))
        self.QueryFullProcessImageNameW = proto(k32.QueryFullProcessImageNameW, boolean, handle, dword,
                                                ctypes.c_wchar_p, ctypes.POINTER(dword))
        self.CreateToolhelp32Snapshot = proto(k32.CreateToolhelp32Snapshot, handle, dword, dword)
        self.Thread32First = proto(k32.Thread32First, boolean, handle, ctypes.POINTER(_THREADENTRY32))
        self.Thread32Next = proto(k32.Thread32Next, boolean, handle, ctypes.POINTER(_THREADENTRY32))
        self.SymSetOptions = proto(dbg.SymSetOptions, dword, dword)
        self.SymInitializeW = proto(dbg.SymInitializeW, boolean, handle, ctypes.c_wchar_p, boolean)
        self.SymCleanup = proto(dbg.SymCleanup, boolean, handle)
        self.SymFromAddrW = proto(dbg.SymFromAddrW, boolean, handle, u64, ctypes.POINTER(u64), vp)
        self.SymGetModuleInfoW64 = proto(dbg.SymGetModuleInfoW64, boolean, handle, u64,
                                         ctypes.POINTER(_IMAGEHLP_MODULEW64))
        # Thread state-change objects (Windows 11 / Server 2022+): None where ntdll lacks them.
        nt = ctypes.WinDLL("ntdll")
        try:
            self.NtCreateThreadStateChange = proto(nt.NtCreateThreadStateChange, ctypes.c_int32,
                                                   ctypes.POINTER(handle), dword, vp, handle, u64)
            self.NtChangeThreadState = proto(nt.NtChangeThreadState, ctypes.c_int32, handle, handle, dword, vp,
                                             ctypes.c_size_t, u64)
        except AttributeError:
            self.NtCreateThreadStateChange = self.NtChangeThreadState = None
        self.StackWalk64 = proto(dbg.StackWalk64, boolean, dword, handle, handle, ctypes.POINTER(_STACKFRAME64),
                                 vp, vp, vp, vp, vp)
        proto(dbg.SymFunctionTableAccess64, vp, handle, u64)
        proto(dbg.SymGetModuleBase64, u64, handle, u64)
        # dbghelp's own exports serve as StackWalk64's function-table and module-base callbacks.
        self.function_table_access = ctypes.cast(dbg.SymFunctionTableAccess64, vp)
        self.get_module_base = ctypes.cast(dbg.SymGetModuleBase64, vp)


def _load_api() -> Any:
    global _api
    if _api is None:
        _api = _Api()
    return _api


# ── formatting and classification (pure; no OS calls) ─────────────────────

def is_system_module(name: str | None) -> bool:
    return bool(name) and bool(SYSTEM_MODULE_RE.match(name))


def format_frame(frame: dict[str, Any], with_symbol: bool = True) -> str:
    """'module+0xoffset', plus ' (symbol+0xdisp)' when known; '0xaddress' outside any module."""
    module, offset = frame.get("module"), int(frame.get("offset") or 0)
    text = f"{module}+0x{offset:x}" if module else f"0x{offset:x}"
    if with_symbol and frame.get("symbol"):
        text += f" ({frame['symbol']})"
    return text


def _first_non_system(frames: list[dict[str, Any]]) -> int | None:
    return next((i for i, f in enumerate(frames) if not is_system_module(f.get("module"))), None)


def classify_state(frames: list[dict[str, Any]]) -> str:
    """Rough thread state from its top frames: running, kernel_wait, user_call, message_wait, unknown."""
    if not frames:
        return "unknown"
    top = (frames[0].get("module") or "").lower()
    if top in ("win32u", "user32"):
        end = _first_non_system(frames)
        leading = frames[:end] if end is not None else frames
        if any(_MESSAGE_WAIT_RE.search(f.get("symbol") or "") for f in leading):
            return "message_wait"  # an idle message loop, not a blocked call
        return "user_call"  # SendMessage, window destroy/activate, ...
    if top == "ntdll":
        return "kernel_wait"  # syscall stubs live in ntdll: wait, sleep, I/O
    return "running"


def top_modules(thread: dict[str, Any], limit: int = 3) -> list[str]:
    """Distinct non-system modules from the top of the stack down."""
    seen: list[str] = []
    for frame in thread.get("frames") or []:
        module = frame.get("module")
        if module and not is_system_module(module) and module not in seen:
            seen.append(module)
            if len(seen) >= limit:
                break
    return seen


def _preview(thread: dict[str, Any], limit: int) -> list[str]:
    return [format_frame(f) for f in (thread.get("frames") or [])[:limit]]


def _blocked_in(thread: dict[str, Any]) -> str:
    frames = thread.get("frames") or []
    if not frames:
        return thread.get("error") or "stack unavailable"
    state = thread.get("state_hint") or classify_state(frames)
    if state == "running":
        return f"running in {format_frame(frames[0])}"
    first = _first_non_system(frames)
    api = frames[first - 1] if first else frames[0]  # the OS entry point the application called
    label = {"kernel_wait": "kernel wait", "user_call": "USER32 call",
             "message_wait": "message-loop wait"}.get(state, state)
    caller = f", called from {format_frame(frames[first])}" if first is not None else ""
    return f"{label} in {format_frame(api)}{caller}"


def identify_main_thread(windows: Iterable[dict[str, Any]],
                         first_thread: int | None = None) -> tuple[int | None, str | None]:
    """(tid, source) of the process's main UI thread from its top-level windows.

    source: "title" (the visible window titled "...Autodesk 3ds Max..."),
    "largest_window" (thread of the largest visible top-level window) or
    "first_thread" (the oldest thread, when no window is visible).
    """
    visible = [w for w in windows if w.get("visible") and w.get("tid")]
    titled = [w for w in visible if MAIN_WINDOW_TITLE.casefold() in (w.get("title") or "").casefold()]
    for pool, source in ((titled, "title"), (visible, "largest_window")):
        if pool:
            best = max(pool, key=lambda w: (not w.get("owned"), w.get("area") or 0))
            return int(best["tid"]), source
    if first_thread:
        return int(first_thread), "first_thread"
    return None, None


class _Fields(dict):
    def __missing__(self, key: str) -> str:
        return "?"


def _match_thread(cond: dict[str, Any], thread: dict[str, Any]) -> dict[str, Any] | None:
    """Context for message formatting when `thread` meets every condition, else None."""
    frames = thread.get("frames") or []
    modules = [(f.get("module") or "") for f in frames]
    ctx: dict[str, Any] = {}
    if "state" in cond and thread.get("state_hint") != cond["state"]:
        return None
    first = _first_non_system(frames)
    if "first_module" in cond:
        if first is None or not re.match(cond["first_module"], modules[first], re.IGNORECASE):
            return None
        ctx["module"] = modules[first]
    if "below" in cond:
        start = first + 1 if first is not None else 0
        if not any(re.match(cond["below"], m, re.IGNORECASE) for m in modules[start:]):
            return None
    if "near" in cond:
        near = [m for m in modules if m and not is_system_module(m)][:cond.get("near_depth", 8)]
        hit = next((m for m in near if re.match(cond["near"], m, re.IGNORECASE)), None)
        if hit is None:
            return None
        ctx["module"] = hit
    if "any_module" in cond:
        hit = next((m for m in modules if re.match(cond["any_module"], m, re.IGNORECASE)), None)
        if hit is None:
            return None
        ctx.setdefault("module", hit)
    if "any_symbol" in cond and not any(re.search(cond["any_symbol"], f.get("symbol") or "", re.IGNORECASE)
                                         for f in frames):
        return None
    if cond.get("owns_hung_window"):
        hung = [w for w in thread.get("windows") or [] if w.get("hung")]
        if not hung:
            return None
        ctx["window"] = hung[0].get("title") or hung[0].get("class") or "untitled window"
    return ctx


def _window_brief(window: dict[str, Any]) -> str:
    flags = [] if window.get("visible") else ["hidden"]
    if window.get("hung"):
        flags.append("hung")
    title = window.get("title") or window.get("class") or "untitled"
    return f"'{title}'" + (f" ({', '.join(flags)})" if flags else "")


def summarize(capture: dict[str, Any], rules: Iterable[dict[str, Any]] = RULES) -> dict[str, Any]:
    """Turn a capture into {main_thread, findings: [{kind, message, evidence}], hung_windows}."""
    threads = [dict(t, state_hint=t.get("state_hint") or classify_state(t.get("frames") or []))
               for t in capture.get("threads") or [] if isinstance(t, dict)]
    main = next((t for t in threads if t.get("is_main")), None)
    findings: list[dict[str, Any]] = []
    explained = False
    if main is None:
        findings.append({"kind": "main_thread_unknown",
                         "message": "The main thread was not identified (no visible window); read the per-thread stacks",
                         "evidence": {"threads": len(threads)}})
    elif not main.get("frames"):
        findings.append({"kind": "main_thread_unreadable",
                         "message": f"The main thread's stack could not be read: {main.get('error') or 'no frames'}",
                         "evidence": {"tid": main.get("tid")}})
        explained = True

    for rule in rules:
        excluded = rule.get("unless_kind") or []
        if isinstance(excluded, str):
            excluded = [excluded]
        if any(f["kind"] in excluded for f in findings):
            continue
        if rule.get("fallback") and explained:
            continue
        if rule.get("thread", "main") == "any":
            hits = [(t, c) for t in threads if t.get("frames") and (c := _match_thread(rule, t)) is not None]
            if not hits:
                continue
            owned = [(t["tid"], w) for t, _ in hits for w in t.get("windows") or []]
            fields = _Fields(hits[0][1], count=len(hits), tids=", ".join(str(t["tid"]) for t, _ in hits[:8])
                             + (" ..." if len(hits) > 8 else ""))
            fields["windows_note"] = "".join(f"; thread {tid} owns {_window_brief(w)}" for tid, w in owned[:3])
            evidence = {"threads": [{"tid": t["tid"], "frames": _preview(t, 6),
                                     "windows": [_window_brief(w) for w in t.get("windows") or []]}
                                    for t, _ in hits[:5]]}
            findings.append({"kind": rule["kind"], "message": rule["message"].format_map(fields),
                             "evidence": evidence})
            continue
        if main is None or not main.get("frames"):
            continue
        ctx = _match_thread(rule, main)
        if ctx is None:
            continue
        evidence: dict[str, Any] = {"tid": main["tid"], "frames": _preview(main, 8)}
        if rule.get("partner"):
            partner = pctx = None
            for other in threads:
                if other is not main and other.get("frames"):
                    pctx = _match_thread(rule["partner"], other)
                    if pctx is not None:
                        partner = other
                        break
            if partner is None:
                continue
            ctx.update(partner_tid=partner["tid"], window=pctx.get("window", "?"),
                       partner_module=(top_modules(partner, 1) or ["?"])[0])
            evidence["partner"] = {"tid": partner["tid"], "frames": _preview(partner, 8),
                                   "windows": [_window_brief(w) for w in partner.get("windows") or []]}
        fields = _Fields(ctx, tid=main["tid"], state=main.get("state_hint") or "unknown",
                         top_modules=", ".join(top_modules(main, 3)) or "system code only")
        findings.append({"kind": rule["kind"], "message": rule["message"].format_map(fields), "evidence": evidence})
        if rule.get("cause"):
            explained = True

    main_summary = None
    if main is not None:
        main_summary = {"tid": main.get("tid"), "source": capture.get("main_thread_source"),
                        "state": main.get("state_hint"), "top_modules": top_modules(main, 5),
                        "blocked_in": _blocked_in(main), "frames_preview": _preview(main, 12)}
    # A hung thread's windows are all hung, its hidden tooltips and helper windows too:
    # list the visible ones and count the hidden ones per thread.
    hung = [(t, w) for t in threads for w in t.get("windows") or [] if w.get("hung")]
    hung_windows = [{"tid": t.get("tid"), "title": w.get("title"), "class": w.get("class"),
                     "visible": True, "main_thread": bool(t.get("is_main"))} for t, w in hung if w.get("visible")]
    hidden: dict[Any, dict[str, Any]] = {}
    for t, w in hung:
        if not w.get("visible"):
            entry = hidden.setdefault(t.get("tid"), {"tid": t.get("tid"), "main_thread": bool(t.get("is_main")),
                                                     "count": 0})
            entry["count"] += 1
    return {"main_thread": main_summary, "findings": findings, "hung_windows": hung_windows,
            **({"hidden_hung_windows": list(hidden.values())} if hidden else {})}


def format_text(capture: dict[str, Any], summary: dict[str, Any] | None = None) -> str:
    """The original script's per-thread format, with '#' annotation lines."""
    threads = capture.get("threads") or []
    lines = [f"# pid {capture.get('pid')} ({capture.get('image') or '?'}) captured {capture.get('captured_at')}: "
             f"{len(threads)} of {capture.get('thread_count', '?')} threads ({capture.get('mode')}), "
             f"depth {capture.get('depth')}"]
    for thread in threads:
        frames = thread.get("frames") or []
        lines.append(f"=== thread {thread.get('tid')} ({len(frames)} frames) ===")
        notes = [f"main thread ({capture.get('main_thread_source')})"] if thread.get("is_main") else []
        notes.append(str(thread.get("state_hint")))
        if thread.get("suspended_ms") is not None:
            notes.append(f"suspended {thread['suspended_ms']:.2f} ms")
        lines.append("   # " + "; ".join(notes))
        for window in thread.get("windows") or []:
            flags = ["visible" if window.get("visible") else "hidden"] + (["hung"] if window.get("hung") else [])
            lines.append(f"   # window 0x{int(window.get('hwnd') or 0):x} '{window.get('title')}' "
                         f"[{window.get('class')}] {' '.join(flags)}")
        if thread.get("error"):
            lines.append(f"   # error: {thread['error']}")
        lines.extend("   " + format_frame(frame) for frame in frames)
    for error in capture.get("errors") or []:
        where = f"thread {error['tid']} " if error.get("tid") else ""
        lines.append(f"# error: {where}{error.get('message')}")
    if summary:
        lines.append("=== summary ===")
        main = summary.get("main_thread")
        if main:
            lines.append(f"# main thread {main.get('tid')}: {main.get('blocked_in')}")
        lines.extend(f"# [{f.get('kind')}] {f.get('message')}" for f in summary.get("findings") or [])
        for window in summary.get("hung_windows") or []:
            lines.append(f"# hung window: {_window_brief(window)} on thread {window.get('tid')}")
        for entry in summary.get("hidden_hung_windows") or []:
            lines.append(f"# {entry.get('count')} hidden hung windows on thread {entry.get('tid')}")
    return "\n".join(lines) + "\n"


# ── capture (Windows) ──────────────────────────────────────────────────

def _error(stage: str, code: int | None = None, tid: int | None = None, message: str | None = None) -> dict[str, Any]:
    if message is None:
        reason = ctypes.FormatError(code).strip() if code and _IS_WINDOWS else "unknown error"
        message = f"{stage} failed: {reason} (Win32 error {code})"
        if code == _ERROR_ACCESS_DENIED:
            message += "; run the MCP server as the same user and integrity level as the target (elevated Max needs an elevated server)"
    entry: dict[str, Any] = {"stage": stage, "message": message}
    if tid is not None:
        entry["tid"] = tid
    if code is not None:
        entry["win32_error"] = code
    return entry


def _thread_ids(api: Any, pid: int) -> list[int]:
    snap = api.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snap or snap == _INVALID_HANDLE:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    found: list[int] = []
    try:
        entry = _THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = api.Thread32First(snap, ctypes.byref(entry))
        while ok:
            if entry.th32OwnerProcessID == pid:
                found.append(int(entry.th32ThreadID))
            ok = api.Thread32Next(snap, ctypes.byref(entry))
    finally:
        api.CloseHandle(snap)
    return found


def _oldest_thread(api: Any, tids: list[int]) -> int | None:
    best: tuple[int, int] | None = None
    for tid in tids:
        handle = api.OpenThread(_THREAD_QUERY_LIMITED_INFORMATION, False, tid)
        if not handle:
            continue
        try:
            times = [_FILETIME() for _ in range(4)]
            if api.GetThreadTimes(handle, *(ctypes.byref(t) for t in times)):
                created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
                if best is None or created < best[0]:
                    best = (created, tid)
        finally:
            api.CloseHandle(handle)
    return best[1] if best else None


def _image_name(api: Any, hproc: Any) -> str | None:
    size = ctypes.c_uint32(32768)
    buf = ctypes.create_unicode_buffer(size.value)
    if api.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size)):
        return os.path.basename(buf.value)
    return None


class _Pause:
    """How one thread is suspended and resumed.

    Prefers a thread state-change object: the kernel reverts its suspension
    when the handle is closed, including when this process dies by any means.
    Falls back to SuspendThread/ResumeThread where that is unavailable.
    """

    def __init__(self, api: Any, hthread: Any) -> None:
        self.api, self.hthread = api, hthread
        self.handle: Any = None
        self.method = "suspend_thread"
        self.suspend_error: str | None = None
        self.resume_error: str | None = None
        create = getattr(api, "NtCreateThreadStateChange", None)
        if create is not None and getattr(api, "NtChangeThreadState", None) is not None:
            handle = ctypes.c_void_p()
            try:
                status = create(ctypes.byref(handle), _THREAD_STATE_CHANGE_ALL_ACCESS, None, hthread, 0)
            except Exception:
                status = -1
            if status >= 0 and handle.value:
                self.handle, self.method = handle.value, "state_change"

    def suspend(self) -> bool:
        if self.handle is not None:
            status = self.api.NtChangeThreadState(self.handle, self.hthread, _THREAD_STATE_CHANGE_SUSPEND, None, 0, 0)
            if status >= 0:
                return True
            self.suspend_error = f"NtChangeThreadState(suspend) failed: NTSTATUS 0x{status & 0xFFFFFFFF:08X}"
            return False
        if self.api.SuspendThread(self.hthread) != _SUSPEND_FAILED:
            return True
        code = ctypes.get_last_error()
        self.suspend_error = _error("SuspendThread", code)["message"]
        return False

    def resume(self) -> None:
        if self.handle is not None:
            status = self.api.NtChangeThreadState(self.handle, self.hthread, _THREAD_STATE_CHANGE_RESUME, None, 0, 0)
            if status < 0:
                self.resume_error = (f"NtChangeThreadState(resume) failed: NTSTATUS 0x{status & 0xFFFFFFFF:08X}; "
                                     "closing the state-change handle resumes the thread")
        elif self.api.ResumeThread(self.hthread) == _SUSPEND_FAILED:
            code = ctypes.get_last_error()
            self.resume_error = (_error("ResumeThread", code)["message"]
                                 + "; the thread may still be suspended (resume it with Process Explorer)")

    def close(self) -> None:
        if self.handle is not None:
            handle, self.handle = self.handle, None
            self.api.CloseHandle(handle)  # reverts a suspension that is somehow still in effect


def _walk(api: Any, hproc: Any, pid: int, tid: int, depth: int,
          methods: dict[str, int] | None = None) -> tuple[list[int], float | None, dict[str, Any] | None]:
    """Return addresses of one thread's stack: (pcs, suspended_ms, error).

    The thread is suspended only for GetThreadContext and StackWalk64, at most
    about _MAX_SUSPEND_S, and nothing is symbolized or loaded meanwhile.
    Suspension goes through suspend_guard, which resumes it exactly once and
    keeps this process from exiting (watchdog, stdin EOF, atexit) while it is
    suspended; `methods` counts how each thread was paused.
    """
    access = _THREAD_GET_CONTEXT | _THREAD_SUSPEND_RESUME | _THREAD_QUERY_INFORMATION
    hthread = api.OpenThread(access | _THREAD_SET_INFORMATION, False, tid) or api.OpenThread(access, False, tid)
    if not hthread:
        return [], None, _error("OpenThread", ctypes.get_last_error(), tid)
    pcs: list[int] = []
    error: dict[str, Any] | None = None
    suspended_ms: float | None = None
    pause: _Pause | None = None
    try:
        # The TID came from an earlier snapshot: if that thread has exited, the ID can
        # already name a thread of another process (or of this one). Never suspend it.
        owner = api.GetProcessIdOfThread(hthread)
        if owner != pid or owner == os.getpid():
            return [], None, _error("OpenThread", tid=tid,
                                    message=f"thread {tid} no longer belongs to PID {pid} (it exited)")
        raw = ctypes.create_string_buffer(_CONTEXT_SIZE + 16)
        ctx = ctypes.addressof(raw) + (-ctypes.addressof(raw)) % 16
        ctypes.c_uint32.from_address(ctx + _CTX_FLAGS).value = _CONTEXT_FULL  # set before suspending
        pause = _Pause(api, hthread)
        if methods is not None:
            methods[pause.method] = methods.get(pause.method, 0) + 1
        started = time.perf_counter()
        try:
            with suspend_guard.suspended(pause.suspend, pause.resume) as ok:
                if not ok:
                    error = _error("SuspendThread", tid=tid, message=pause.suspend_error or "SuspendThread failed")
                elif not api.GetThreadContext(hthread, ctx):
                    error = _error("GetThreadContext", ctypes.get_last_error(), tid)
                else:
                    rip = ctypes.c_uint64.from_address(ctx + _CTX_RIP).value
                    frame = _STACKFRAME64()
                    frame.AddrPC.Offset, frame.AddrPC.Mode = rip, _ADDR_MODE_FLAT
                    frame.AddrFrame.Offset = ctypes.c_uint64.from_address(ctx + _CTX_RBP).value
                    frame.AddrFrame.Mode = _ADDR_MODE_FLAT
                    frame.AddrStack.Offset = ctypes.c_uint64.from_address(ctx + _CTX_RSP).value
                    frame.AddrStack.Mode = _ADDR_MODE_FLAT
                    last = None
                    for _ in range(depth):
                        if time.perf_counter() - started > _MAX_SUSPEND_S:
                            error = _error("StackWalk64", tid=tid,
                                           message=f"stack walk stopped after {_MAX_SUSPEND_S * 1000:.0f} ms "
                                                   "to keep the thread's pause short (stack truncated)")
                            break
                        if not api.StackWalk64(_IMAGE_FILE_MACHINE_AMD64, hproc, hthread, ctypes.byref(frame), ctx,
                                               None, api.function_table_access, api.get_module_base, None):
                            break
                        pc, sp = frame.AddrPC.Offset, frame.AddrStack.Offset
                        if not pc or (pc, sp) == last:
                            break
                        last = (pc, sp)
                        pcs.append(pc)
                    if not pcs and rip:
                        pcs.append(rip)  # the walk failed: keep at least the current instruction
            if ok:
                suspended_ms = round((time.perf_counter() - started) * 1000.0, 3)
        except suspend_guard.ExitingError as exc:
            error = _error("SuspendThread", tid=tid, message=str(exc))
        if pause.resume_error:
            error = _error("ResumeThread", tid=tid, message=pause.resume_error)
    finally:
        if pause is not None:
            pause.close()
        api.CloseHandle(hthread)
    return pcs, suspended_ms, error


def _describe(api: Any, hproc: Any, pc: int, cache: dict[int, dict[str, Any]]) -> dict[str, Any]:
    cached = cache.get(pc)
    if cached is None:
        cached = {"module": None, "offset": pc}
        size = ctypes.sizeof(_SYMBOL_INFOW)
        buf = ctypes.create_string_buffer(size + 2 * (_MAX_SYMBOL_NAME + 1))
        info = _SYMBOL_INFOW.from_buffer(buf)
        info.SizeOfStruct, info.MaxNameLen = size, _MAX_SYMBOL_NAME
        displacement = ctypes.c_uint64(0)
        symbol = None
        if api.SymFromAddrW(hproc, pc, ctypes.byref(displacement), buf):
            name = ctypes.wstring_at(ctypes.addressof(buf) + _SYMBOL_INFOW.Name.offset,
                                     min(info.NameLen, _MAX_SYMBOL_NAME))
            symbol = (name, displacement.value)
        module = _IMAGEHLP_MODULEW64()
        module.SizeOfStruct = ctypes.sizeof(module)
        if api.SymGetModuleInfoW64(hproc, pc, ctypes.byref(module)) and module.BaseOfImage:
            name = module.ModuleName
            if len(name) >= 31 and module.ImageName:  # ModuleName is truncated to 31 characters
                name = os.path.splitext(os.path.basename(module.ImageName))[0]
            cached = {"module": name, "offset": pc - module.BaseOfImage}
            # Without a PDB the name is the nearest export: right for the OS entry points
            # (waits, USER32 calls), a misleading guess inside third-party code.
            exact = module.SymType in _SYM_REAL_TYPES
            if symbol and symbol[0] and (exact or (_EXPORT_NAMED_RE.match(name)
                                                   and symbol[1] < _EXPORT_MAX_DISPLACEMENT)):
                cached["symbol"] = f"{symbol[0]}+0x{symbol[1]:x}"
        cache[pc] = cached
    return dict(cached)


def _keep_by_default(thread: dict[str, Any], main_tid: int | None) -> bool:
    """Small dump: main thread, window owners, threads running app code, watched modules."""
    frames = thread.get("frames") or []
    if thread["tid"] == main_tid or thread.get("windows"):
        return True
    if frames and not is_system_module(frames[0].get("module")):
        return True
    return any(re.match(p, f.get("module") or "", re.IGNORECASE) for p in _WATCHED_MODULES for f in frames)


def capture_stacks(pid: int, tids: Iterable[int] | None = None, all_threads: bool = True,
                   depth: int = DEFAULT_DEPTH) -> dict[str, Any]:
    """Native stacks of `pid`'s threads; never raises.

    tids: only these threads. Otherwise all_threads=True keeps every thread;
    False keeps the main thread, threads owning a top-level window, threads
    whose top frame is application code, and threads in watched modules.
    Returns {pid, image, captured_at, mode, depth, thread_count, main_thread,
    main_thread_source, threads: [{tid, is_main, state_hint, frames:
    [{module, offset, symbol?}], windows, suspended_ms, error?}], omitted, errors,
    pause_methods: {state_change|suspend_thread: count}}.

    The capture runs on its own (non-daemon) thread: signals such as Ctrl+C are
    delivered to the main thread only, so a KeyboardInterrupt can never land
    between suspending a thread and registering its resume. If the caller is
    interrupted, the capture still finishes, and resumes every thread, in the background.
    """
    try:
        depth = max(1, min(int(depth), MAX_DEPTH))
    except (TypeError, ValueError):
        depth = DEFAULT_DEPTH
    bad_tids = False
    try:
        wanted = None if tids is None else [int(t) for t in tids]
    except (TypeError, ValueError):
        wanted, bad_tids = None, True
    result: dict[str, Any] = {
        "pid": pid, "image": None, "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "tids" if wanted else ("all" if all_threads else "selected"), "depth": depth,
        "thread_count": 0, "main_thread": None, "main_thread_source": None, "threads": [], "omitted": 0,
        "errors": [], "elapsed_ms": None, "pause_methods": {},
    }
    errors = result["errors"]
    if bad_tids:
        errors.append(_error("args", message="tids must be thread IDs"))
        return result
    if not _IS_WINDOWS:
        errors.append(_error("platform", message="native stack capture needs Windows"))
        return result
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        errors.append(_error("platform", message="native stack capture needs 64-bit Python"))
        return result
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 0xFFFFFFFF:
        errors.append(_error("args", message=f"invalid pid {pid!r}"))
        return result
    if pid == os.getpid():
        errors.append(_error("args", message="refusing to suspend the threads of the calling process"))
        return result
    started = time.perf_counter()
    try:
        api = _load_api()
    except (OSError, AttributeError) as exc:
        errors.append(_error("load", message=f"could not load kernel32/dbghelp: {exc}"))
        return result

    def run() -> None:
        with _capture_lock:
            try:
                _capture(api, pid, wanted, all_threads, depth, result)
            except Exception as exc:  # a diagnosis must never take the caller down
                errors.append(_error("internal", message=f"{type(exc).__name__}: {exc}"))

    worker = threading.Thread(target=run, name="maxmcp-stackdump")
    worker.start()
    worker.join()
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    return result


def _capture(api: Any, pid: int, wanted: list[int] | None, all_threads: bool, depth: int,
             result: dict[str, Any]) -> None:
    errors = result["errors"]
    hproc = api.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not hproc:
        errors.append(_error("OpenProcess", ctypes.get_last_error()))
        return
    try:
        wow64 = ctypes.c_int32(0)
        if api.IsWow64Process(hproc, ctypes.byref(wow64)) and wow64.value:
            errors.append(_error("platform", message="32-bit (WOW64) targets are not supported"))
            return
        result["image"] = _image_name(api, hproc)
        try:
            all_tids = _thread_ids(api, pid)
        except OSError as exc:
            errors.append(_error("CreateToolhelp32Snapshot", exc.errno))
            return
        result["thread_count"] = len(all_tids)
        windows = [w for w in process_health.list_windows(pid) if w.get("class") not in _NOISE_WINDOW_CLASSES]
        owned: dict[int, list[dict[str, Any]]] = {}
        for window in windows:
            owned.setdefault(window["tid"], []).append(
                {k: window.get(k) for k in ("hwnd", "title", "class", "visible", "owned", "hung")})
        has_visible = any(w.get("visible") for w in windows)
        main_tid, source = identify_main_thread(windows, None if has_visible else _oldest_thread(api, all_tids))
        result["main_thread"], result["main_thread_source"] = main_tid, source

        if wanted:
            known = set(all_tids)
            for tid in wanted:
                if tid not in known:
                    errors.append(_error("args", tid=tid, message=f"thread {tid} does not belong to PID {pid}"))
            targets = [tid for tid in dict.fromkeys(wanted) if tid in known]
        else:
            # Main thread first (earliest snapshot), then window owners, then the rest.
            targets = sorted(all_tids, key=lambda t: (t != main_tid, t not in owned))

        api.SymSetOptions(_SYM_OPTIONS)
        if not api.SymInitializeW(hproc, _SYM_SEARCH_PATH, True):
            errors.append(_error("SymInitialize", ctypes.get_last_error()))
            return
        try:
            walked = [(tid, *_walk(api, hproc, pid, tid, depth, result["pause_methods"])) for tid in targets]
            cache: dict[int, dict[str, Any]] = {}
            for tid, pcs, suspended_ms, error in walked:  # every thread runs again before symbolizing
                frames = [_describe(api, hproc, pc, cache) for pc in pcs]
                thread: dict[str, Any] = {"tid": tid, "is_main": tid == main_tid, "state_hint": classify_state(frames),
                                          "frames": frames, "windows": owned.get(tid, []),
                                          "suspended_ms": suspended_ms}
                if error:
                    thread["error"] = error["message"]
                    errors.append(error)
                if wanted or all_threads or _keep_by_default(thread, main_tid):
                    result["threads"].append(thread)
                else:
                    result["omitted"] += 1
        finally:
            api.SymCleanup(hproc)
    finally:
        api.CloseHandle(hproc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m maxmcp.core.stackdump",
        description="Print native stacks of a (hung) process's threads. Each thread is paused for milliseconds.")
    parser.add_argument("pid", type=int)
    parser.add_argument("tids", type=int, nargs="*", help="only these threads")
    parser.add_argument("--all", action="store_true",
                        help="every thread (default: main thread, window owners, threads in app code or watched modules)")
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH, help=f"frames per thread (default {DEFAULT_DEPTH})")
    parser.add_argument("--json", action="store_true", help="print {capture, summary} as JSON")
    args = parser.parse_intermixed_args(argv)
    capture = capture_stacks(args.pid, tids=args.tids or None, all_threads=args.all, depth=args.depth)
    summary = summarize(capture)
    output = json.dumps({"capture": capture, "summary": summary}, indent=2) + "\n" if args.json \
        else format_text(capture, summary)
    try:
        sys.stdout.reconfigure(errors="replace")  # window titles can hold any script
    except (AttributeError, ValueError):
        pass
    sys.stdout.write(output)
    return 0 if capture["threads"] else 1


if __name__ == "__main__":
    sys.exit(main())
