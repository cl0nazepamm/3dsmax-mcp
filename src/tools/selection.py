from typing import Optional
from ..server import mcp, client


def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


@mcp.tool()
def select_objects(
    names: Optional[list[str]] = None,
    pattern: str = "",
    class_name: str = "",
    all: bool = False,
) -> str:
    """Select objects in the 3ds Max scene.

    At least one parameter must be provided. Selection is cleared first.

    Args:
        names: List of specific object names to select.
        pattern: Wildcard pattern (e.g. "Wall*", "*Light*").
        class_name: Select by class name (e.g. "Box", "SpotLight").
        all: If true, select all objects.

    Returns count and names of selected objects.
    """
    if all:
        maxscript = """(
            select objects
            local result = "Selected " + (selection.count as string) + " objects"
            result
        )"""
    elif names:
        name_arr = "#(" + ", ".join(f'"{_safe_name(n)}"' for n in names) + ")"
        maxscript = f"""(
            clearSelection()
            local nameList = {name_arr}
            local found = #()
            for n in nameList do (
                local obj = getNodeByName n
                if obj != undefined do (
                    selectMore obj
                    append found n
                )
            )
            "Selected " + (found.count as string) + " of " + (nameList.count as string) + " objects"
        )"""
    elif pattern:
        safe_pat = _safe_name(pattern)
        maxscript = f"""(
            clearSelection()
            local matched = for obj in objects where matchPattern obj.name pattern:"{safe_pat}" collect obj
            select matched
            "Selected " + (matched.count as string) + " objects matching \\\"" + "{safe_pat}" + "\\\""
        )"""
    elif class_name:
        maxscript = f"""(
            clearSelection()
            local matched = for obj in objects where (classOf obj) as string == "{class_name}" collect obj
            select matched
            "Selected " + (matched.count as string) + " objects of class {class_name}"
        )"""
    else:
        return "At least one parameter (names, pattern, class_name, or all) must be provided."

    response = client.send_command(maxscript)
    return response.get("result", "")
