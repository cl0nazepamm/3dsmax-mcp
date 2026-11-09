"""Hot reload of the native core DLL inside a running 3ds Max (development).

The bridge can run as a permanent shell GUP plus a reloadable core DLL. The
shell answers native:core over the independent control channel, so a rebuilt
core loads without restarting Max. This is Python orchestration of a shell
control route, not a native handler: it is never part of the core's
invoke_tool registry or smoke cases.
"""

from __future__ import annotations

import json
from typing import Any

from ...core.native_compat import is_missing_native_route_error
from ...core.max_client import CORE_MAX_WAIT_MS, MaxBridgeError, RequestOutcomeUnknown
from ...server import client, mcp
from ...core.tool_response import parse_error_payload

_RESTART_STEP = "To apply a native change, install the rebuilt GUP and restart 3ds Max."


class CoreControlError(Exception):
    """A native:core failure carried as a structured error with its next step."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.bridge_message = json.dumps(payload)
        super().__init__(str(payload.get("message", "")))


def _label(build: Any) -> str:
    return str(build) if build not in (None, "") else "(unknown build)"


def _native_error(exc: MaxBridgeError) -> dict[str, Any]:
    """The structured NativeError a bridge error carries, or a minimal stand-in.

    Same sources and order as the envelope's own error extraction: the bridge
    message, then the response's error field.
    """
    response = exc.bridge_response if isinstance(exc.bridge_response, dict) else {}
    return (parse_error_payload(exc.bridge_message) or parse_error_payload(response.get("error"))
            or {"type": "MaxBridgeError", "message": str(exc.bridge_message)})


def _failure_step(
    action: str, code: str, exc: MaxBridgeError, details: dict[str, Any], wait_ms: int
) -> tuple[str, list[str]]:
    if code == "CORE_STATIC":
        return ("Do not retry: this bridge runs the static single-DLL layout, which cannot reload its core. "
                + _RESTART_STEP, [])
    if code == "CORE_BUSY":
        busy = details.get("in_flight")
        calls = f"{busy} native call(s)" if busy is not None else "Native calls"
        return (f"The old core is still running. {calls} did not finish within {wait_ms} ms "
                f"after {details.get('attempts', 1)} attempt(s). Retry later, once long calls finish, "
                "or reload with a larger wait_ms.", ["reload_native_core"])
    if code == "CORE_LOAD_FAILED":
        fix = ("Fix what the message reports (path, missing dependency or broken build), rebuild the "
               "core DLL, then reload; do not retry unchanged.")
        restored = details.get("previous_restored")
        if restored is False:
            return ("No core is loaded: the previous core could not be restored, so native handlers are "
                    "unavailable until a working core is reloaded. " + fix, ["reload_native_core"])
        if restored is True:
            watch = (" watch_scene was stopped; start it again if you need it."
                     if details.get("watch_scene_stopped") is True else "")
            return ("The previous core was restored and is serving." + watch + " " + fix,
                    ["reload_native_core"])
        return ("The old core is still running. " + fix, ["reload_native_core"])
    if code == "CORE_VERSION_MISMATCH":
        return ("The old core is still running. The new core's API version does not match the loaded "
                "shell: rebuild the core from the same source as the shell. If the shell itself changed, "
                "reload cannot apply it. " + _RESTART_STEP, [])
    if is_missing_native_route_error(exc):
        return ("This bridge predates the hot-reload shell and has no native:core control. "
                + _RESTART_STEP, [])
    if code == "BAD_PARAM":
        return (f"Correct the arguments: action is status or reload, wait_ms is 0-{CORE_MAX_WAIT_MS}, "
                "path names the core DLL.", [])
    if action == "reload":
        return ("Call reload_native_core(action='status') to see which core is running before retrying.",
                ["reload_native_core"])
    return ("Diagnose the bridge connection with get_bridge_status.", ["get_bridge_status"])


def _failure_payload(action: str, exc: MaxBridgeError, wait_ms: int) -> dict[str, Any]:
    native = _native_error(exc)
    code = str(native.get("code") or "")
    details = dict(native["details"]) if isinstance(native.get("details"), dict) else {}
    if "in_flight" in native:
        details.setdefault("in_flight", native["in_flight"])
    if getattr(exc, "attempts", None):
        details["attempts"] = exc.attempts
    if native.get("hint"):
        details["native_hint"] = native["hint"]
    step, tools = _failure_step(action, code, exc, details, wait_ms)
    details["next_step"] = step
    payload: dict[str, Any] = {
        "type": native.get("type") or "NativeError",
        "message": str(native.get("message") or native.get("error") or exc.bridge_message),
        "details": details,
        "hint": {"message": step, **({"suggested_tools": tools} if tools else {})},
    }
    if code:
        payload["code"] = code
    if isinstance(native.get("retryable"), bool):
        payload["retryable"] = native["retryable"]
    return payload


def _success_step(action: str, state: dict[str, Any]) -> str:
    if action == "reload":
        if state.get("reloaded") is not True:
            return ("The shell did not report a reload. Call reload_native_core(action='status') "
                    "and check last_error.")
        return (f"Core {_label(state.get('build'))} replaced {_label(state.get('previous_build'))}. "
                "Verify the changed native behavior through its MCP tool. Python changes still need "
                "a fresh MCP server process.")
    if state.get("layout") == "static":
        return "Static layout: the core cannot be reloaded. " + _RESTART_STEP
    last_error = f" Last error: {state['last_error']}." if state.get("last_error") else ""
    if not state.get("loaded"):
        return (f"No core is loaded, so native handlers are unavailable.{last_error} Fix and rebuild "
                "the core DLL, then call reload_native_core(action='reload').")
    return (f"Core {_label(state.get('build'))} is loaded.{last_error} After rebuilding the core DLL, "
            "call reload_native_core(action='reload').")


@mcp.tool()
def reload_native_core(action: str = "status", wait_ms: int = 5000, path: str | None = None) -> str:
    """Report the native core's state, or reload a rebuilt core DLL into the running 3ds Max.

    Development only, for the hot layout (permanent shell GUP plus reloadable
    core DLL). Use action="reload" after rebuilding the core DLL so changed
    native handlers load without restarting Max. Python changes need a fresh
    MCP server process instead. Never call this as a session preamble.

    action:
      - "status" (default): safe at any time; changes nothing. wait_ms is
        ignored and path is rejected.
      - "reload": must be requested explicitly. Waits up to wait_ms (0-600000)
        for in-flight native calls to drain, then swaps in the core from path
        (optional; omitted, the shell loads its configured core). With wait_ms
        at most 20000, CORE_BUSY is retried up to 3 attempts before it is
        returned; a longer wait makes one attempt and returns CORE_BUSY directly.

    Result: layout ("static"|"hot"), loaded, build, bridge_version,
    core_api_version, host_api_version, source, loaded_from, loaded_at_ms,
    reloads, in_flight, last_error; reload adds reloaded, previous_build and
    drain_ms. next_step says what to do next; on failure it is in
    error.details.next_step and the hint.

    Errors: CORE_STATIC: static layout, reload unsupported; do not retry; a
    native change applies after the rebuilt GUP is installed and Max restarts. CORE_BUSY
    (retryable): native calls were still running; retry later or with a larger
    wait_ms. CORE_LOAD_FAILED and CORE_VERSION_MISMATCH: the old core keeps
    running unchanged; fix and rebuild the core, then reload again.
    """
    if action not in {"status", "reload"}:
        raise ValueError("action must be status or reload")
    if action == "status" and path is not None:
        raise ValueError("path applies to reload only")
    try:
        if action == "status":
            state = client.core_control("status")
        else:
            state = client.core_control("reload", wait_ms=wait_ms, path=path)
    except MaxBridgeError as exc:
        raise CoreControlError(_failure_payload(action, exc, wait_ms)) from exc
    except RequestOutcomeUnknown as exc:
        if action != "reload":
            raise
        step = ("The reload request reached 3ds Max but its response was lost. Do not resend it; call "
                "reload_native_core(action='status') and compare build, reloads and last_error.")
        raise CoreControlError({
            "type": "RequestOutcomeUnknown",
            "message": str(exc),
            "retryable": False,
            "details": {"next_step": step},
            "hint": {"message": step, "suggested_tools": ["reload_native_core"]},
        }) from exc

    result = dict(state)
    build, previous = result.get("build"), result.get("previous_build")
    if action == "reload" and result.get("reloaded") is True and build and build == previous:
        result.setdefault("warnings", []).append(
            f"build is unchanged ({build}); confirm the rebuilt DLL was loaded "
            "(compare loaded_from and loaded_at_ms)."
        )
    result["next_step"] = _success_step(action, result)
    return json.dumps(result)
