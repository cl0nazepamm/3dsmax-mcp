"""Never let this process exit while it holds a 3ds Max thread suspended.

capture_hang_diagnostics suspends Max threads for milliseconds to read their
stacks. Where the OS supports it (Windows 11 / Server 2022 and later) the
suspension is made through a thread state-change object, which the kernel
reverts when its handle closes, so even a hard kill of this process
(TerminateProcess) resumes the thread. The plain SuspendThread fallback has no
such safety net, so every suspension also goes through `suspended()`, which
suspends and registers in one step under the guard lock, and every exit path
(the parent watchdog's os._exit, server.main's os._exit, and atexit) calls
`release_all()` first: it stops new suspensions, waits briefly for the active
ones, then resumes whatever is still registered itself.
"""

from __future__ import annotations

import atexit
import itertools
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

RELEASE_WAIT_S = 0.5

_cond = threading.Condition()
_active: dict[int, Callable[[], None]] = {}
_ids = itertools.count(1)
_closing = False


class ExitingError(RuntimeError):
    """The process is exiting; no new suspension may start."""


def _once(resume: Callable[[], object]) -> Callable[[], None]:
    """`resume` at most once; a concurrent second caller waits until it has returned.

    resume() runs under the lock (resuming never blocks), so "done" means the
    thread really runs again: nobody can close the thread handle or exit the
    process between a first caller deciding to resume and the resume itself.
    A resume() that raises is not marked done, so release_all can retry it.
    """
    lock = threading.Lock()
    done = False

    def call() -> None:
        nonlocal done
        with lock:
            if done:
                return
            resume()
            done = True

    return call


def _quietly(fn: Callable[[], object]) -> None:
    try:
        fn()
    except Exception:
        pass


@contextmanager
def suspended(suspend: Callable[[], bool], resume: Callable[[], object]) -> Iterator[bool]:
    """Run `suspend()` (True on success) and guarantee exactly one `resume()` after it.

    Yields whether the suspension took effect. Raises ExitingError, without
    calling `suspend`, once release_all() has started. If an exception (such
    as a KeyboardInterrupt) escapes between the OS suspending the thread and
    the registration, `resume()` is called before it propagates: resuming a
    thread that is not suspended is a no-op.
    """
    resume_once = _once(resume)
    token = next(_ids)
    registered = False
    with _cond:
        if _closing:
            raise ExitingError("this process is exiting; no thread was suspended")
        # Under the lock, so an exit cannot begin between suspending and registering.
        try:
            if suspend():
                _active[token] = resume_once
                registered = True
        except BaseException:
            _active.pop(token, None)
            _quietly(resume_once)
            raise
    try:
        yield registered
    finally:
        if registered:
            try:
                resume_once()
            finally:
                with _cond:
                    _active.pop(token, None)
                    _cond.notify_all()


def _snapshot() -> list[Callable[[], None]]:
    for _ in range(5):
        try:
            return list(_active.values())
        except RuntimeError:  # changed size while copying
            continue
    return []


def release_all(wait_s: float = RELEASE_WAIT_S) -> int:
    """Block new suspensions, wait up to `wait_s` for active ones, resume the rest.

    Returns how many suspensions had to be resumed here. Never raises and never
    blocks much longer than `wait_s`, even if another thread holds the guard
    lock: it then resumes a lock-free snapshot of the registered suspensions.
    """
    global _closing
    try:
        _closing = True  # before taking the lock: no new suspension may start
        wait_s = max(0.0, wait_s)
        deadline = time.monotonic() + wait_s
        if _cond.acquire(True, wait_s):
            try:
                while _active:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    _cond.wait(remaining)
                leftovers = list(_active.values())
            finally:
                _cond.release()
        else:
            leftovers = _snapshot()
        for resume in leftovers:
            _quietly(resume)
        return len(leftovers)
    except Exception:
        return 0


def active_count() -> int:
    with _cond:
        return len(_active)


def _reset_for_tests() -> None:
    global _closing
    with _cond:
        _closing = False
        _active.clear()


atexit.register(release_all)
