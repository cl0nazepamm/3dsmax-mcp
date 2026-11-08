"""MaxClient whose transport is the bridge's in-process entry point instead of the named pipe."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional
from uuid import uuid4

from ..max_client import MaxClient
from . import binding


class InProcessClient(MaxClient):
    """Builds and parses requests exactly like MaxClient; only the transport differs.

    Inside Max the call runs on the main thread, so the bridge runs its handler inline
    and returns when it is done: no pipe, routing, job waits or hang probes apply.
    """

    def __init__(self, call: Callable[[str], bytes] | None = None) -> None:
        super().__init__(transport="pipe")
        self._call = call

    @property
    def native_available(self) -> bool:
        return True

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
        request_id = uuid4().hex
        started_at = time.perf_counter()
        self._reset_response()
        request = json.dumps({
            **(request_fields or {}),
            "command": command,
            "type": cmd_type,
            "requestId": request_id,
            "protocolVersion": 2,
        }, ensure_ascii=True)
        try:
            response = self._parse_response((self._call or binding.bridge_call)(request), request_id, started_at)
        except Exception as exc:
            self._local.last_error = {"transport": "inprocess", "request_id": request_id, "error": str(exc)}
            raise
        meta = response.setdefault("meta", {})
        meta.setdefault("transport", "inprocess")
        meta.setdefault("requestedTransport", "inprocess")
        self._local.last_response = response
        return response
