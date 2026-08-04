"""Build tool_playground/catalog.json — friendly tool list for the GUI playground."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "maxmcp" / "tools"
OUT_PATH = ROOT / "tool_playground" / "catalog.json"

sys.path.insert(0, str(ROOT))
from scripts.gen_tool_registry import (  # noqa: E402
    DISABLED_MODULES,
    build_schema,
    find_cmd_type,
    first_doc_line,
    is_mcp_tool_decorator,
)
from scripts.gen_tool_smoke import CUSTOM, MUTATE_TOOLS, SKIP_DEFAULT  # noqa: E402

MODULE_CATEGORY = {
    "bridge": "Connection",
    "query_scene": "Scene",
    "capabilities": "Connection",
    "session_context": "Session",
    "scene_query": "Scene",
    "scene_manage": "Scene",
    "objects": "Objects",
    "transform": "Objects",
    "orientation": "Objects",
    "hierarchy": "Objects",
    "selection": "Objects",
    "visibility": "Objects",
    "clone": "Objects",
    "modifiers": "Modifiers",
    "materials": "Materials",
    "material_ops": "Materials",
    "material_network": "Material Network",
    "material_replace": "Materials",
    "palette_laydown": "Materials",
    "smart_import": "Smart Import",
    "inspect": "Inspect",
    "plugins": "Plugins",
    "organize": "Organization",
    "viewport": "Viewport",
    "identify": "Objects",
    "file_access": "Files",
    "learning": "Analysis",
    "controllers": "Controllers",
    "wire_params": "Controllers",
    "execute": "Advanced",
    "chat": "Chat",
    "data_channel": "Data Channel",
    "mcg": "Max Creation Graph",
    "effects": "Effects",
    "floor_plan": "Floor Plan",
    "railclone": "RailClone",
    "render": "Render",
    "scattering": "Scattering",
    "state_sets": "State Sets",
    "tyflow": "tyFlow",
    "tool_test": "Tool Test",
}

# Top-level buckets for the playground sidebar (order matters).
CATEGORY_TO_GROUP: dict[str, str] = {
    "Connection": "Setup",
    "Session": "Setup",
    "Scene": "Scene",
    "Objects": "Objects",
    "Modifiers": "Objects",
    "Organization": "Objects",
    "Materials": "Materials",
    "Material Network": "Materials",
    "Smart Import": "Materials",
    "Inspect": "Inspect",
    "Plugins": "Inspect",
    "Analysis": "Inspect",
    "Controllers": "Animation",
    "Viewport": "Viewport & Render",
    "Render": "Viewport & Render",
    "Files": "Files",
    "tyFlow": "Specialty",
    "RailClone": "Specialty",
    "Floor Plan": "Specialty",
    "Data Channel": "Specialty",
    "Max Creation Graph": "Specialty",
    "Scattering": "Specialty",
    "Effects": "Specialty",
    "State Sets": "Specialty",
    "Advanced": "Advanced",
    "Tool Test": "Advanced",
    "Chat": "Advanced",
}

GROUP_ORDER: list[str] = [
    "Setup",
    "Scene",
    "Objects",
    "Materials",
    "Inspect",
    "Animation",
    "Viewport & Render",
    "Files",
    "Specialty",
    "Advanced",
]

GROUP_HINTS: dict[str, str] = {
    "Setup": "Bridge status, session context",
    "Scene": "Scene queries, instancing, management",
    "Objects": "Create, transform, modifiers, layers",
    "Materials": "Assign, textures, palette laydown, smart import",
    "Inspect": "Object/plugin introspection and analysis",
    "Animation": "Controllers and wire params",
    "Viewport & Render": "Captures and rendering",
    "Files": "External .max inspection and merge",
    "Specialty": "MCG, Data Channel, tyFlow, RailClone, etc.",
    "Advanced": "execute_maxscript, smoke tests, chat",
}

STARTER_TOOLS = [
    "query_scene",
    "get_session_context",
    "get_hierarchy",
    "get_instances",
    "get_dependencies",
    "get_materials",
    "get_bridge_status",
]

HIDDEN_ALIAS_TOOLS = {
    # Kept registered for compatibility; hidden from the manual playground to
    # make inspect_properties(target="modifier") the single visible path.
    "inspect_modifier_properties",
}

def example_for(name: str, schema: dict) -> dict:
    if name in CUSTOM:
        return dict(CUSTOM[name])
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    if not required:
        return {}
    out: dict = {}
    for key in required:
        if key == "name":
            out[key] = "$first_object"
        elif key == "names":
            out[key] = ["$first_object"]
        elif key == "action":
            out[key] = "list" if "manage" in name else "info"
        elif key == "class_name":
            out[key] = "Box"
        elif key == "property_name":
            out[key] = "name"
        elif key == "code":
            out[key] = "1+1"
        elif key == "file_path":
            out[key] = "C:\\\\path\\\\to\\\\scene.max"
        elif key in props:
            ptype = props[key].get("type")
            if ptype == "string":
                out[key] = ""
            elif ptype == "integer":
                out[key] = 1
            elif ptype == "boolean":
                out[key] = False
            elif ptype == "array":
                out[key] = []
            else:
                out[key] = {}
        else:
            out[key] = ""
    return out


def risk_for(name: str) -> str:
    if name in {"mcg_apply_modifier", "mcg_set_node_parameter"}:
        return "changes_scene"
    if name in {
        "mcg_apply_patch",
        "mcg_cleanup_workspace",
        "mcg_compile_graph",
        "mcg_create_graph",
        "mcg_reload_operators",
        "mcg_resolve_class",
        "mcg_restore_checkpoint",
        "mcg_test_tool",
    }:
        return "advanced"
    if name in SKIP_DEFAULT:
        return "advanced"
    if name in MUTATE_TOOLS:
        return "changes_scene"
    if name in STARTER_TOOLS or name.startswith("get_") or name.startswith("inspect_"):
        return "safe"
    if name.startswith("list_") or name.startswith("discover_") or name.startswith("introspect_"):
        return "safe"
    if name in {"execute_maxscript", "render_scene", "merge_from_file", "delete_objects"}:
        return "advanced"
    if name in {"smart_import", "palette_laydown", "create_material_from_textures", "create_shell_material"}:
        return "changes_scene"
    return "read"


def collect_tools() -> list[dict]:
    tools: list[dict] = []
    seen: set[str] = set()
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        module = path.stem
        if module in DISABLED_MODULES:
            continue
        category = MODULE_CATEGORY.get(module, module.replace("_", " ").title())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(is_mcp_tool_decorator(d) for d in node.decorator_list):
                continue
            if node.name in HIDDEN_ALIAS_TOOLS:
                continue
            if node.name in seen:
                continue
            seen.add(node.name)
            schema = build_schema(node)
            cmd_type = find_cmd_type(node, source) or "python"
            tools.append({
                "name": node.name,
                "description": first_doc_line(node) or node.name,
                "category": category,
                "group": CATEGORY_TO_GROUP.get(category, "Other"),
                "module": module,
                "schema": schema,
                "example": example_for(node.name, schema),
                "risk": risk_for(node.name),
                "routing": cmd_type,
                "starter": node.name in STARTER_TOOLS,
            })
    tools.sort(key=lambda t: (t["group"], t["category"], t["name"]))
    return tools


def build_group_index(tools: list[dict]) -> list[dict]:
    """Sidebar index: groups -> categories -> tool names."""
    index: dict[str, dict[str, list[str]]] = {}
    for tool in tools:
        group = tool["group"]
        category = tool["category"]
        index.setdefault(group, {}).setdefault(category, []).append(tool["name"])

    groups: list[dict] = []
    seen_groups = set(GROUP_ORDER)
    for group in GROUP_ORDER:
        categories_map = index.get(group, {})
        if not categories_map:
            continue
        categories = []
        for cat in sorted(categories_map):
            categories.append({
                "name": cat,
                "tools": sorted(categories_map[cat]),
            })
        groups.append({
            "name": group,
            "hint": GROUP_HINTS.get(group, ""),
            "tool_count": sum(len(c["tools"]) for c in categories),
            "categories": categories,
        })

    for group in sorted(index):
        if group in seen_groups:
            continue
        categories_map = index[group]
        categories = [
            {"name": cat, "tools": sorted(categories_map[cat])}
            for cat in sorted(categories_map)
        ]
        groups.append({
            "name": group,
            "hint": "",
            "tool_count": sum(len(c["tools"]) for c in categories),
            "categories": categories,
        })
    return groups


def main() -> int:
    tools = collect_tools()
    groups = build_group_index(tools)
    payload = {
        "version": 2,
        "tool_count": len(tools),
        "starter_tools": STARTER_TOOLS,
        "groups": groups,
        "group_order": GROUP_ORDER,
        "tools": tools,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[gen_tool_catalog] wrote {len(tools)} tools -> {OUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
