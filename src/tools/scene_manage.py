from ..server import mcp, client


@mcp.tool()
def manage_scene(action: str) -> str:
    """Manage the 3ds Max scene state.

    Args:
        action: One of:
            - "hold": Snapshot current state (holdMaxFile)
            - "fetch": Restore held state (fetchMaxFile quiet:true)
            - "reset": New empty scene (resetMaxFile #noPrompt)
            - "save": Save current file
            - "info": Get scene info (file path, object count, poly count)

    Returns confirmation or scene info.
    """
    action = action.lower().strip()

    if action == "hold":
        maxscript = """(
            holdMaxFile()
            "Hold saved successfully"
        )"""
    elif action == "fetch":
        maxscript = """(
            fetchMaxFile quiet:true
            "Fetched (restored) held state"
        )"""
    elif action == "reset":
        maxscript = """(
            resetMaxFile #noPrompt
            "Scene reset to empty"
        )"""
    elif action == "save":
        maxscript = """(
            local fp = maxFilePath + maxFileName
            if fp == "" then (
                "No file path set — use File > Save As first"
            ) else (
                saveMaxFile fp
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

    response = client.send_command(maxscript)
    return response.get("result", "")
