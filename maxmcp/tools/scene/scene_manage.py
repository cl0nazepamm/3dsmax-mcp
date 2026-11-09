import json as _json
from ...server import mcp, client


@mcp.tool()
def manage_scene(action: str) -> str:
    """Manage the 3ds Max scene file. action: hold | fetch | reset | save | info.

    save writes the current file path (never Save As); fetch and reset show their prompts.
    """
    action = action.lower().strip()

    if client.native_available:
        payload = _json.dumps({"action": action})
        response = client.send_command(payload, cmd_type="native:manage_scene")
        return response.get("result", "")

    if action == "hold":
        maxscript = """(
            holdMaxFile()
            "Hold saved successfully"
        )"""
    elif action == "fetch":
        # Prompts shown, as in the native handler: Fetch confirms, and Reset asks to save
        # unsaved changes instead of discarding them.
        maxscript = """(
            fetchMaxFile quiet:false
            "Fetched (restored) held state"
        )"""
    elif action == "reset":
        maxscript = """(
            resetMaxFile()
            "Scene reset to empty"
        )"""
    elif action == "save":
        maxscript = """(
            local fp = maxFilePath + maxFileName
            if fp == "" then (
                "Error: Scene was not saved: it has no file path yet. Use File > Save As first."
            ) else if not (saveMaxFile fp) then (
                "Error: 3ds Max failed to save " + fp
            ) else (
                "Saved: " + fp
            )
        )"""
    elif action == "info":
        maxscript = r"""(
            local fp = maxFilePath + maxFileName
            if fp == "" do fp = "(unsaved)"
            local objCount = objects.count
            local polyCount = 0
            for obj in objects do (
                try (
                    local m = snapshotAsMesh obj
                    polyCount += m.numFaces
                    delete m
                ) catch ()
            )
            "{" + \
                "\"filePath\":\"" + (substituteString fp "\\" "\\\\") + "\"," + \
                "\"objectCount\":" + (objCount as string) + "," + \
                "\"polyCount\":" + (polyCount as string) + \
            "}"
        )"""
    else:
        return f"Unknown action: {action}. Use hold, fetch, reset, save, or info."

    # quiet=false keeps a native bridge from running the script in quiet mode, where the
    # prompts above take their default; the TCP listener ignores the field.
    fields = {"quiet": False} if action in ("fetch", "reset") else None
    response = client.send_command(maxscript, request_fields=fields)
    return response.get("result", "")


@mcp.tool()
def undo_last() -> str:
    """Undo the last 3ds Max scene operation."""
    if client.native_available:
        try:
            response = client.send_command("{}", cmd_type="native:undo_last")
            return response.get("result", "")
        except RuntimeError:
            pass

    response = client.send_command('max undo; "Undid last scene operation"')
    return response.get("result", "")
