import json as _json
from typing import Optional
from ..server import mcp, client
from ..coerce import StrList
from src.helpers.maxscript import safe_string


@mcp.tool()
def set_visibility(
    names: Optional[StrList] = None,
    pattern: str = "",
    action: str = "hide",
) -> str:
    """Show, hide, freeze, or unfreeze objects.

    Args:
        names: List of specific object names. Default empty.
        pattern: Wildcard pattern (e.g. "Light*"). Default empty.
        action: "hide", "show", "toggle", "freeze", or "unfreeze".

    At least one of names or pattern must be provided.
    Returns count of affected objects.
    """
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
        name_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in names) + ")"
        collect_line = f"""local nameList = {name_arr}
            local matched = for n in nameList where (getNodeByName n) != undefined collect (getNodeByName n)"""
    elif pattern:
        safe_pat = safe_string(pattern)
        collect_line = f"""local matched = for obj in objects where matchPattern obj.name pattern:"{safe_pat}" collect obj"""
    else:
        return "At least one of names or pattern must be provided."

    maxscript = f"""(
        {collect_line}
        local count = 0
        for obj in matched do (
            {prop_line}
            count += 1
        )
        "{action}: " + (count as string) + " objects"
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
