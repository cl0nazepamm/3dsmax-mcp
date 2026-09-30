# Executor deadlines

Native named-pipe requests may supply `timeoutMs` (integer milliseconds, default
120000). This is the deadline for starting queued main-thread work, not a deadline
that interrupts a running callback. The Python client sends its requested timeout.
Install the rebuilt bridge together with the Python changes; older bridges do not
implement the new cancellation contract.

A queued work item has one atomic state: pending, running, done, or cancelled.
Only pending work can be cancelled. The UI thread claims pending -> running before
calling it, releases the completion mutex during the callback, and publishes done
under the mutex afterwards. An expired/cancelled item is skipped in both the Windows
message queue and the nested-message deferred queue. A caller whose callback has
started keeps waiting, preserving stack variables borrowed by reference.

Wire responses distinguish:

- `TASK_CANCELLED`, `retryable: true`, `meta.executionStatus: cancelled_before_start`:
  no executor work in this request completed, and the cancelled callback never ran.
- `meta.executionStatus: completed_late`: the actual result (or original execution
  error) is returned. MCP envelopes retain a `COMPLETED_LATE` warning even in minimal
  mode. Do not replay a successful late request.
- `REQUEST_PARTIALLY_EXECUTED`, `retryable: false`: an earlier executor call in the
  same request completed before a later call was cancelled. Inspect before retrying.
- A broken named pipe after sending remains an unknown outcome. The transport does
  not resend it or fall back to TCP. A deadline alone does not mean outcome unknown.

Read-only direct-mode callbacks and calls already on the UI thread remain inline;
they are not queued. Legacy TCP timeout semantics are unchanged. A running callback
that never finishes still holds its caller; interrupting it safely would require
owned captures and an explicit cancellation protocol, beyond this change.

## SDK-independent Windows regression suite

From the repository root, with CMake and MSVC available:

```powershell
cmake -S native -B build-executor -DMCP_EXECUTOR_TESTS_ONLY=ON
cmake --build build-executor --config Release
ctest --test-dir build-executor -C Release --output-on-failure
python -m unittest discover -s tests -p test_executor_outcomes.py -v
```

The C++ test uses the production executor and a real Win32 message loop. It covers
queue cancellation with zero side effects, running callbacks borrowing stack data,
a nested progress-window message pump and deferred cancellation, 100 sequential
requests, 200 cancellation/start races, and an exception after the deadline.

## Optional test inside Max

Back up the installed GUP before rebuilding. Build with
`-DMAX_VERSION=2027 -DMAXSDK_PATH=<2027 maxsdk> -DMCP_BUILD_EXECUTOR_PROBE=ON`.
This produces `executor_probe.dll` in addition to the normal GUP. In a separate,
empty Max session, load the DLL using Python `ctypes.WinDLL`, declare
`RunExecutorProbe.argtypes = [ctypes.c_wchar_p]` and `restype = ctypes.c_int`, and
call it on the Max UI thread with an absolute output report path. Return code zero
and `ALL PASS` in the report indicate success. The probe refuses a nonempty scene.

The probe links the same production executor source with a unique window class so
it can coexist with an installed bridge; it does not replace or test the full GUP
transport end to end. Live timings are a 10-second blocker with a 2-second queue
deadline and 3-second running callbacks with 500-millisecond deadlines. It starts
no listener and modifies no scene objects. Do not load it into a working scene.
