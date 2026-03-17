import ctypes
import ctypes.wintypes as wintypes
import json
import socket
import threading
import time
from typing import Any, Optional
from uuid import uuid4

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 120.0
DEFAULT_PIPE_NAME = r"\\.\pipe\3dsmax-mcp"

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
_INVALID_HANDLE = wintypes.HANDLE(-1).value


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
        self._pipe_lock = threading.Lock()

    @property
    def native_available(self) -> bool:
        """Check whether the native C++ bridge is currently available."""
        if self.transport == "pipe":
            return True
        if self.transport == "tcp":
            return False
        return self._probe_pipe_available()

    def _probe_pipe_available(self) -> bool:
        """Best-effort probe that treats a busy pipe as available."""
        handle = _kernel32.CreateFileW(
            self.pipe_name,
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

        if _kernel32.WaitNamedPipeW(self.pipe_name, 0):
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

    def _ensure_pipe_handle(self, deadline: float) -> int:
        handle = self._pipe_handle
        if handle not in (None, 0, _INVALID_HANDLE):
            return handle

        while True:
            handle = _kernel32.CreateFileW(
                self.pipe_name,
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
                    f"Named pipe {self.pipe_name} not found. "
                    "Is the MCP Bridge plugin loaded in 3ds Max?"
                )
            if err != _ERROR_PIPE_BUSY:
                raise ConnectionError(f"Failed to open pipe: Win32 error {err}")

            remaining_ms = int((deadline - time.perf_counter()) * 1000)
            if remaining_ms <= 0:
                raise TimeoutError(
                    f"Timed out waiting for named pipe {self.pipe_name} after "
                    f"{self.timeout}s."
                )

            wait_ms = min(remaining_ms, 250)
            if _kernel32.WaitNamedPipeW(self.pipe_name, wait_ms):
                continue
            wait_err = ctypes.get_last_error()
            if wait_err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
                raise ConnectionError(
                    f"Named pipe {self.pipe_name} disappeared while waiting."
                )
            if wait_err in (_ERROR_SEM_TIMEOUT, _ERROR_PIPE_BUSY):
                continue
            raise ConnectionError(
                f"Failed waiting for named pipe {self.pipe_name}: "
                f"Win32 error {wait_err}"
            )

    def send_command(
        self,
        command: str,
        cmd_type: str = "maxscript",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Send a command to 3ds Max and return the parsed JSON response."""
        effective_timeout = timeout or self.timeout
        request_id = uuid4().hex
        started_at = time.perf_counter()

        request = json.dumps({
            "command": command,
            "type": cmd_type,
            "requestId": request_id,
            "protocolVersion": 2,
        }, ensure_ascii=True)

        if self.transport == "pipe":
            response_data = self._send_via_pipe(request, effective_timeout)
        elif self.transport == "tcp":
            response_data = self._send_via_tcp(request, effective_timeout)
        else:
            try:
                response_data = self._send_via_pipe(request, effective_timeout)
            except (ConnectionError, TimeoutError):
                response_data = self._send_via_tcp(request, effective_timeout)

        return self._parse_response(response_data, request_id, started_at)

    # ── Named Pipe transport ─────────────────────────────────────
    def _send_via_pipe(self, request: str, timeout: float) -> bytes:
        deadline = time.perf_counter() + timeout
        data = (request + "\n").encode("utf-8")

        with self._pipe_lock:
            for attempt in range(2):
                handle = self._ensure_pipe_handle(deadline)
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
                        total_written += written.value

                    response_data = bytearray()
                    buf = ctypes.create_string_buffer(65536)
                    while True:
                        if time.perf_counter() >= deadline:
                            self._close_pipe_handle()
                            raise TimeoutError(
                                f"Timed out waiting for named pipe response after "
                                f"{timeout}s."
                            )

                        bytes_read = wintypes.DWORD()
                        ok = _kernel32.ReadFile(
                            handle, buf, len(buf), ctypes.byref(bytes_read), None
                        )
                        if bytes_read.value > 0:
                            response_data.extend(buf.raw[:bytes_read.value])
                            if b"\n" in response_data:
                                return bytes(response_data)

                        if not ok:
                            err = ctypes.get_last_error()
                            if err == _ERROR_BROKEN_PIPE:
                                raise BrokenPipeError(
                                    "Pipe closed while reading response."
                                )
                            raise ConnectionError(
                                f"Failed reading from pipe: Win32 error {err}"
                            )

                        if bytes_read.value == 0:
                            raise BrokenPipeError(
                                "Pipe closed before response terminator."
                            )
                except BrokenPipeError:
                    self._close_pipe_handle()
                    if attempt == 0 and time.perf_counter() < deadline:
                        continue
                    raise ConnectionError("Named pipe connection closed during request.")
                except ConnectionError:
                    self._close_pipe_handle()
                    if attempt == 0 and time.perf_counter() < deadline:
                        continue
                    raise

    # ── TCP transport (legacy) ───────────────────────────────────
    def _send_via_tcp(self, request: str, timeout: float) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        try:
            sock.connect((self.host, self.port))
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
            raise TimeoutError(
                f"3ds Max did not respond within {timeout}s. "
                "Is the MCP TCP listener running in 3ds Max?"
            )
        except ConnectionRefusedError:
            raise ConnectionError(
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
            raise RuntimeError(f"MAXScript error: {error_msg}")

        return response
