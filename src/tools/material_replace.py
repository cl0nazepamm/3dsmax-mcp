"""Batch material replacement tool for 3ds Max.

Replaces materials on objects by reassigning an existing source material
to all objects that currently use a target material.  Useful for unifying
split material assignments (e.g. after partial glTF conversion) or
swapping materials across many objects at once.
"""

import json as _json
from ..server import mcp, client
from ..coerce import DictList
from src.helpers.maxscript import safe_string


@mcp.tool()
def replace_material(
    source_material: str,
    target_material: str,
    preview: bool = False,
) -> str:
    """Replace one material with another across all objects that use it.

    Finds every object whose material name matches *target_material* and
    reassigns it to the material named *source_material*.  The source
    material must already exist on at least one object in the scene.

    Args:
        source_material: Name of the material to apply (must exist in scene).
        target_material: Name of the material to replace / remove.
        preview: If True, only list affected objects without making changes.

    Returns:
        JSON with affected object names, count, and status.
    """
    safe_src = safe_string(source_material)
    safe_tgt = safe_string(target_material)

    if preview:
        maxscript = f"""(
            local tgtObjs = for obj in objects
                where obj.material != undefined
                  and obj.material.name == "{safe_tgt}"
                collect obj.name
            local srcExists = false
            for obj in objects where obj.material != undefined do (
                if obj.material.name == "{safe_src}" do (srcExists = true; exit)
            )
            local names = "["
            for i = 1 to tgtObjs.count do (
                if i > 1 do names += ","
                names += "\\"" + tgtObjs[i] + "\\""
            )
            names += "]"
            "{{" + \
                "\\"source_material\\":\\"" + "{safe_src}" + "\\"," + \
                "\\"target_material\\":\\"" + "{safe_tgt}" + "\\"," + \
                "\\"source_exists\\":" + (if srcExists then "true" else "false") + "," + \
                "\\"affected_count\\":" + (tgtObjs.count as string) + "," + \
                "\\"affected_objects\\":" + names + "," + \
                "\\"preview\\":true" + \
            "}}"
        )"""
    else:
        maxscript = f"""(
            -- Find the source material instance from scene objects
            local srcMat = undefined
            for obj in objects where obj.material != undefined do (
                if obj.material.name == "{safe_src}" do (
                    srcMat = obj.material
                    exit
                )
            )
            if srcMat == undefined then (
                "{{" + \
                    "\\"error\\":\\"source material '{safe_src}' not found on any object\\"," + \
                    "\\"status\\":\\"failed\\"" + \
                "}}"
            ) else (
                local replaced = #()
                for obj in objects where obj.material != undefined do (
                    if obj.material.name == "{safe_tgt}" do (
                        obj.material = srcMat
                        append replaced obj.name
                    )
                )
                local names = "["
                for i = 1 to replaced.count do (
                    if i > 1 do names += ","
                    names += "\\"" + replaced[i] + "\\""
                )
                names += "]"
                "{{" + \
                    "\\"source_material\\":\\"" + "{safe_src}" + "\\"," + \
                    "\\"target_material\\":\\"" + "{safe_tgt}" + "\\"," + \
                    "\\"replaced_count\\":" + (replaced.count as string) + "," + \
                    "\\"replaced_objects\\":" + names + "," + \
                    "\\"status\\":\\"success\\"" + \
                "}}"
            )
        )"""

    response = client.send_command(maxscript)
    return response.get("result", "{}")


@mcp.tool()
def batch_replace_materials(
    replacements: DictList,
    preview: bool = False,
) -> str:
    """Replace multiple materials in a single operation.

    Each entry maps a source material (to keep) to a target material
    (to replace).  Useful for unifying several split assignments at once.

    Args:
        replacements: List of dicts, each with:
            - "source": name of the material to apply
            - "target": name of the material to replace
        preview: If True, only list affected objects without making changes.

    Returns:
        JSON with per-replacement results and an overall summary.
    """
    results = []
    for entry in replacements:
        src = entry.get("source", "")
        tgt = entry.get("target", "")
        if not src or not tgt:
            results.append({"source": src, "target": tgt, "status": "skipped", "error": "missing source or target"})
            continue
        raw = replace_material(source_material=src, target_material=tgt, preview=preview)
        try:
            results.append(_json.loads(raw))
        except _json.JSONDecodeError:
            results.append({"source": src, "target": tgt, "status": "error", "raw": raw})

    total_replaced = sum(r.get("replaced_count", 0) for r in results)
    return _json.dumps({
        "results": results,
        "total_replaced": total_replaced,
        "preview": preview,
    })
