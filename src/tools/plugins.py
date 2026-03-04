"""Generic plugin discovery, inspection, and manifest tools."""

from __future__ import annotations

import json
import re
from typing import Any

from src.helpers.maxscript import safe_string

from ..server import client, mcp


PLUGIN_INDEX_RESOURCE_URI = "resource://3dsmax-mcp/plugins/index"
PLUGIN_MANIFEST_RESOURCE_URI = "resource://3dsmax-mcp/plugins/{plugin_name}/manifest"
PLUGIN_GUIDE_RESOURCE_URI = "resource://3dsmax-mcp/plugins/{plugin_name}/guide"
PLUGIN_RECIPES_RESOURCE_URI = "resource://3dsmax-mcp/plugins/{plugin_name}/recipes"
PLUGIN_GOTCHAS_RESOURCE_URI = "resource://3dsmax-mcp/plugins/{plugin_name}/gotchas"


PLUGIN_OVERLAYS: dict[str, dict[str, Any]] = {
    "tyflow": {
        "name": "tyFlow",
        "aliases": ["tyflow", "ty flow"],
        "markers": ["tyflow", "tymesher", "tycache", "tyselect"],
        "capability_key": "tyFlow",
        "entry_classes": ["tyFlow"],
        "workflow_tools": ["create_tyflow_basic_verified", "create_tyflow_scatter_from_objects_verified"],
        "recipes": [
            "Create a flow object and inspect its current graph before editing.",
            "Use instance inspection before touching nested event/operator structures.",
            "Prefer verified recipes for common source, surface, and randomness workflows.",
        ],
        "gotchas": [
            "Nested event/operator structures may not be fully discoverable from generic property reflection alone.",
            "Runtime class discovery is reliable; deep graph wiring still needs curated adapters.",
        ],
    },
    "forestpack": {
        "name": "Forest Pack",
        "aliases": ["forest pack", "forestpack", "forest"],
        "markers": ["forestpro", "forestlite", "forest"],
        "capability_key": "forestPack",
        "entry_classes": ["Forest_Pro", "Forest_Lite"],
        "recipes": [
            "Inspect existing scatter objects before changing sources or surfaces.",
            "Verify distribution surfaces and geometry references after edits.",
        ],
        "gotchas": [
            "Some scatter setup is more stable through dedicated adapters than generic property writes.",
        ],
    },
    "railclone": {
        "name": "RailClone",
        "aliases": ["railclone", "rail clone"],
        "markers": ["railclone"],
        "capability_key": "railClone",
        "entry_classes": ["RailClone_Pro"],
        "recipes": [
            "Inspect the live generator object and its property surface before changing rules.",
        ],
        "gotchas": [
            "Generator rule graphs are likely to need curated recipes beyond generic reflection.",
        ],
    },
    "phoenixfd": {
        "name": "Phoenix FD",
        "aliases": ["phoenix fd", "phoenixfd", "phoenix"],
        "markers": ["phoenixfd", "phoenix"],
        "capability_key": "phoenixFD",
        "entry_classes": ["PhoenixFDLiquid"],
        "recipes": [
            "Inspect simulation objects and supporting nodes before applying parameter changes.",
        ],
        "gotchas": [
            "Simulation systems often expose UI-driven state that generic reflection cannot fully explain.",
        ],
    },
}

SUPERCLASS_SOURCES: tuple[dict[str, str], ...] = (
    {"label": "GeometryClass", "expr": "GeometryClass.classes", "category": "geometry"},
    {"label": "Modifier", "expr": "Modifier.classes", "category": "modifier"},
    {"label": "Material", "expr": "Material.classes", "category": "material"},
    {"label": "TextureMap", "expr": "TextureMap.classes", "category": "texturemap"},
    {"label": "Helper", "expr": "Helper.classes", "category": "helper"},
    {"label": "Light", "expr": "Light.classes", "category": "light"},
    {"label": "Camera", "expr": "Camera.classes", "category": "camera"},
    {"label": "RendererClass", "expr": "RendererClass.classes", "category": "renderer"},
)

SUPERCLASS_ALIASES: dict[str, dict[str, str]] = {
    "geometryclass": SUPERCLASS_SOURCES[0],
    "geometry": SUPERCLASS_SOURCES[0],
    "object": SUPERCLASS_SOURCES[0],
    "modifier": SUPERCLASS_SOURCES[1],
    "modifiers": SUPERCLASS_SOURCES[1],
    "material": SUPERCLASS_SOURCES[2],
    "materials": SUPERCLASS_SOURCES[2],
    "texturemap": SUPERCLASS_SOURCES[3],
    "texture": SUPERCLASS_SOURCES[3],
    "textures": SUPERCLASS_SOURCES[3],
    "helper": SUPERCLASS_SOURCES[4],
    "helpers": SUPERCLASS_SOURCES[4],
    "light": SUPERCLASS_SOURCES[5],
    "lights": SUPERCLASS_SOURCES[5],
    "camera": SUPERCLASS_SOURCES[6],
    "cameras": SUPERCLASS_SOURCES[6],
    "rendererclass": SUPERCLASS_SOURCES[7],
    "renderer": SUPERCLASS_SOURCES[7],
    "renderers": SUPERCLASS_SOURCES[7],
}


def _load_json(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _find_overlay(plugin_name: str) -> dict[str, Any] | None:
    norm = _normalize(plugin_name)
    if not norm:
        return None

    for key, overlay in PLUGIN_OVERLAYS.items():
        candidates = [key, overlay["name"], *overlay.get("aliases", [])]
        if any(_normalize(candidate) == norm for candidate in candidates):
            return overlay
    return None


def _plugin_terms(plugin_name: str) -> list[str]:
    overlay = _find_overlay(plugin_name)
    if overlay:
        candidates = [
            plugin_name,
            overlay["name"],
            *overlay.get("aliases", []),
            *overlay.get("markers", []),
            *overlay.get("entry_classes", []),
        ]
    else:
        candidates = [plugin_name]

    terms = {_normalize(candidate) for candidate in candidates if _normalize(candidate)}
    return sorted(terms, key=len, reverse=True)


def _all_plugin_terms() -> list[str]:
    terms = set()
    for overlay in PLUGIN_OVERLAYS.values():
        for value in [overlay["name"], *overlay.get("aliases", []), *overlay.get("markers", []), *overlay.get("entry_classes", [])]:
            norm = _normalize(str(value))
            if norm:
                terms.add(norm)
    return sorted(terms, key=len, reverse=True)


def _guess_plugin_family(class_name: str) -> str | None:
    norm = _normalize(class_name)
    for overlay in PLUGIN_OVERLAYS.values():
        markers = [_normalize(marker) for marker in overlay.get("markers", [])]
        if any(marker and marker in norm for marker in markers):
            return str(overlay["name"])
    return None


def _class_matches_plugin(class_name: str, plugin_name: str) -> bool:
    terms = _plugin_terms(plugin_name)
    if not terms:
        return False

    class_norm = _normalize(class_name)
    return any(term in class_norm for term in terms)


def _category_kind(classes: list[dict[str, Any]]) -> str:
    categories = sorted({str(item.get("category", "")) for item in classes if item.get("category")})
    if not categories:
        return "unknown"
    if len(categories) == 1:
        return categories[0]
    return "mixed"


def _category_summary(classes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in classes:
        category = str(item.get("category", "unknown"))
        counts[category] = counts.get(category, 0) + 1
    return counts


def _runtime_class_entry(name: str, superclass: str, category: str) -> dict[str, str]:
    return {
        "name": name,
        "superclass": superclass,
        "category": category,
        "plugin": _guess_plugin_family(name),
    }


def _fetch_runtime_classes(
    superclass: str = "",
    filter_terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    if superclass:
        source = SUPERCLASS_ALIASES.get(_normalize(superclass))
        if source is None:
            return []
        sources = [source]
    else:
        sources = list(SUPERCLASS_SOURCES)

    if filter_terms is None:
        filter_terms = _all_plugin_terms()

    terms_expr = ", ".join(f'"{safe_string(term)}"' for term in filter_terms if term)

    body = []
    for source in sources:
        body.append(
            "for c in {expr} do maybeAddClass c \"{label}\" \"{category}\"".format(
                expr=source["expr"],
                label=source["label"],
                category=source["category"],
            )
        )

    maxscript = """(
        local esc = MCP_Server.escapeJsonString
        local filterTerms = #({terms_expr})
        local result = "["
        local first = true

        fn classMatches className terms = (
            if terms.count == 0 then return true
            local lowerName = toLower className
            for term in terms where term != "" do (
                if (findString lowerName term) != undefined do return true
            )
            false
        )

        fn addClass cls superclassName categoryName = (
            if not first do result += ","
            first = false
            result += "{\\"name\\":\\"" + (esc (cls as string)) + "\\""
            result += ",\\"superclass\\":\\"" + (esc superclassName) + "\\""
            result += ",\\"category\\":\\"" + (esc categoryName) + "\\"}"
        )

        fn maybeAddClass cls superclassName categoryName = (
            local className = cls as string
            if classMatches className filterTerms do addClass cls superclassName categoryName
        )

        {body}

        result += "]"
        result
    )""".replace("{body}", "\n        ".join(body)).replace("{terms_expr}", terms_expr)

    response = client.send_command(maxscript)
    raw = response.get("result", "[]")
    classes = _load_json(raw, [])
    result = []
    for item in classes:
        if not isinstance(item, dict):
            continue
        result.append(
            _runtime_class_entry(
                str(item.get("name", "")),
                str(item.get("superclass", "")),
                str(item.get("category", "")),
            )
        )
    return result


def _filter_runtime_classes(
    classes: list[dict[str, Any]],
    plugin_name: str = "",
    class_limit: int = 200,
) -> tuple[list[dict[str, Any]], bool]:
    if plugin_name:
        filtered = [item for item in classes if _class_matches_plugin(str(item.get("name", "")), plugin_name)]
    else:
        filtered = [item for item in classes if item.get("plugin")]

    filtered.sort(key=lambda item: (str(item.get("plugin", "")), str(item.get("name", "")).lower()))
    truncated = len(filtered) > class_limit
    return filtered[:class_limit], truncated


def _get_scene_instance_counts(class_names: list[str]) -> dict[str, int]:
    if not class_names:
        return {}

    names_expr = ", ".join(f'"{safe_string(name)}"' for name in class_names)
    maxscript = f"""(
        local esc = MCP_Server.escapeJsonString
        local classNames = #({names_expr})
        local result = "{{"
        local first = true
        for className in classNames do (
            local count = 0
            try (
                local cls = execute className
                if cls != undefined do count = (getclassinstances cls).count
            ) catch ()
            if not first do result += ","
            first = false
            result += "\\"" + (esc className) + "\\":" + (count as string)
        )
        result += "}}"
        result
    )"""
    response = client.send_command(maxscript)
    return _load_json(response.get("result", "{}"), {})


def _fetch_showclass_lines(class_name: str) -> list[str]:
    safe = safe_string(class_name)
    maxscript = f"""(
        local esc = MCP_Server.escapeJsonString
        local ss = stringstream ""
        try (showClass "{safe}.*" to:ss) catch ()
        seek ss 0
        local result = "["
        local first = true
        while not eof ss do (
            local line = readline ss
            if not first do result += ","
            first = false
            result += "\\"" + (esc line) + "\\""
        )
        result += "]"
        result
    )"""
    response = client.send_command(maxscript)
    return _load_json(response.get("result", "[]"), [])


def _property_category(property_name: str, declared_type: str) -> str:
    prop_norm = _normalize(property_name)
    type_norm = _normalize(declared_type)

    if "bool" in type_norm:
        return "bool"
    if "string" in type_norm or "filename" in type_norm:
        return "string"
    if "texture" in type_norm or "map" in type_norm:
        return "texturemap"
    if "material" in type_norm:
        return "material"
    if "color" in type_norm:
        return "color"
    if "node" in type_norm and "array" in type_norm:
        return "node_array"
    if "node" in type_norm or "object" in type_norm or prop_norm.endswith("node"):
        return "node"
    if "array" in type_norm or prop_norm.endswith("list"):
        return "array"
    if any(token in type_norm for token in ("int", "float", "double", "percent", "worldunit", "angle", "time")):
        if prop_norm.endswith("mode") or prop_norm.endswith("type"):
            return "enum_like"
        return "numeric"
    if prop_norm.endswith("mode") or prop_norm.endswith("type"):
        return "enum_like"
    return "unknown"


def _parse_showclass_lines(lines: list[str]) -> dict[str, Any]:
    if not lines:
        return {
            "header": "",
            "class_name": "",
            "superclass": "",
            "properties": [],
            "warnings": ["showClass returned no lines for this class."],
        }

    header = lines[0].strip()
    match = re.match(r"^(\S+)\s*:\s*(\S+)", header)
    class_name = match.group(1) if match else ""
    superclass = match.group(2) if match else ""
    properties = []
    warnings: list[str] = []

    for line in lines[1:]:
        prop_match = re.match(r"^\s*\.([A-Za-z0-9_]+)\s*:\s*(.+?)\s*$", line)
        if not prop_match:
            continue
        prop_name = prop_match.group(1)
        declared_type = prop_match.group(2)
        properties.append({
            "name": prop_name,
            "declaredType": declared_type,
            "category": _property_category(prop_name, declared_type),
            "source": "showClass",
            "inferred": True,
        })

    if not properties:
        warnings.append("Property reflection was empty; this class may expose little through showClass.")

    return {
        "header": header,
        "class_name": class_name,
        "superclass": superclass,
        "properties": properties,
        "warnings": warnings,
    }


def _recommended_tools(category: str) -> list[str]:
    tools = [
        "discover_plugin_surface",
        "list_plugin_classes",
        "inspect_plugin_class",
        "inspect_plugin_instance",
        "get_plugin_manifest",
        "inspect_properties",
    ]
    if category == "modifier":
        tools.extend(["add_modifier_verified", "set_modifier_state_verified"])
    elif category in {"geometry", "helper", "camera", "light"}:
        tools.extend(["create_object_verified", "transform_object_verified", "set_object_property_verified"])
    elif category in {"material", "texturemap"}:
        tools.extend(["assign_material_verified", "set_material_verified", "get_material_slots"])
    else:
        tools.append("execute_maxscript")
    return tools


def _plugin_guide_markdown(plugin_name: str) -> str:
    manifest = _build_manifest(plugin_name)
    plugin = manifest.get("plugin", plugin_name)
    lines = [
        f"# {plugin}",
        "",
        f"- Installed: `{manifest.get('installed', False)}`",
        f"- Category mix: `{manifest.get('category', 'unknown')}`",
        f"- Related class count: `{manifest.get('classCount', 0)}`",
        "",
        "## Entry Classes",
    ]

    entry_classes = manifest.get("entryClasses", [])
    if entry_classes:
        lines.extend([f"- `{item}`" for item in entry_classes])
    else:
        lines.append("- None detected")

    lines.extend(["", "## Recommended Tools"])
    lines.extend([f"- `{item}`" for item in manifest.get("recommendedTools", [])])

    lines.extend(["", "## Recipes"])
    recipes = manifest.get("recipes", [])
    if recipes:
        lines.extend([f"- {item}" for item in recipes])
    else:
        lines.append("- No curated recipes yet.")

    lines.extend(["", "## Gotchas"])
    gotchas = manifest.get("gotchas", [])
    if gotchas:
        lines.extend([f"- {item}" for item in gotchas])
    else:
        lines.append("- No curated gotchas yet.")

    lines.extend(["", "## Related Classes"])
    related_classes = manifest.get("relatedClasses", [])
    if related_classes:
        lines.extend([f"- `{item}`" for item in related_classes])
    else:
        lines.append("- No related classes detected.")

    return "\n".join(lines)


def _plugin_recipe_markdown(plugin_name: str) -> str:
    manifest = _build_manifest(plugin_name)
    plugin = manifest.get("plugin", plugin_name)
    recipes = manifest.get("recipes", [])
    lines = [f"# {plugin} Recipes", ""]
    if recipes:
        lines.extend([f"- {item}" for item in recipes])
    else:
        lines.append("- No curated recipes yet.")
    return "\n".join(lines)


def _plugin_gotchas_markdown(plugin_name: str) -> str:
    manifest = _build_manifest(plugin_name)
    plugin = manifest.get("plugin", plugin_name)
    gotchas = manifest.get("gotchas", [])
    lines = [f"# {plugin} Gotchas", ""]
    if gotchas:
        lines.extend([f"- {item}" for item in gotchas])
    else:
        lines.append("- No curated gotchas yet.")
    return "\n".join(lines)


def _build_manifest(plugin_name: str) -> dict[str, Any]:
    overlay = _find_overlay(plugin_name)
    classes = _fetch_runtime_classes(filter_terms=_plugin_terms(plugin_name))
    filtered, truncated = _filter_runtime_classes(classes, plugin_name=plugin_name, class_limit=200)
    capabilities = _load_json(get_plugin_capabilities(), {})

    display_name = overlay["name"] if overlay else plugin_name.strip()
    installed = False
    if overlay and overlay.get("capability_key"):
        installed = bool(capabilities.get("plugins", {}).get(str(overlay["capability_key"]), False))
    elif filtered:
        installed = True

    class_names = [str(item.get("name", "")) for item in filtered]
    scene_instances = _get_scene_instance_counts(class_names[:20])
    categories = _category_summary(filtered)
    category = _category_kind(filtered)
    recommended_tools = _recommended_tools(category)
    if overlay:
        for tool_name in overlay.get("workflow_tools", []):
            if tool_name not in recommended_tools:
                recommended_tools.append(tool_name)

    if overlay:
        entry_classes = [cls for cls in overlay.get("entry_classes", []) if cls in class_names] or list(overlay.get("entry_classes", []))
        aliases = list(overlay.get("aliases", []))
        recipes = list(overlay.get("recipes", []))
        gotchas = list(overlay.get("gotchas", []))
    else:
        entry_classes = class_names[:3]
        aliases = [plugin_name]
        recipes = []
        gotchas = ["Generic manifest only; no curated overlay exists for this plugin yet."]

    return {
        "plugin": display_name,
        "installed": installed,
        "aliases": aliases,
        "entryClasses": entry_classes,
        "relatedClasses": class_names,
        "classCount": len(filtered),
        "truncated": truncated,
        "category": category,
        "categoryCounts": categories,
        "sceneInstances": scene_instances,
        "recommendedTools": recommended_tools,
        "recipes": recipes,
        "gotchas": gotchas,
        "generatedFrom": {
            "capabilities": True,
            "runtimeClassScan": True,
            "curatedOverlay": bool(overlay),
        },
    }


@mcp.tool()
def list_plugin_classes(
    plugin_name: str = "",
    superclass: str = "",
    limit: int = 200,
) -> str:
    """List classes likely tied to a plugin or superclass family."""
    classes = _fetch_runtime_classes(
        superclass=superclass,
        filter_terms=_plugin_terms(plugin_name) if plugin_name else None,
    )
    filtered, truncated = _filter_runtime_classes(classes, plugin_name=plugin_name, class_limit=max(1, limit))
    return json.dumps({
        "plugin": plugin_name or None,
        "superclass": superclass or None,
        "count": len(filtered),
        "truncated": truncated,
        "classes": filtered,
    })


@mcp.tool()
def discover_plugin_surface(
    plugin_name: str = "",
    class_limit: int = 100,
) -> str:
    """Discover plugin-related classes and summarize likely entry points."""
    capabilities = _load_json(get_plugin_capabilities(), {})

    if plugin_name:
        classes = _fetch_runtime_classes(filter_terms=_plugin_terms(plugin_name))
        filtered, truncated = _filter_runtime_classes(classes, plugin_name=plugin_name, class_limit=max(1, class_limit))
        overlay = _find_overlay(plugin_name)
        scene_instances = _get_scene_instance_counts([str(item.get("name", "")) for item in filtered[:20]])
        installed = bool(filtered)
        if overlay and overlay.get("capability_key"):
            installed = bool(capabilities.get("plugins", {}).get(str(overlay["capability_key"]), False))

        return json.dumps({
            "plugin": overlay["name"] if overlay else plugin_name,
            "installed": installed,
            "families": sorted({item.get("category") for item in filtered if item.get("category")}),
            "entryClasses": [item["name"] for item in filtered if item.get("name") in (overlay or {}).get("entry_classes", [])][:10],
            "relatedClasses": [item["name"] for item in filtered],
            "sceneInstances": scene_instances,
            "truncated": truncated,
            "notes": [] if filtered else ["No matching runtime classes were found for this plugin filter."],
        })

    plugin_summaries = []
    for overlay in PLUGIN_OVERLAYS.values():
        classes = _fetch_runtime_classes(filter_terms=_plugin_terms(overlay["name"]))
        filtered, truncated = _filter_runtime_classes(classes, plugin_name=overlay["name"], class_limit=max(1, class_limit))
        scene_instances = _get_scene_instance_counts([str(item.get("name", "")) for item in filtered[:10]])
        installed = bool(capabilities.get("plugins", {}).get(str(overlay.get("capability_key", "")), False))
        plugin_summaries.append({
            "plugin": overlay["name"],
            "installed": installed or bool(filtered),
            "families": sorted({item.get("category") for item in filtered if item.get("category")}),
            "entryClasses": [item["name"] for item in filtered if item.get("name") in overlay.get("entry_classes", [])],
            "relatedClassCount": len(filtered),
            "sceneInstances": scene_instances,
            "truncated": truncated,
        })

    plugin_summaries.sort(key=lambda item: str(item["plugin"]).lower())
    return json.dumps({
        "plugins": plugin_summaries,
        "maxVersion": capabilities.get("maxVersion"),
        "renderer": capabilities.get("renderer"),
    })


@mcp.tool()
def inspect_plugin_class(
    class_name: str,
    include_methods: bool = True,
    include_properties: bool = True,
) -> str:
    """Inspect a plugin class using runtime class scans plus showClass reflection."""
    classes = _fetch_runtime_classes(filter_terms=[_normalize(class_name)])
    matched = next((item for item in classes if _normalize(str(item.get("name", ""))) == _normalize(class_name)), None)
    if matched is None:
        return json.dumps({"error": f"Unknown class: {class_name}"})

    parsed = _parse_showclass_lines(_fetch_showclass_lines(class_name))
    category = str(matched.get("category", "unknown"))
    plugin_guess = matched.get("plugin")
    warnings = list(parsed.get("warnings", []))
    if include_methods:
        warnings.append("Method reflection is not implemented yet; this class view is property-first.")

    return json.dumps({
        "class": matched["name"],
        "superclass": parsed.get("superclass") or matched.get("superclass"),
        "category": category,
        "plugin": plugin_guess,
        "creatable": category != "renderer",
        "constructorInference": {
            "creatable": category != "renderer",
            "inferred": True,
            "recommendedTooling": _recommended_tools(category),
        },
        "properties": parsed.get("properties", []) if include_properties else [],
        "methods": [] if include_methods else None,
        "warnings": warnings,
        "reflection": {
            "showClassHeader": parsed.get("header"),
            "propertyCount": len(parsed.get("properties", [])),
        },
    })


@mcp.tool()
def inspect_plugin_constructor(class_name: str) -> str:
    """Return likely creation notes for a plugin class."""
    inspected = _load_json(inspect_plugin_class(class_name, include_methods=False, include_properties=False), {})
    if "error" in inspected:
        return json.dumps(inspected)

    category = str(inspected.get("category", "unknown"))
    class_label = str(inspected.get("class", class_name))
    constructor_form = f"{class_label}"
    post_create = []

    if category == "modifier":
        post_create.append("Attach this class to an existing object modifier stack.")
    elif category == "material":
        constructor_form = f'{class_label} name:"{class_label}_Mat"'
        post_create.append("Assign the material to one or more objects after creation.")
    elif category == "texturemap":
        constructor_form = f"{class_label}"
        post_create.append("Wire the map into a material slot after creation.")
    elif category in {"geometry", "helper", "light", "camera"}:
        post_create.append("Set transform and scene relationships after creation.")
    else:
        post_create.append("Generic reflection cannot guarantee a safe constructor path for this class.")

    if category == "modifier":
        recommended = ["add_modifier_verified", "add_modifier", "inspect_modifier_properties"]
    elif category in {"material", "texturemap"}:
        recommended = ["assign_material_verified", "set_material_verified", "get_material_slots"]
    else:
        recommended = ["create_object_verified", "set_object_property_verified", "inspect_object"]

    return json.dumps({
        "class": class_label,
        "category": category,
        "creatable": bool(inspected.get("creatable", False)),
        "constructorForm": constructor_form,
        "postCreateWiring": post_create,
        "recommendedTools": recommended,
        "warnings": inspected.get("warnings", []),
        "inferred": True,
    })


def _extract_plugin_sources(inspected_object: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    candidates = [
        ("class", inspected_object.get("class")),
        ("baseObject", inspected_object.get("baseObject")),
    ]

    material = inspected_object.get("material")
    if isinstance(material, dict):
        candidates.append(("material", material.get("class")))

    for modifier in inspected_object.get("modifiers", []):
        if isinstance(modifier, dict):
            candidates.append(("modifier", modifier.get("class")))

    for source_type, class_name in candidates:
        if not class_name:
            continue
        plugin = _guess_plugin_family(str(class_name))
        if plugin:
            sources.append({
                "source": source_type,
                "class": str(class_name),
                "plugin": plugin,
            })
    return sources


def _summarize_property_dump(property_dump: dict[str, Any], plugin_name: str, limit: int = 20) -> dict[str, Any]:
    props = property_dump.get("properties", [])
    if not isinstance(props, list):
        return {
            "class": property_dump.get("class"),
            "propertyCount": property_dump.get("propertyCount", 0),
            "interestingProperties": [],
        }

    terms = _plugin_terms(plugin_name)
    interesting = []
    for item in props:
        if not isinstance(item, dict):
            continue
        prop_name = str(item.get("name", ""))
        name_norm = _normalize(prop_name)
        value = str(item.get("value", ""))
        decl_type = str(item.get("declaredType", ""))
        if any(term in name_norm for term in terms) or any(token in _normalize(decl_type) for token in ("node", "array", "bool", "percent", "angle")):
            interesting.append({
                "name": prop_name,
                "value": value,
                "declaredType": decl_type,
                "runtimeType": item.get("runtimeType"),
            })
        if len(interesting) >= limit:
            break

    return {
        "class": property_dump.get("class"),
        "propertyCount": property_dump.get("propertyCount", 0),
        "interestingProperties": interesting,
    }


@mcp.tool()
def inspect_plugin_instance(name: str, detail: str = "normal") -> str:
    """Inspect a live scene instance with plugin-aware summarization."""
    from .inspect import inspect_object, inspect_properties

    inspected_object = _load_json(inspect_object(name), {})
    if not isinstance(inspected_object, dict):
        return json.dumps({"error": f"Could not inspect object: {name}"})
    if "error" in inspected_object:
        return json.dumps(inspected_object)

    plugin_sources = _extract_plugin_sources(inspected_object)
    plugin_name = plugin_sources[0]["plugin"] if plugin_sources else None

    result: dict[str, Any] = {
        "name": name,
        "plugin": plugin_name,
        "pluginSources": plugin_sources,
        "object": inspected_object,
    }

    if detail not in {"summary", "normal", "full"}:
        detail = "normal"

    if detail in {"normal", "full"}:
        object_dump = _load_json(inspect_properties(name, target="object"), {})
        result["objectProperties"] = _summarize_property_dump(object_dump, plugin_name or name)

        base_object = str(inspected_object.get("baseObject", ""))
        node_class = str(inspected_object.get("class", ""))
        if base_object and base_object != node_class:
            base_dump = _load_json(inspect_properties(name, target="baseobject"), {})
            result["baseObjectProperties"] = _summarize_property_dump(base_dump, plugin_name or base_object)

    if detail == "full":
        result["fullObjectProperties"] = _load_json(inspect_properties(name, target="object"), {})
        base_object = str(inspected_object.get("baseObject", ""))
        node_class = str(inspected_object.get("class", ""))
        if base_object and base_object != node_class:
            result["fullBaseObjectProperties"] = _load_json(inspect_properties(name, target="baseobject"), {})

    if plugin_name:
        result["manifest"] = _build_manifest(plugin_name)

    return json.dumps(result)


@mcp.tool()
def get_plugin_manifest(plugin_name: str) -> str:
    """Return a structured plugin manifest derived from live runtime data plus curated hints."""
    return json.dumps(_build_manifest(plugin_name))


@mcp.tool()
def refresh_plugin_manifest(plugin_name: str) -> str:
    """Refresh and return the plugin manifest.

    This currently rebuilds the manifest from live runtime data on every call.
    """
    manifest = _build_manifest(plugin_name)
    manifest["refreshed"] = True
    return json.dumps(manifest)


@mcp.resource(PLUGIN_INDEX_RESOURCE_URI, name="Plugin Index", mime_type="application/json")
def plugin_index_resource() -> str:
    """Installed plugin family summary as an MCP resource."""
    return discover_plugin_surface()


@mcp.resource(PLUGIN_MANIFEST_RESOURCE_URI, name="Plugin Manifest", mime_type="application/json")
def plugin_manifest_resource(plugin_name: str) -> str:
    """Per-plugin manifest as an MCP template resource."""
    return get_plugin_manifest(plugin_name)


@mcp.resource(PLUGIN_GUIDE_RESOURCE_URI, name="Plugin Guide", mime_type="text/markdown")
def plugin_guide_resource(plugin_name: str) -> str:
    """Per-plugin operational guide as an MCP template resource."""
    return _plugin_guide_markdown(plugin_name)


@mcp.resource(PLUGIN_RECIPES_RESOURCE_URI, name="Plugin Recipes", mime_type="text/markdown")
def plugin_recipes_resource(plugin_name: str) -> str:
    """Per-plugin curated recipes as an MCP template resource."""
    return _plugin_recipe_markdown(plugin_name)


@mcp.resource(PLUGIN_GOTCHAS_RESOURCE_URI, name="Plugin Gotchas", mime_type="text/markdown")
def plugin_gotchas_resource(plugin_name: str) -> str:
    """Per-plugin gotchas as an MCP template resource."""
    return _plugin_gotchas_markdown(plugin_name)


# Imported lazily to avoid circular imports at module load time.
from .capabilities import get_plugin_capabilities  # noqa: E402
