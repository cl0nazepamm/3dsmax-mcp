import json as _json
from typing import Optional
from ...server import mcp, client
from ...core.coerce import StrList
from ...core.maxscript import NODE_LOOKUP_FNS, batch_reply, safe_string


@mcp.tool()
def set_visibility(
    names: Optional[StrList] = None,
    pattern: str = "",
    action: str = "hide",
) -> str:
    """Show, hide, freeze, or unfreeze objects."""
    if client.native_available:
        try:
            params: dict = {"action": action}
            if names:
                params["names"] = names
            if pattern:
                params["pattern"] = pattern
            response = client.send_command(_json.dumps(params), cmd_type="native:set_visibility")
            return response.get("result", "")
        except RuntimeError:
            pass

    # ── MAXScript fallback (TCP) ──────────────────────────────────
    action = action.lower().strip()

    if action == "hide":
        prop_line = "obj.isHidden = true"
    elif action == "show":
        prop_line = "obj.isHidden = false"
    elif action == "toggle":
        prop_line = "obj.isHidden = not obj.isHidden"
    elif action == "freeze":
        prop_line = "obj.isFrozen = true"
    elif action == "unfreeze":
        prop_line = "obj.isFrozen = false"
    else:
        return f"Unknown action: {action}. Use hide, show, toggle, freeze, or unfreeze."

    if names:
        # Names that match no object or several objects are skipped; shared
        # names are reported.
        name_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in names) + ")"
        collect_line = f"""local nameList = {name_arr}
            local matched = for n in nameList collect (mcpBatchNode n missing ambiguous)
            matched = for obj in matched where obj != undefined collect obj"""
    elif pattern:
        safe_pat = safe_string(pattern)
        collect_line = f"""local matched = for obj in objects where matchPattern obj.name pattern:"{safe_pat}" collect obj"""
    else:
        return "At least one of names or pattern must be provided."

    maxscript = f"""(
        {NODE_LOOKUP_FNS}
        local missing = #()
        local ambiguous = #()
        {collect_line}
        if matched.count == 0 and ambiguous.count > 0 do mcpThrowUnresolved missing ambiguous "nothing was changed"
        local count = 0
        for obj in matched do (
            {prop_line}
            count += 1
        )
        "{action}: " + (count as string) + " objects" + (mcpAmbiguousReply ambiguous)
    )"""
    response = client.send_command(maxscript)
    return batch_reply(response.get("result", ""))
