"""Bridge/transport status tools for the live 3ds Max connection."""

from __future__ import annotations

import json

from ...core.max_client import (_NOT_DIALOG_TITLE, ALWAYS_ASK, BLOCKED_CAUSE, HANG_ADVICE, DialogBlocked, MaxBusyError,
                          MaxNotRespondingError, RequestOutcomeUnknown, _grace, real_dialogs)
from ...core.process_health import describe as describe_process, diagnose_process
from ...server import mcp, client

_HEALTH_TIMEOUT_S = 3.0
_CPU_SAMPLE_S = 1.0
# A main thread idle in a wait for less than this is reported busy, not blocked
# (a short non-pumping call looks the same as a deadlock from outside).
_BLOCKED_CONFIRM_S = 20.0
# A bridge request younger than this does not make Max "busy": the ping queues behind it briefly.
_BUSY_RUNNING_MS = 2000
# Bridge-internal dispatchers (invoke_tool/tool_smoke probes); never another MCP client.
_INTERNAL_CLIENTS = frozenset({"native-tool-probe"})
# Clock skew allowed between a request's registry age and its executor run time.
_ATTRIBUTION_SLACK_MS = 250
# Control requests answered on a bridge pipe thread; never another client's work on Max.
_CONTROL_REQUESTS = frozenset({"native:max_dialogs", "health"})
# Status pings queue on the main thread but change nothing: not work to report, and a
# dropped one may be this server's own earlier probe.
_STATUS_PROBES = frozenset({"ping"})


def _legacy_bridge_status() -> str:
    maxscript = r"""(
        local esc = MCP_Server.escapeJsonString
        local maxYear = ((1998 + ((maxVersion())[1] / 1000)) as integer)
        local rendererName = try ((classOf renderers.current) as string) catch "unknown"
        "{\"pong\":true" + \
        ",\"server\":\"3dsmax-mcp\"" + \
        ",\"protocolVersion\":1" + \
        ",\"maxVersion\":" + (maxYear as string) + \
        ",\"renderer\":\"" + (esc rendererName) + "\"" + \
        ",\"objectCount\":" + (objects.count as string) + \
        ",\"selectionCount\":" + (selection.count as string) + \
        ",\"safeMode\":" + (if MCP_Server.safeMode then "true" else "false") + \
        ",\"port\":" + (MCP_Server.port as string) + \
        "}"
    )"""
    # Read-only: dropped at 5 s like ping instead of waiting out a busy Max.
    response = client.send_command(maxscript, timeout=5.0, probe=True)
    payload = json.loads(response.get("result", "{}"))
    payload["requestId"] = response.get("requestId")
    payload["meta"] = response.get("meta", {})
    payload["connected"] = True
    payload["legacyTransport"] = True
    return json.dumps(payload)


def _native_health() -> dict | MaxBusyError | MaxNotRespondingError | None:
    """Executor/client state from a bridge pipe thread; never touches Max's main thread.

    Returns the health payload; the busy/hung verdict if even the bridge's pipe
    threads did not answer; or None when the bridge predates the health command
    (it answers "Unknown command type" at once), only TCP is available, or no
    pipe exists. The caller falls back to ping on None.
    """
    if getattr(client, "transport", None) == "tcp":
        return None  # the legacy TCP listener has no health and runs on the main thread
    try:
        response = client.send_command("", cmd_type="health", timeout=_HEALTH_TIMEOUT_S)
        payload = json.loads(response.get("result") or "{}")
    except (MaxBusyError, MaxNotRespondingError) as exc:
        return exc
    except Exception:  # best-effort probe: old bridge, no pipe, TCP-only
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("mainThread"), dict):
        return None
    # Bridges from 1.7.5 list the dialogs blocking Max on every reply (window-manager state only).
    # Cosmos browser, Material Editor and viewport windows are never dialogs (older bridges list them).
    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    payload["openDialogs"] = real_dialogs(meta.get("openDialogs"))
    return payload


def _compact_health(health: dict) -> dict:
    main = health.get("mainThread") or {}
    executor = health.get("executor") or {}
    clients = health.get("clients") or {}
    return {
        "main_thread": main.get("state"),
        "heartbeat_age_ms": main.get("heartbeatAgeMs"),
        "queued": executor.get("queued"),
        "completed": executor.get("completed"),
        # Includes this status probe's own pipe connection.
        "clients_connected": clients.get("connected"),
        "bridge_version": health.get("bridgeVersion"),
    }


def _seconds(ms) -> float | None:
    return round(ms / 1000.0, 1) if isinstance(ms, (int, float)) else None


def _skip_ping(health: dict) -> bool:
    """True when the health reply alone shows a ping would only queue behind a busy main thread."""
    main = health.get("mainThread") or {}
    state = main.get("state")
    if state == "busy_mcp":
        # A request that just started (another client's quick call) is not "busy": ping queues briefly.
        running = (health.get("executor") or {}).get("running") or {}
        return (running.get("runningMs") or 0) >= _BUSY_RUNNING_MS
    if state == "busy_other":
        # Version 1 bridges timed the heartbeat with WM_TIMER, which paints and posted
        # messages starve while the main thread still pumps; only trust it if the window is hung.
        return (health.get("healthVersion") or 1) >= 2 or main.get("windowHung") is True
    return False


def _internal(entry: dict) -> bool:
    return bool(entry.get("internal")) or entry.get("clientId") in _INTERNAL_CLIENTS


def _blocked_ids() -> set:
    """This server's BLOCKED_BY_DIALOG calls still waiting in Max (they are ours, not another client's)."""
    ids = client.blocked_request_ids()
    return {i for i in ids if isinstance(i, str)} if isinstance(ids, (list, tuple, set)) else set()


def _dialog_summaries(dialogs) -> list[dict]:
    keep = ("dialog_id", "title", "kind", "main_thread", "age_ms", "during_requests", "text", "buttons")
    return [{k: d[k] for k in keep if k in d} for d in dialogs or [] if isinstance(d, dict)]


def _blocked_by_dialog_status(dialogs: list, *, health: dict | None = None, running: dict | None = None,
                              owner: str | None = None, request_sent: bool = False) -> str:
    """pong=false status: a modal dialog holds Max's main thread. Not a hang: it pumps."""
    pid = (health or {}).get("pid")
    label = f"3ds Max (PID {pid})" if pid else "3ds Max"
    titles = ", ".join(repr(d.get("title", "")) for d in dialogs if isinstance(d, dict)) or "a dialog"
    if running:
        holder = {"this_server": "this server's", "other_client": "another MCP client's"}.get(owner or "", "a")
        what = f"{holder} '{running.get('cmdType') or 'unlabelled'}' request waits on it"
    else:
        what = "a request waits on it"
    main = (health or {}).get("mainThread") or {}
    executor = (health or {}).get("executor") or {}
    return json.dumps({
        "pong": False,
        "connected": True,
        "bridge_state": "blocked_by_dialog",
        "bridge_code": DialogBlocked.code,
        "retryable": False,
        "message": (f"{label} is waiting on {titles}: {what}, and new requests queue behind it. Max is not hung "
                    "(its main thread runs the dialog). Read it with max_dialogs(action='inspect'); answer it with "
                    "max_dialogs(action='respond') only when the user authorized unattended work, otherwise ask the "
                    "user. " + ALWAYS_ASK + " Do not repeat the waiting call."),
        "dialogs": _dialog_summaries(dialogs),
        "main_thread": ({"state": main.get("state"), "pumping": main.get("pumping"),
                         "heartbeat_age_s": _seconds(main.get("heartbeatAgeMs")),
                         "running": ({"cmd_type": running.get("cmdType"),
                                      "running_s": _seconds(running.get("runningMs")),
                                      "owner": owner, "blocked_by_dialog": True} if running else None),
                         "queued": executor.get("queued")} if health else None),
        "blocked_calls": sorted(_blocked_ids()),
        "request_sent": request_sent,
        **({"health": _compact_health(health)} if health else {}),
    })


def _held_by_dialog(health: dict, request_sent: bool = False) -> str | None:
    """blocked_by_dialog status when a main-thread dialog holds the running MCP request.

    Only while the main thread still pumps (a modal loop does); a main thread that
    stopped pumping is busy or hung whatever dialog is open, and the busy logic
    reports it. The dialog monitor's status reads window-manager state only.
    """
    main = health.get("mainThread") or {}
    running = (health.get("executor") or {}).get("running") or {}
    request_id = running.get("requestId")
    if not health.get("openDialogs") or main.get("state") != "busy_mcp" or main.get("pumping") is False \
            or not request_id:
        return None
    try:
        status = client._dialog_control("status")
        holding = [d for d in status.get("dialogs", []) if isinstance(d, dict) and d.get("main_thread")
                   and request_id in (d.get("during_requests") or [])
                   and not _NOT_DIALOG_TITLE.match(str(d.get("title", "")))]
    except Exception:
        return None
    if not holding:
        return None
    mine = client.inflight()
    mine_id = mine.get("request_id") if isinstance(mine, dict) else None
    if request_id == mine_id or request_id in _blocked_ids():
        owner = "this_server"
    else:
        entry = next((r for r in (health.get("clients") or {}).get("inflight") or []
                      if r.get("requestId") == request_id), None)
        owner = "other_client" if entry is not None and not _internal(entry) else "unknown"
    return _blocked_by_dialog_status(holding, health=health, running=running, owner=owner, request_sent=request_sent)


def _running_owner(running: dict, inflight: list[dict], mine_id, abandoned_id) -> str:
    """Who submitted the running item: this_server, abandoned (ours), other_client or unknown."""
    request_id = running.get("requestId")
    if request_id:  # healthVersion >= 2 names the running request
        entry = next((r for r in inflight if r.get("requestId") == request_id), None)
    else:
        # Older bridges only report the cmd type. A candidate must have existed at
        # least as long as the item has run (a queued request is younger).
        run_ms = running.get("runningMs") or 0
        candidates = [r for r in inflight if r.get("cmdType") == running.get("cmdType")
                      and (r.get("elapsedMs") or 0) + _ATTRIBUTION_SLACK_MS >= run_ms]
        entry = candidates[0] if len(candidates) == 1 else None
        request_id = entry.get("requestId") if entry else None
    if request_id and request_id == mine_id:
        return "this_server"
    if request_id and request_id == abandoned_id:
        return "abandoned"
    if entry is not None and not _internal(entry):
        return "other_client"
    return "unknown"


def _busy_from_health(health: dict, probe_queued: bool = False) -> str | None:
    """pong=false status built from the health reply alone.

    None means the evidence is inconclusive (the main window still responds while no
    bridge work runs) and the caller should ping instead. probe_queued: this call's
    dropped ping still waits behind the running request (it changes nothing).
    """
    main = health.get("mainThread") or {}
    executor = health.get("executor") or {}
    running = executor.get("running") or None
    pid = health.get("pid")
    mine = client.inflight()
    mine = mine if isinstance(mine, dict) else None
    verdict = client.hung_verdict(int(pid)) if pid else None
    verdict = verdict if isinstance(verdict, dict) else None
    # This server's request abandoned after Max stopped responding: the bridge may still run it.
    abandoned = verdict.get("inflight") if verdict and isinstance(verdict.get("inflight"), dict) else None
    mine_id = (mine or {}).get("request_id")
    abandoned_id = (abandoned or {}).get("request_id")
    inflight = (health.get("clients") or {}).get("inflight") or []
    blocked_ids = _blocked_ids()
    ours = {i for i in (mine_id, abandoned_id) if i} | blocked_ids
    others = [r for r in inflight if r.get("requestId") not in ours and not _internal(r)
              and r.get("cmdType") not in _CONTROL_REQUESTS | _STATUS_PROBES]
    sent = "This status ping waits behind it and changes nothing" if probe_queued else "Nothing was sent"

    held_by_dialog = False
    if running:
        owner = _running_owner(running, inflight, mine_id, abandoned_id)
        if owner in ("other_client", "unknown") and running.get("requestId") in blocked_ids:
            owner, held_by_dialog = "this_server", True  # returned BLOCKED_BY_DIALOG, still running in Max
        cmd = running.get("cmdType") or "unlabelled"
        elapsed = _seconds(running.get("runningMs"))
        if owner == "abandoned":
            what = (f"this server's earlier '{cmd}' request (abandoned after Max stopped responding) is still "
                    f"running on Max's main thread after {elapsed} s")
        else:
            holder = {"this_server": "this server's", "other_client": "another MCP client's",
                      "unknown": "a bridge"}[owner]
            what = f"{holder} '{cmd}' request has been running on Max's main thread for {elapsed} s"
    else:
        owner = None
        what = ("Max's main thread stopped pumping messages outside the bridge (rendering, loading, a "
                "script or dialog started from the UI, or a plugin)")

    # Only sample CPU when the main thread is not pumping: a pumping request is merely long.
    process = diagnose_process(int(pid), _CPU_SAMPLE_S) if pid and not main.get("pumping") else None
    if not running and (process or {}).get("state") == "responsive":
        return None  # main window still answers: let the ping (5 s deadline) decide
    stuck_s = _seconds(main.get("heartbeatAgeMs")) or 0.0
    blocked = bool(process) and (process.get("state") == "exited" or (
        process.get("state") == "blocked" and stuck_s >= _BLOCKED_CONFIRM_S))
    if blocked and owner == "this_server" and process.get("state") != "exited":
        # Same rule as a held pipe lock: this server's own request is hung only once overdue.
        # A call that returned BLOCKED_BY_DIALOG is no longer in flight: use its own record.
        own = client.blocked_call(running.get("requestId")) if held_by_dialog else mine
        if isinstance(own, dict):
            limit = own.get("timeout_s")
            blocked = limit is not None and (own.get("running_s") or 0) >= limit + _grace(limit)
    if blocked:
        state, code = "not_responding", MaxNotRespondingError.code
        message = (f"3ds Max (PID {pid}) is not responding: {what}; {describe_process(process)}. "
                   f"Max is blocked: {BLOCKED_CAUSE}. {sent}. {HANG_ADVICE} "
                   "capture_hang_diagnostics shows where it is stuck (OS-only; pauses each Max thread for milliseconds).")
    else:
        state, code = "busy", MaxBusyError.code
        evidence = f" ({describe_process(process)})" if process else ""
        message = (f"3ds Max (PID {pid}) is busy: {what}{evidence}. {sent}; "
                   "new requests queue behind it. Wait and re-check with get_bridge_status.")
    return json.dumps({
        "pong": False,
        "connected": not (process and process.get("state") == "exited"),
        "bridge_state": state,
        "bridge_code": code,
        "retryable": state == "busy",
        "message": message,
        "main_thread": {
            "state": main.get("state"),
            "pumping": main.get("pumping"),
            "heartbeat_age_s": _seconds(main.get("heartbeatAgeMs")),
            "running": ({"cmd_type": running.get("cmdType"), "running_s": _seconds(running.get("runningMs")),
                         "owner": "this_server" if owner == "abandoned" else owner,
                         "abandoned": owner == "abandoned",
                         **({"blocked_by_dialog": True} if held_by_dialog else {})} if running else None),
            "queued": executor.get("queued"),
            "oldest_queued_s": _seconds(executor.get("oldestQueuedMs")),
        },
        **({"open_dialogs": _dialog_summaries(health["openDialogs"])} if health.get("openDialogs") else {}),
        "inflight": mine,
        **({"previous": abandoned, "previous_process": verdict.get("process")} if abandoned else {}),
        "other_clients": [{"client_id": r.get("clientId"), "cmd_type": r.get("cmdType"),
                           "elapsed_s": _seconds(r.get("elapsedMs"))} for r in others],
        "process": process,
        "request_sent": probe_queued,
        "health": _compact_health(health),
    })


def _unhealthy_status(exc: MaxBusyError | MaxNotRespondingError, health: dict | None = None) -> str:
    """Status payload for a busy or hung Max (deliberately no error/code keys)."""
    details = getattr(exc, "details", None) or {}
    busy = isinstance(exc, MaxBusyError)
    process = details.get("process") or {}
    return json.dumps({
        "pong": False,
        "connected": busy and process.get("alive", True) is not False,
        "bridge_state": "busy" if busy else "not_responding",
        "bridge_code": exc.code,
        "retryable": exc.retryable,
        "message": str(exc),
        "inflight": details.get("inflight"),
        "process": details.get("process"),
        "request_sent": details.get("request_sent", False),
        **({"previous": details["previous"]} if details.get("previous") else {}),
        **({"open_dialogs": _dialog_summaries(health["openDialogs"])} if (health or {}).get("openDialogs") else {}),
        **({"health": _compact_health(health)} if health else {}),
    })


def _lost_ping_status(exc: RequestOutcomeUnknown, health: dict | None = None) -> str:
    """Status when the connection broke after the ping was sent (Max exiting, the bridge dropping the pipe)."""
    pid = (health or {}).get("pid")
    process = diagnose_process(int(pid), _CPU_SAMPLE_S) if pid else None
    exited = (process or {}).get("state") == "exited"
    label = f"3ds Max (PID {pid})" if pid else "3ds Max"
    evidence = f" ({describe_process(process)})" if process else ""
    return json.dumps({
        "pong": False,
        "connected": False,
        "bridge_state": "not_responding" if exited else "connection_lost",
        "bridge_code": getattr(exc, "code", "REQUEST_OUTCOME_UNKNOWN"),
        "retryable": not exited,
        "message": (f"{label} closed the connection while answering the status ping{evidence}: "
                    + ("Max has exited. Ask the user to restart Max, then inspect the scene."
                       if exited else "Max may be exiting, or its bridge dropped the pipe. The ping changes "
                       "nothing; re-check with get_bridge_status.")),
        "process": process,
        "request_sent": True,
        **({"health": _compact_health(health)} if health else {}),
    })


def _silent_bridge_status(exc: MaxBusyError | MaxNotRespondingError) -> str:
    """Status when even the bridge's pipe threads did not answer health (the probe was dropped)."""
    details = getattr(exc, "details", None) or {}
    process = details.get("process") or None
    pid = (process or {}).get("pid")
    label = f"3ds Max (PID {pid})" if pid else "3ds Max"
    evidence = f" ({describe_process(process)})" if process else ""
    advice = HANG_ADVICE if isinstance(exc, MaxNotRespondingError) else "Retry later."
    payload = json.loads(_unhealthy_status(exc))
    payload["message"] = (
        f"{label} did not answer the bridge health probe within {_HEALTH_TIMEOUT_S:g} s{evidence}. "
        "The bridge answers health off Max's main thread, so the whole process (not just its main thread) "
        f"appears frozen or suspended. The probe was dropped (it changes nothing). {advice}")
    # This server's request, not the dropped health probe.
    mine = client.inflight()
    probe = details.get("inflight") or {}
    payload["inflight"] = mine if isinstance(mine, dict) else (None if probe.get("cmd_type") == "health"
                                                              else details.get("inflight"))
    return json.dumps(payload)


@mcp.tool()
def get_bridge_status() -> str:
    """Ping the MCP bridge for protocol/transport metadata.

    Use when: a tool failed with a connection/transport/claim error and you need to diagnose.
    If Max is busy or hung, returns pong=false with bridge_state, what holds the main thread
    (this server's request, another MCP client's, or work outside the bridge) and process health.
    bridge_state blocked_by_dialog: a modal dialog holds a request; read it with max_dialogs.
    Not when: starting a session or before every task — prefer query_scene for scene work.
    """
    # Precedence: silent bridge; a dialog holding the running request while the main
    # thread pumps (BLOCKED_BY_DIALOG); busy or hung (MAX_BUSY / MAX_NOT_RESPONDING);
    # then a ping. A ping held by a request that started meanwhile is attributed from
    # a second health reading, in the same order.
    health = _native_health()
    if isinstance(health, (MaxBusyError, MaxNotRespondingError)):
        return _silent_bridge_status(health)  # a ping would wait out the same silence
    if health:
        status = _held_by_dialog(health)
        if status is not None:
            return status
    if health and _skip_ping(health):
        status = _busy_from_health(health)
        if status is not None:
            return status
    try:
        response = client.send_command("", cmd_type="ping", timeout=5.0)
    except DialogBlocked as exc:
        return _blocked_by_dialog_status(exc.dialogs, health=health or None, request_sent=True)
    except (MaxBusyError, MaxNotRespondingError) as exc:
        # A request that had only just started when health was read held the ping:
        # a fresh reading says whose it is.
        sent = bool((getattr(exc, "details", None) or {}).get("request_sent"))
        later = _native_health() if health and sent else None
        if isinstance(later, dict):
            status = _held_by_dialog(later, request_sent=True)
            if status is None and _skip_ping(later):
                status = _busy_from_health(later, probe_queued=True)
            if status is not None:
                return status
        return _unhealthy_status(exc, health)
    except RequestOutcomeUnknown as exc:
        return _lost_ping_status(exc, health or None)
    except RuntimeError as exc:
        error = str(exc)
        if "Empty command" in error or "Unknown command type" in error:
            try:
                return _legacy_bridge_status()
            except DialogBlocked as legacy_exc:
                return _blocked_by_dialog_status(legacy_exc.dialogs, request_sent=True)
            except (MaxBusyError, MaxNotRespondingError) as legacy_exc:
                return _unhealthy_status(legacy_exc)
            except RequestOutcomeUnknown as legacy_exc:
                return _lost_ping_status(legacy_exc)
        raise

    payload = json.loads(response.get("result", "{}"))
    payload["requestId"] = response.get("requestId")
    payload["meta"] = response.get("meta", {})
    payload["connected"] = True
    payload["legacyTransport"] = False
    if health:
        payload["health"] = _compact_health(health)
    return json.dumps(payload)
