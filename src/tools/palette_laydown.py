"""Material Editor palette laydown tool."""

from ..server import mcp


@mcp.tool()
def palette_laydown(
    texture_folder: str,
    start_slot: int = 1,
    max_slots: int = 24,
    recursive: bool = False,
    open_editor: bool = True,
    material_prefix: str = "tex_",
    slot_content: str = "material",
    material_class: str = "",
    include_displacement: bool = True,
) -> str:
    """Lay down folder textures into Compact Material Editor palette slots.

    slot_content: material = one OpenPBR preview per bitmap; bitmap = raw
    Bitmaptexture slots; pbr_material/full_pbr = grouped PBR material sets.
    material_class only applies to grouped PBR: OpenPBRMaterial, PhysicalMaterial,
    ai_standard_surface, RS_Standard_Material, or VRayMtl.
    include_displacement controls whether height/displacement maps are wired in
    grouped PBR mode.
    """
    from .material_ops import _palette_laydown_impl

    return _palette_laydown_impl(
        texture_folder=texture_folder,
        start_slot=start_slot,
        max_slots=max_slots,
        recursive=recursive,
        open_editor=open_editor,
        material_prefix=material_prefix,
        slot_content=slot_content,
        material_class=material_class,
        include_displacement=include_displacement,
    )
