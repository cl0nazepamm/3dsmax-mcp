"""The bridge's in-process entry point, reached with ctypes from Python inside 3ds Max.

The mcp_bridge .gup exports mcp_bridge_call(request, length, &responseLength): the same
request JSON a named-pipe client sends, routed through the same dispatch (reload gate,
safe mode, main-thread rules). No compiled Python extension is involved.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any

_EXPORT = b"mcp_bridge_call"
_Proto = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
_function: Any = None


def _loaded_modules() -> list[int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
    process = kernel32.GetCurrentProcess()
    count = 2048
    while True:
        modules = (wintypes.HMODULE * count)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModules(process, modules, ctypes.sizeof(modules), ctypes.byref(needed)):
            return []
        found = needed.value // ctypes.sizeof(wintypes.HMODULE)
        if found <= count:
            return [handle for handle in modules[:found] if handle]
        count = found + 64


def _bridge_module() -> tuple[str, int] | None:
    """(file path, entry point address) of the loaded mcp_bridge*.gup that exports the entry point."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]
    kernel32.GetProcAddress.restype = ctypes.c_void_p
    path = ctypes.create_unicode_buffer(32768)
    for handle in _loaded_modules():
        if not kernel32.GetModuleFileNameW(handle, path, len(path)):
            continue
        name = os.path.basename(path.value).lower()
        if name.startswith("mcp_bridge") and name.endswith(".gup"):
            address = kernel32.GetProcAddress(handle, _EXPORT)
            if address:
                return path.value, address
    return None


def _locate() -> Any:
    found = _bridge_module()
    return _Proto(found[1]) if found else None


def bridge_path() -> Path | None:
    """The bridge plugin file loaded in this 3ds Max, or None."""
    found = _bridge_module()
    return Path(found[0]) if found else None


def available() -> bool:
    """True inside a 3ds Max whose bridge exports the in-process entry point."""
    global _function
    if _function is None:
        _function = _locate() or False
    return bool(_function)


def bridge_call(request: str) -> bytes:
    """Send one request JSON to the bridge in-process and return its response bytes."""
    if not available():
        raise ConnectionError(
            "This 3ds Max has no in-process MCP bridge entry point; update the 3dsmax-mcp plugin.")
    data = request.encode("utf-8")
    size = ctypes.c_size_t()
    pointer = _function(data, len(data), ctypes.byref(size))
    return ctypes.string_at(pointer, size.value) if pointer else b""
