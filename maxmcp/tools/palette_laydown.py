"""Material Editor palette laydown tool."""

from ..server import mcp


@mcp.tool()
def palette_laydown(
    texture_folder: str,
    start_slot: int = 1,
    max_slots: int = 24,
    recursive: bool = True,
    open_editor: bool = True,
    material_prefix: str = "tex_",
    slot_content: str = "material",
    material_class: str = "",
    include_displacement: bool = True,
    name_pattern: str = "",
    exclude_pattern: str = "",
    sample_mode: str = "first",
    overflow_mode: str = "truncate",
    random_seed: int | None = None,
) -> str:
    """Lay down folder textures into Compact Material Editor palette slots.

    slot_content: material = one OpenPBR preview per bitmap; bitmap = raw
    Bitmaptexture slots; pbr_material/full_pbr = grouped PBR material sets.
    material_class only applies to grouped PBR (slot_content=pbr_material). Pass any
    value from tripback ``supported_material_classes`` — OpenPBR, Physical, Arnold,
    Redshift, V-Ray, MaterialX, Octane variants, etc.
    include_displacement controls whether height/displacement maps are wired in
    grouped PBR mode.
    name_pattern: optional include glob(s) on texture-set / filename stems,
    comma-separated (e.g. ``*wood*`` or ``oak_*,birch_*``). Empty imports the
    first ``max_slots`` items alphabetically.
    exclude_pattern: optional exclude glob(s) on the same stems (comma-separated);
    anything matching is skipped even if it matched name_pattern — the
    "don't lay that down" filter (e.g. ``*_preview*`` or ``*atlas*,*lowpoly*``).
    recursive scans subfolders (default true).

    sample_mode:
        first — flat folder, first items alphabetically (default).
        random — flat folder, random selection (use random_seed to repeat).
        one_per_subfolder — first texture set / image from each child folder.
        random_per_subfolder — one random set / image per child folder; good for
            large asset libraries with many material categories (one per subfolder).

    overflow_mode:
        truncate — only fill up to max_slots (default).
        palette_then_library — overflow materials go to currentMaterialLibrary
            when there are more picks than Compact palette slots (max 24).
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
        name_pattern=name_pattern,
        exclude_pattern=exclude_pattern,
        sample_mode=sample_mode,
        overflow_mode=overflow_mode,
        random_seed=random_seed,
    )
