"""Main-thread (UI) hygiene.

One tool to see what quietly runs on the 3ds Max main/UI thread — redraw-views
callbacks, live .NET timers held in globals, general-callback count, playback
state — and to kill/disable a specific hook. callbacks.show() alone misses
timers and per-redraw callbacks; this fills that gap. Backed by the native
bridge (native:main_thread).
"""

import json
from ..server import mcp, client


@mcp.tool()
def main_thread(action: str = "list", name: str = "") -> str:
    """Inspect or clean up what runs on the Max main/UI thread.

    action:
      - "list" (default) — enumerate main-thread activity as JSON:
            redrawCallbacks {enabled, count, raw}  (run on EVERY viewport redraw)
            dotNetTimers [{scope,name,class,enabled,intervalMs}]
                (enabled=true => firing; tiny intervalMs = a landmine)
            generalCallbackHandlers (count of event-driven callbacks)
            globalsScanned, state {isAnimPlaying, activeShadeRenderer}
      - "native_threads" — the C++ side: every process thread attributed to its
            owning DLL, with per-thread CPU sampled over ~500 ms. Returns
            byModule [{module,threads,cpuMsDelta,cpuMsTotal,pctBusy}] and
            topThreads [{tid,module,cpuMsDelta}] — shows which native plugin is
            burning cycles (SetTimer/RegisterNotification have no enumeration API,
            but a chugging C++ timer/worker shows up here as CPU on its module).
      - "unregister_redraw"  name=<global fn>    remove one redraw-views callback
                                                 (e.g. name="gw_viewInfo")
      - "disable_all_redraw"                     disable ALL redraw-views callbacks
      - "enable_all_redraw"                      re-enable them
      - "stop_timer"         name=<global timer> .Stop() + .Enabled=false
                                                 (e.g. name="gSimClock")
      - "remove_callback"    name=<callback id>  callbacks.removeScripts id:<name>

    Reversible: redraw callbacks re-register on Max restart; enable_all_redraw
    undoes disable_all_redraw. Kill actions return JSON {ok, action, name?, error?}.
    """
    if not client.native_available:
        return json.dumps({"ok": False, "error": "native bridge required for main_thread"})
    payload = json.dumps({"action": action, "name": name})
    response = client.send_command(payload, cmd_type="native:main_thread")
    return response.get("result", "")
