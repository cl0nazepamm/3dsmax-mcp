"""Shared error → next-tool hints for MCP envelopes."""

from __future__ import annotations

import re
from typing import Any, TypedDict


class ToolHint(TypedDict, total=False):
    message: str
    suggested_tools: list[str]
    next: list[str]


# Keyword patterns → dedicated tools that handle the same intent. Each pattern
# is scanned against user-supplied MAXScript; matches contribute tool names to
# a deduplicated suggestion list returned in the error envelope's hint.
MAXSCRIPT_SUGGESTION_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    # OSL-related failures are particularly opaque and rarely tractable by
    # retrying MAXScript. introspect_osl is the only way to learn the shader's
    # actual parameter names, output channels, and connection rules.
    (
        re.compile(
            r"\bOSLMap\b|\bOSLPath\b|\bOSLAutoUpdate\b|\bUberBitmap2?\b|\.osl\b|"
            r"\bsetOSL\w*\b|\bgetOSL\w*\b",
            re.IGNORECASE,
        ),
        ("introspect_osl", "write_osl_shader"),
    ),
    (
        re.compile(
            r"\b(Box|Sphere|Cylinder|Cone|Plane|Pyramid|Torus|GeoSphere|Teapot|Tube)\b\s*(?:\(|\w+:)",
            re.IGNORECASE,
        ),
        ("create_object",),
    ),
    (
        re.compile(r"\bclassOf\s+\$|\bshowProperties\b|\bgetProperty\b", re.IGNORECASE),
        ("inspect_object", "get_object_properties"),
    ),
    (re.compile(r"\baddModifier\b", re.IGNORECASE), ("add_modifier",)),
    (re.compile(r"\bdeleteModifier\b", re.IGNORECASE), ("remove_modifier",)),
    (re.compile(r"\.material\s*="), ("assign_material",)),
    (
        re.compile(
            r"\bcurrentMaterialLibrary\b|\bloadTempMaterialLibrary\b|"
            r"\bsaveTempMaterialLibrary\b|\bsaveMaterialLibrary\b|\bloadMaterialLibrary\b",
            re.IGNORECASE,
        ),
        ("get_material_library", "backup_material_library"),
    ),
    (
        re.compile(r"\bmeditMaterials\b|\bgetClassName\b.*[Mm]aterial"),
        ("get_materials", "get_material_slots", "get_material_library"),
    ),
    (
        re.compile(r"\.position\s*=|\.rotation\s*=|\.scale\s*=|\bmove\s+\$|\brotate\s+\$|\bscale\s+\$"),
        ("transform_object",),
    ),
    (
        re.compile(r"\bselect\s+\$|\bselectmore\s+\$|\bclearSelection\b", re.IGNORECASE),
        ("select_objects",),
    ),
    (re.compile(r"\.parent\s*="), ("set_parent",)),
    (
        re.compile(r"\.isHidden\s*=|hide\s+\$|unhide\s+\$", re.IGNORECASE),
        ("set_visibility",),
    ),
    (re.compile(r"\.name\s*=\s*\""), ("batch_rename_objects",)),
    (
        re.compile(r"\bcopy\s+\$|\binstance\s+\$|\breference\s+\$", re.IGNORECASE),
        ("clone_objects",),
    ),
    (
        re.compile(r"\bdelete\s+\$|\bdelete\s+\(getNodeByName", re.IGNORECASE),
        ("delete_objects",),
    ),
    (
        re.compile(r"\bILayerManager\b|\bLayerManager\.", re.IGNORECASE),
        ("manage_layers",),
    ),
    (
        re.compile(r"\bgroup\s+selection\b|\bungroup\b|\bopenGroup\b|\bcloseGroup\b", re.IGNORECASE),
        ("manage_groups",),
    ),
    (
        re.compile(r"\brender\s*\(|\bmax\s+quick\s+render\b", re.IGNORECASE),
        ("render_scene",),
    ),
    (
        re.compile(
            r"\bholdMaxFile\b|\bfetchMaxFile\b|\bresetMaxFile\b|\bsaveMaxFile\b",
            re.IGNORECASE,
        ),
        ("manage_scene",),
    ),
    (
        re.compile(r"\bSelectionSets\b|\bnamedSelectionSets\b", re.IGNORECASE),
        ("manage_selection_sets",),
    ),
    (
        re.compile(r"\bData_Channel\b|\bDataChannelModifier\b", re.IGNORECASE),
        ("add_data_channel", "add_dc_script_operator"),
    ),
    (
        re.compile(r"\bgetSubAnim\b|\bsetController\b|\bassignNewController\b", re.IGNORECASE),
        ("assign_controller", "inspect_controller"),
    ),
    (
        re.compile(r"\bparamWire\.connect\b|\bparamwire\.disconnect\b", re.IGNORECASE),
        ("wire_params", "unwire_params"),
    ),
)


# Message patterns applied to any tool error when no tool-authored hint exists.
_MESSAGE_HINT_RULES: tuple[tuple[re.Pattern[str], ToolHint], ...] = (
    (
        re.compile(r"\bnot found\b|\bunknown (object|node|material)\b", re.IGNORECASE),
        {
            "message": (
                "Target was not found. Use query_scene(action=filter) or "
                "query_scene(action=selection) to locate names, then retry."
            ),
            "suggested_tools": ["query_scene", "select_objects"],
        },
    ),
    (
        re.compile(r"blocked by safe mode", re.IGNORECASE),
        {
            "message": (
                "Command blocked by safe mode. Do not retry the same call; "
                "use a dedicated MCP tool or adjust safe_mode settings."
            ),
        },
    ),
    # Deliberately narrow: bare "connect"/"connection"/"claim" also appear in
    # MAXScript errors (Slate node connections, etc.) and must not match.
    (
        re.compile(
            r"named.?pipe|\bbridge\b|\bno claimed\b|\btransport\b",
            re.IGNORECASE,
        ),
        {
            "message": (
                "Bridge/transport issue. Diagnose with get_bridge_status only — "
                "not as a session preamble. Ensure MCP Claim This Max in the target window."
            ),
            "suggested_tools": ["get_bridge_status"],
        },
    ),
    (
        re.compile(r"unknown action:|unsupported |missing (required )?param", re.IGNORECASE),
        {
            "message": "Bad action or parameter. Re-read the tool schema and retry with valid args.",
        },
    ),
)


def suggest_tools_for_maxscript(script: str) -> list[str]:
    """Return dedicated MCP tools that match MAXScript intent patterns."""
    suggestions: list[str] = []
    for pattern, tools in MAXSCRIPT_SUGGESTION_RULES:
        if pattern.search(script):
            for tool in tools:
                if tool not in suggestions:
                    suggestions.append(tool)
    return suggestions


def normalize_hint(raw: Any) -> ToolHint | None:
    """Coerce string / dict / plural ``hints`` into a canonical ToolHint."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        return {"message": text} if text else None
    if isinstance(raw, dict):
        message = raw.get("message")
        suggested = raw.get("suggested_tools")
        next_steps = raw.get("next")
        # Material-class catalogs and other structured hints without message:
        # preserve as-is under a synthetic message when needed.
        hint: ToolHint = {}
        if isinstance(message, str) and message.strip():
            hint["message"] = message.strip()
        elif not suggested and not next_steps:
            # Pass through structured catalogs (e.g. material_class_hint).
            return _json_safe_hint(raw)
        if isinstance(suggested, list):
            tools = [str(t) for t in suggested if t]
            if tools:
                hint["suggested_tools"] = tools
        if isinstance(next_steps, list):
            steps = [str(s) for s in next_steps if s]
            if steps:
                hint["next"] = steps
        # Preserve extra keys from tool-authored structured hints (renderers, etc.)
        for key, value in raw.items():
            if key in {"message", "suggested_tools", "next"}:
                continue
            if value is not None:
                hint[key] = value  # type: ignore[literal-required]
        return hint or None
    if isinstance(raw, list) and raw:
        # Plural "hints" sometimes arrives as a list of strings.
        parts = [str(item).strip() for item in raw if str(item).strip()]
        if not parts:
            return None
        return {"message": "; ".join(parts)}
    return None


def _json_safe_hint(raw: dict[str, Any]) -> ToolHint:
    """Keep non-canonical structured hints (material catalogs) intact."""
    out: ToolHint = {}
    for key, value in raw.items():
        if value is not None:
            out[key] = value  # type: ignore[literal-required]
    return out


def extract_tool_hint(result: Any) -> ToolHint | None:
    """Pull a tool-authored hint from a result dict (``hint`` or ``hints``)."""
    if not isinstance(result, dict):
        return None
    if "hint" in result and result["hint"]:
        return normalize_hint(result["hint"])
    if "hints" in result and result["hints"]:
        return normalize_hint(result["hints"])
    return None


def resolve_error_hint(
    *,
    tool_name: str = "",
    error: dict[str, str] | None = None,
    raw_result: Any = None,
    script: str | None = None,
) -> ToolHint | None:
    """Resolve a hint for an error envelope.

    Tool-authored hints always win. Otherwise apply MAXScript intent rules
    (when ``script`` is provided) then message-pattern rules.
    """
    authored = extract_tool_hint(raw_result)
    if authored is not None:
        return authored

    if script:
        suggested = suggest_tools_for_maxscript(script)
        if suggested:
            return {
                "message": (
                    "execute_maxscript is a fallback. Before retrying the same "
                    "script, consider using a dedicated MCP tool that handles "
                    "this intent directly."
                ),
                "suggested_tools": suggested,
            }

    message = ""
    if error:
        message = str(error.get("message") or "")
    elif isinstance(raw_result, str):
        message = raw_result
    elif isinstance(raw_result, dict):
        message = str(raw_result.get("error") or raw_result.get("message") or "")

    if not message:
        return None

    for pattern, hint in _MESSAGE_HINT_RULES:
        if pattern.search(message):
            # Avoid suggesting get_bridge_status when that tool itself failed.
            if tool_name == "get_bridge_status" and "get_bridge_status" in hint.get(
                "suggested_tools", []
            ):
                return {"message": hint["message"]}
            return dict(hint)

    return None
