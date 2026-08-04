"""Compact session context tool for natural AI + 3ds Max interaction."""

from __future__ import annotations

import json

from ..server import mcp, client
from ._query_scene_core import run_overview, run_selection


def _unit_context() -> dict:
    script = r"""(
        fn esc value = (
            local s = if value == undefined then "" else (value as string)
            s = substituteString s "\\" "\\\\"
            s = substituteString s "\"" "\\\""
            s = substituteString s "\t" "\\t"
            s = substituteString s "\r" "\\r"
            s = substituteString s "\n" "\\n"
            s
        )
        "{\"systemType\":\"" + (esc (units.SystemType as string)) + "\"" + \
        ",\"systemScale\":" + (units.SystemScale as string) + \
        ",\"displayType\":\"" + (esc (units.DisplayType as string)) + "\"" + \
        ",\"distanceBase\":\"system units\"" + \
        "}"
    )"""
    try:
        response = client.send_command(script, cmd_type="maxscript", timeout=5.0)
        payload = json.loads(response.get("result", "{}"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {
            "systemType": "unknown",
            "systemScale": None,
            "displayType": "unknown",
            "distanceBase": "system units",
        }


@mcp.tool()
def get_session_context(
    max_roots: int = 20,
    max_selection: int = 20,
) -> str:
    """Bundle bridge, capabilities, scene overview, and selection in one call.

    Use when: the user explicitly wants a full live snapshot (bridge + caps + scene + selection).
    Not when: starting a task or before every edit — prefer query_scene for scene reads and
    get_bridge_status only on connection failures.
    """
    from .bridge import get_bridge_status
    from .capabilities import get_plugin_capabilities

    return json.dumps({
        "bridge": json.loads(get_bridge_status()),
        "capabilities": json.loads(get_plugin_capabilities()),
        "scene": json.loads(run_overview(client, max_roots=max_roots)),
        "selection": json.loads(run_selection(client, detail="full", max_items=max_selection)),
        "units": _unit_context(),
    })
