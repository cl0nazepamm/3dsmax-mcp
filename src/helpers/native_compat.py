"""Compatibility helpers for native routes added after older bridge releases."""

from __future__ import annotations

from src.max_client import MaxBridgeError


_MISSING_NATIVE_ROUTE_MARKERS = (
    "unknown command type",
    "unknown native command",
    "native command not found",
    "native route not found",
)


def is_missing_native_route_error(exc: BaseException) -> bool:
    """Return whether an older bridge rejected a command it does not register."""
    if isinstance(exc, MaxBridgeError):
        messages = [exc.bridge_message]
        response_error = exc.bridge_response.get("error")
        if isinstance(response_error, str):
            messages.append(response_error)
    elif isinstance(exc, RuntimeError):
        messages = [str(exc)]
    else:
        return False

    return any(
        marker in message.casefold()
        for message in messages
        for marker in _MISSING_NATIVE_ROUTE_MARKERS
    )
