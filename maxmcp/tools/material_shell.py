"""Shell Material wrapper — pairs any render + export/viewport materials."""

from __future__ import annotations

from ..helpers.maxscript import safe_string

# MultiOutputChannelTexmapToTexmap output indices for UberBitmap2 (used by PBR builder ORM split).
UBER_OUT_COL = 1
UBER_OUT_R = 2
UBER_OUT_G = 3
UBER_OUT_B = 4

FIND_MATERIAL_FN = """
fn mcp_findMaterialByName matName = (
    if matName == undefined or matName == "" do return undefined
    for m in sceneMaterials where (m.name == matName) do return m
    for obj in objects where obj.material != undefined do (
        if obj.material.name == matName do return obj.material
        if classOf obj.material == Multimaterial do (
            for i = 1 to obj.material.numsubs do (
                local sm = obj.material[i]
                if sm != undefined and sm.name == matName do return sm
            )
        )
    )
    undefined
)
""".strip()


def build_shell_wrap_maxscript(
    shell_name: str,
    render_material: str,
    export_material: str = "",
    assign_to: list[str] | None = None,
    *,
    render_slot: int = 0,
    viewport_slot: int = 1,
    render_var: str = "renderMat",
    export_var: str = "exportMat",
    preface_lines: list[str] | None = None,
) -> str:
    """Build MAXScript that wraps existing or just-created materials in Shell_Material."""
    safe_shell = safe_string(shell_name)
    safe_render = safe_string(render_material)
    safe_export = safe_string(export_material) if export_material else ""

    lines: list[str] = [FIND_MATERIAL_FN, f"local {render_var} = undefined", f"local {export_var} = undefined"]

    if preface_lines:
        lines.extend(preface_lines)
        lines.append(f'if {render_var} == undefined do {render_var} = mcp_findMaterialByName "{safe_render}"')
    else:
        lines.append(f'{render_var} = mcp_findMaterialByName "{safe_render}"')

    if export_material:
        lines.append(f'if {export_var} == undefined do {export_var} = mcp_findMaterialByName "{safe_export}"')

    lines.extend([
        f'if {render_var} == undefined do throw ("Render material not found: {safe_render}")',
        "shell = Shell_Material()",
        f'shell.name = "{safe_shell}"',
        f"shell.originalMaterial = {render_var}",
        f"if {export_var} != undefined do shell.bakedMaterial = {export_var}",
        f"shell.renderMtlIndex = {int(render_slot)}",
        f"shell.viewportMtlIndex = {int(viewport_slot)}",
        "assignCount = 0",
    ])

    if assign_to:
        names_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in assign_to) + ")"
        lines.append(f"nameList = {names_arr}")
        lines.append(
            f"for n in nameList do (obj = getNodeByName n; "
            f"if obj != undefined then (obj.material = shell; assignCount += 1))"
        )

    lines.extend([
        'resultJson = "{"',
        'resultJson += "\\"workflow\\":\\"shell_wrap\\","',
        'resultJson += "\\"shell_name\\":\\"" + shell.name + "\\","',
        f'resultJson += "\\"render_material\\":\\"" + {render_var}.name + "\\","',
        f'resultJson += "\\"render_material_class\\":\\"" + ((classOf {render_var}) as string) + "\\","',
        f'if {export_var} != undefined do (',
        f'    resultJson += "\\"export_material\\":\\"" + {export_var}.name + "\\","',
        f'    resultJson += "\\"export_material_class\\":\\"" + ((classOf {export_var}) as string) + "\\","',
        ")",
        'resultJson += "\\"assigned_count\\":" + (assignCount as string) + ","',
        'resultJson += "\\"status\\":\\"success\\""',
        'resultJson += "}"',
        "resultJson",
    ])

    return "(\n    " + "\n    ".join(lines) + "\n)"


def _extract_material_builder_body(full_script: str, *, mat_var: str) -> list[str]:
    """Strip assign/return tail from a PBR builder script; rename ``mat`` to *mat_var*."""
    body = full_script.strip()
    if body.startswith("("):
        body = body[1:].strip()
    if body.endswith(")"):
        body = body[:-1].strip()

    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("nameList ="):
            break
        if stripped.startswith("for n in nameList"):
            break
        if stripped.startswith("assignCount"):
            continue
        if stripped == "summary":
            break
        if stripped.startswith("summary +="):
            break
        kept.append(line.replace("mat =", f"{mat_var} =").replace("mat.", f"{mat_var}."))

    return kept
