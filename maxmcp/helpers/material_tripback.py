"""Structured tripback for material_class on texture import / laydown tools."""

from __future__ import annotations

from typing import Any

from ..tools._pbr_material_builder import RENDERER_LABELS

# One row per wired PBR renderer. ask_as = values users/agents can pass to material_class.
PBR_RENDERER_REGISTRY: list[dict[str, Any]] = [
    {
        "renderer": "openpbr",
        "label": RENDERER_LABELS["openpbr"],
        "max_class": "OpenPBRMaterial",
        "ask_as": ["OpenPBRMaterial", "OpenPBR_Material", "OpenPBR_Mtl", "openpbr"],
        "default": True,
    },
    {
        "renderer": "physical",
        "label": RENDERER_LABELS["physical"],
        "max_class": "PhysicalMaterial",
        "ask_as": ["PhysicalMaterial", "physical"],
    },
    {
        "renderer": "arnold",
        "label": RENDERER_LABELS["arnold"],
        "max_class": "ai_standard_surface",
        "ask_as": ["ai_standard_surface", "arnold", "standard_surface"],
    },
    {
        "renderer": "redshift",
        "label": RENDERER_LABELS["redshift"],
        "max_class": "RS_Standard_Material",
        "ask_as": ["RS_Standard_Material", "redshift"],
    },
    {
        "renderer": "vray",
        "label": RENDERER_LABELS["vray"],
        "max_class": "VRayMtl",
        "ask_as": ["VRayMtl", "vray", "v-ray"],
    },
    {
        "renderer": "materialx",
        "label": RENDERER_LABELS["materialx"],
        "max_class": "OpenPBRMaterial",
        "ask_as": ["MaterialX", "materialx", "mtlx"],
    },
    {
        "renderer": "octane_standard",
        "label": RENDERER_LABELS["octane_standard"],
        "max_class": "Std_Surface_Mtl",
        "ask_as": ["octane", "octane_standard", "Std_Surface_Mtl", "std_surface_mtl"],
    },
    {
        "renderer": "octane_pbr",
        "label": RENDERER_LABELS["octane_pbr"],
        "max_class": "Open_PBR_Surf__Mtl",
        "ask_as": ["octane_pbr", "Open_PBR_Surf__Mtl"],
    },
    {
        "renderer": "octane_universal",
        "label": RENDERER_LABELS["octane_universal"],
        "max_class": "Universal_material",
        "ask_as": ["octane_universal", "Universal_material"],
    },
]

_PBR_TOOLS = (
    "smart_import, palette_laydown(slot_content=pbr_material), create_material_from_textures"
)


def supported_pbr_renderer_keys() -> list[str]:
    return [entry["renderer"] for entry in PBR_RENDERER_REGISTRY]


def material_class_hint(
    *,
    tool: str | None = None,
    applies_to: str = _PBR_TOOLS,
) -> dict[str, Any]:
    renderers = list(PBR_RENDERER_REGISTRY)
    primary_ask_as = [entry["ask_as"][0] for entry in renderers]

    return {
        "param": "material_class",
        "applies_to": applies_to,
        "default": "OpenPBRMaterial",
        "renderers": renderers,
        "supported_material_classes": primary_ask_as,
        "summary": (
            "OpenPBR (default), Physical, Arnold, Redshift, V-Ray, MaterialX, "
            "Octane (standard / pbr / universal)"
        ),
    }


def unsupported_material_class_result(
    requested: str,
    *,
    tool: str | None = None,
) -> dict[str, Any]:
    hint = material_class_hint(tool=tool)
    return {
        "status": "error",
        "error": f"Unsupported material_class: {requested!r}",
        "material_class": requested,
        "hint": hint,
        "supported_material_classes": hint["supported_material_classes"],
    }


def wrap_material_tool_result(
    message: str,
    *,
    material_class: str,
    renderer: str,
    tool: str | None = None,
    include_hint: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    resolved_class = (material_class or "").strip() or "OpenPBRMaterial"
    label = RENDERER_LABELS.get(renderer, renderer)
    payload: dict[str, Any] = {
        "message": message,
        "material_class": resolved_class,
        "material_renderer": renderer,
        "material_renderer_label": label,
        **extra,
    }
    if include_hint:
        hint = material_class_hint(tool=tool)
        payload["hint"] = hint
        payload["supported_material_classes"] = hint["supported_material_classes"]
    return payload
