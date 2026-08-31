"""Generate native/generated/tool_smoke_cases.inc for in-Max live tool smoke tests.

Walks the same @mcp.tool() surface as gen_tool_registry.py and emits C++ smoke
cases with safe default inputs. Run from repo root:

    python scripts/gen_tool_smoke.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "native" / "generated" / "tool_smoke_cases.inc"

sys.path.insert(0, str(ROOT))
from scripts.gen_tool_registry import TOOLS_DIR, extract_tools  # noqa: E402

SMOKE_TARGET = "MCP_SmokeTarget"
SMOKE_SPAWN = "MCP_SmokeSpawn"

# Tier constants — must match tool_test_handlers.cpp
TIER_READ = 0
TIER_FIXTURE = 1
TIER_MUTATE = 2

FLAG_SKIP_DEFAULT = 1
FLAG_EXPECT_ERROR = 2

# Skipped unless run_tool_smoke(include_skipped=True) / native includeSkipped
SKIP_DEFAULT = {
    "render_scene",
    "inspect_max_file",
    "merge_from_file",
    "batch_file_info",
    "search_max_files",
    "create_shell_material",
    "create_material_from_textures",
    "replicate_material",
    "palette_laydown",
    "build_floor_plan",
    "scatter_forest_pack",
    "send_to_chat",
    "chat_clear",
    "chat_reload",
    "chat_status",
    "toggle_effect",
    "delete_effect",
    "replace_material",
    "batch_replace_materials",
    "wire_params",
    "unwire_params",
    "assign_controller",
    "set_controller_props",
    "add_controller_target",
    "merge_from_file",
    "get_railclone_style_graph",
    "load_dc_preset",
    "add_dc_script_operator",
    "set_data_channel_operator",
    "add_data_channel",
    "isolate_and_capture_selected",
    # Viewport — disrupts the UI (multi_view cycles front/right/back/top + zoom extents)
    "capture_viewport",
    "capture_multi_view",
    "capture_screen",
    "create_tyflow",
    "create_tyflow_preset",
    "get_tyflow_info",
    "list_tyflow_operator_types",
    "modify_tyflow_operator",
    "add_tyflow_event",
    "connect_tyflow_events",
    "set_tyflow_shape",
    "set_tyflow_physx",
    "add_tyflow_collision",
    "remove_tyflow_element",
    "get_tyflow_particle_count",
    "get_tyflow_particles",
    "reset_tyflow_simulation",
    "invoke_tool",
    "run_tool_smoke",
    # Heavy / interactive reads — fine manually, risky in automated bursts
    "learn_scene_patterns",
    "map_class_relationships",
    "watch_scene",
    "discover_plugin_classes",
    "execute_maxscript",
    # Compatibility aliases stay callable but are not part of the default smoke pass.
    "inspect_modifier_properties",
}

# Explicit inputs — ${SMOKE_TARGET} / ${SMOKE_SPAWN} replaced at runtime.
CUSTOM: dict[str, dict] = {
    "execute_maxscript": {"code": "42"},
    "manage_scene": {"action": "info"},
    "manage_layers": {"action": "list"},
    "manage_groups": {"action": "list"},
    "manage_selection_sets": {"action": "list"},
    "watch_scene": {"action": "poll", "limit": 5},
    "query_scene": {"action": "overview", "max_roots": 5},
    "scene_qa": {"max_issues": 20},
    "discover_plugin_classes": {"limit": 10},
    "list_plugin_classes": {"limit": 10},
    "introspect_class": {"class_name": "Box"},
    "map_class_relationships": {"limit": 5},
    "get_object_properties": {"name": SMOKE_TARGET},
    "inspect_object": {"name": SMOKE_TARGET},
    "inspect_properties": {"name": SMOKE_TARGET},
    "inspect_track_view": {"name": SMOKE_TARGET, "depth": 2},
    "inspect_controller": {"name": SMOKE_TARGET, "param_path": "[#transform][#position][#x_position]"},
    "keyframe_tracks": {"action": "list", "names": [SMOKE_TARGET], "tracks": "position", "from_time": 0, "to_time": 100},
    "get_material_slots": {"name": SMOKE_TARGET, "slot_scope": "map"},
    "inspect_material_network": {"name": SMOKE_TARGET, "depth": 2, "verify_files": False},
    "get_hierarchy": {"name": SMOKE_TARGET},
    "get_instances": {"name": SMOKE_TARGET},
    "get_dependencies": {"name": SMOKE_TARGET},
    "list_wireable_params": {"name": SMOKE_TARGET, "max_results": 20},
    "get_wired_params": {"name": SMOKE_TARGET},
    "walk_references": {"name": SMOKE_TARGET, "max_depth": 2},
    "introspect_instance": {"name": SMOKE_TARGET},
    "analyze_node_orientation": {"names": [SMOKE_TARGET]},
    "select_objects": {"names": [SMOKE_TARGET]},
    "set_visibility": {"names": [SMOKE_TARGET], "action": "unhide"},
    "transform_object": {"name": SMOKE_TARGET, "move": [0, 0, 0]},
    "set_object_property": {"name": SMOKE_TARGET, "property": "wirecolor", "value": "[180,180,180]"},
    "add_modifier": {"name": SMOKE_TARGET, "modifier": "Smooth"},
    "remove_modifier": {"name": SMOKE_TARGET, "modifier": "Smooth"},
    "set_modifier_state": {"name": SMOKE_TARGET, "modifier_name": "Smooth", "enabled": True},
    "make_modifier_unique": {"name": SMOKE_TARGET, "modifier_index": 1},
    "collapse_modifier_stack": {"name": SMOKE_TARGET},
    "assign_material": {
        "names": [SMOKE_TARGET],
        "material_class": "PhysicalMaterial",
        "material_name": "MCP_SmokeMtl",
    },
    "set_material_property": {"name": SMOKE_TARGET, "property": "base_color", "value": "[0.5,0.5,0.5]"},
    "set_material_properties": {"name": SMOKE_TARGET, "properties": {"roughness": 0.5}},
    "create_texture_map": {"map_class": "Noise", "map_name": "MCP_SmokeNoise"},
    "set_texture_map_properties": {"global_var": "MCP_SmokeNoise", "properties": {"size": 25}},
    "write_osl_shader": {
        "shader_name": "mcp_smoke_test",
        "osl_code": 'shader mcp_smoke_test(color outColor = 0) { outColor = color(1,0,0); }',
        "global_var": "MCP_SmokeOSL",
    },
    "create_object": {
        "type": "box",
        "name": SMOKE_SPAWN,
        "length": 5,
        "width": 5,
        "height": 5,
    },
    "delete_objects": {"names": [SMOKE_SPAWN]},
    "clone_objects": {"names": [SMOKE_TARGET], "mode": "copy", "offset": [20, 0, 0]},
    "set_parent": {"children": [SMOKE_SPAWN], "parent": SMOKE_TARGET},
    "set_modifier_property": {
        "name": SMOKE_TARGET,
        "modifier_index": 1,
        "property_name": "autosmooth",
        "property_value": "true",
    },
    "batch_rename_objects": {
        "renames": [{"old_name": SMOKE_SPAWN, "new_name": SMOKE_SPAWN}],
    },
    "capture_screen": {"enabled": False},
    "inspect_data_channel": {"name": SMOKE_TARGET},
    "add_data_channel": {
        "name": SMOKE_TARGET,
        "operators": [
            {"type": "vertex_input"},
            {"type": "vertex_output", "params": {"output": 4, "channelNum": 1}},
        ],
    },
    "list_dc_presets": {},
    "introspect_osl": {"global_var": "MCP_SmokeOSL"},
    "get_plugin_capabilities": {},
    "get_bridge_status": {},
    "get_session_context": {"max_roots": 5, "max_selection": 5},
    "discover_plugin_surface": {"limit": 5},
    "get_plugin_manifest": {"plugin_name": "tyFlow"},
    "refresh_plugin_manifest": {"plugin_name": "tyFlow"},
    "inspect_plugin_class": {"class_name": "Box"},
    "inspect_plugin_constructor": {"class_name": "Box"},
    "inspect_plugin_instance": {"name": SMOKE_TARGET},
    "inspect_modifier_properties": {"name": SMOKE_TARGET, "modifier_index": 1},
}

MUTATE_TOOLS = {
    "create_object",
    "delete_objects",
    "clone_objects",
    "set_parent",
    "transform_object",
    "set_object_property",
    "set_visibility",
    "add_modifier",
    "remove_modifier",
    "set_modifier_state",
    "make_modifier_unique",
    "collapse_modifier_stack",
    "assign_material",
    "set_material_property",
    "set_material_properties",
    "create_texture_map",
    "set_texture_map_properties",
    "set_sub_material",
    "write_osl_shader",
    "set_modifier_property",
    "batch_rename_objects",
}

TIER_FIXTURE_NAMES = {
    name
    for name, payload in CUSTOM.items()
    if SMOKE_TARGET in json.dumps(payload) or SMOKE_SPAWN in json.dumps(payload)
} - MUTATE_TOOLS


def c_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")


def flags_for(name: str) -> int:
    flags = 0
    if name in SKIP_DEFAULT:
        flags |= FLAG_SKIP_DEFAULT
    if name == "capture_screen":
        flags |= FLAG_EXPECT_ERROR
    return flags


def input_for(name: str, schema: dict) -> dict:
    if name in CUSTOM:
        return CUSTOM[name]
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    if not required:
        return {}
    out: dict = {}
    for key in required:
        if key == "name":
            out[key] = SMOKE_TARGET
        elif key == "names":
            out[key] = [SMOKE_TARGET]
        elif key == "action":
            out[key] = "list" if "manage" in name else "info"
        elif key == "class_name":
            out[key] = "Box"
        elif key == "property_name":
            out[key] = "name"
        elif key == "code":
            out[key] = "42"
        elif key in props:
            ptype = props[key].get("type")
            if ptype == "string":
                out[key] = "test"
            elif ptype == "integer":
                out[key] = 1
            elif ptype == "boolean":
                out[key] = False
            elif ptype == "array":
                out[key] = []
            else:
                out[key] = {}
        else:
            out[key] = "test"
    return out


def collect_native_tools() -> list[dict]:
    tools: list[dict] = []
    seen: set[str] = set()
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for t in extract_tools(path):
            if t["name"] in seen:
                continue
            seen.add(t["name"])
            if t["cmdType"] == "native:chat_ui":
                continue
            if not t["cmdType"].startswith("native:") and t["cmdType"] != "maxscript":
                continue
            t = dict(t)
            t["module"] = path.stem
            tools.append(t)
    return sorted(tools, key=lambda x: x["name"])


def collect_all_mcp_tools() -> list[dict]:
    import ast
    from scripts.gen_tool_registry import find_cmd_type, is_mcp_tool_decorator

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
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(is_mcp_tool_decorator(d) for d in node.decorator_list):
                continue
            if node.name in seen:
                continue
            seen.add(node.name)
            cmd_type = find_cmd_type(node, source) or "python-only"
            schema = {}
            for t in extract_tools(path):
                if t["name"] == node.name:
                    schema = t["schema"]
                    break
            if not schema:
                from scripts.gen_tool_registry import build_schema
                schema = build_schema(node)
            tools.append({
                "name": node.name,
                "cmdType": cmd_type,
                "schema": schema,
                "module": path.stem,
            })
    return sorted(tools, key=lambda x: x["name"])


# Native scene reads covered by query_scene — keep smoke coverage after MCP tool consolidation.
EXTRA_NATIVE_SCENE_READS: list[tuple[str, dict, int, str]] = [
    ("native:scene_snapshot", {}, TIER_READ, "query_scene_overview"),
    ("native:scene_info", {"class_name": "Box", "limit": 5}, TIER_READ, "query_scene_filter"),
    ("native:selection", {}, TIER_READ, "query_scene_selection_compact"),
    ("native:selection_snapshot", {"max_items": 5}, TIER_READ, "query_scene_selection"),
    ("native:scene_delta", {"capture": True}, TIER_READ, "query_scene_delta"),
    ("native:find_class_instances", {"class_name": "Box", "limit": 5}, TIER_READ, "query_scene_class"),
    ("native:find_objects_by_property", {"property_name": "name"}, TIER_READ, "query_scene_property"),
]


def main() -> int:
    tools = collect_native_tools()
    cases: list[dict] = []
    for t in tools:
        name = t["name"]
        if name in {"invoke_tool", "run_tool_smoke"}:
            continue
        payload = input_for(name, t["schema"])
        tier = TIER_MUTATE if name in MUTATE_TOOLS else (
            TIER_FIXTURE if name in TIER_FIXTURE_NAMES or SMOKE_TARGET in json.dumps(payload) else TIER_READ
        )
        cases.append({
            "tool": name,
            "cmdType": t["cmdType"],
            "module": t["module"],
            "input": payload,
            "tier": tier,
            "flags": flags_for(name),
        })

    covered_cmd_types = {c["cmdType"] for c in cases}
    for cmd_type, payload, tier, label in EXTRA_NATIVE_SCENE_READS:
        if cmd_type in covered_cmd_types:
            continue
        cases.append({
            "tool": label,
            "cmdType": cmd_type,
            "module": "query_scene",
            "input": payload,
            "tier": tier,
            "flags": 0,
        })

    # Stable run order: read → fixture → mutate
    cases.sort(key=lambda c: (c["tier"], c["tool"]))

    lines: list[str] = []
    lines.append("// AUTO-GENERATED by scripts/gen_tool_smoke.py — do not edit by hand.")
    lines.append(f"// Source: {len(cases)} native/maxscript smoke cases")
    lines.append("")
    lines.append("#pragma once")
    lines.append("")
    lines.append("#include <cstdint>")
    lines.append("")
    lines.append("struct SmokeCase {")
    lines.append("    const char* tool;")
    lines.append("    const char* inputJson;")
    lines.append("    uint8_t tier;")
    lines.append("    uint8_t flags;")
    lines.append("};")
    lines.append("")
    lines.append(f"static constexpr const char* kSmokeTargetToken = \"{SMOKE_TARGET}\";")
    lines.append(f"static constexpr const char* kSmokeSpawnToken = \"{SMOKE_SPAWN}\";")
    lines.append("")
    lines.append("static const SmokeCase kSmokeCases[] = {")
    for c in cases:
        inp = json.dumps(c["input"], separators=(",", ":"))
        lines.append(
            f'    {{"{c_escape(c["tool"])}", "{c_escape(inp)}", {c["tier"]}, {c["flags"]}}},'
        )
    lines.append("};")
    lines.append("static const size_t kSmokeCaseCount = sizeof(kSmokeCases) / sizeof(kSmokeCases[0]);")
    lines.append("")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")

    # Also emit JSON for the Python live runner (native/maxscript subset).
    json_path = ROOT / "native" / "generated" / "tool_smoke_cases.json"
    json_path.write_text(json.dumps(cases, indent=2), encoding="utf-8")

    # Full MCP surface for run_live_tool_smoke.py --tier full
    full_cases: list[dict] = []
    for t in collect_all_mcp_tools():
        name = t["name"]
        if name in {"invoke_tool", "run_tool_smoke"}:
            continue
        payload = input_for(name, t["schema"])
        tier = TIER_MUTATE if name in MUTATE_TOOLS else (
            TIER_FIXTURE if name in TIER_FIXTURE_NAMES or SMOKE_TARGET in json.dumps(payload) else TIER_READ
        )
        full_cases.append({
            "tool": name,
            "cmdType": t["cmdType"],
            "module": t["module"],
            "input": payload,
            "tier": tier,
            "flags": flags_for(name),
        })
    full_cases.sort(key=lambda c: (c["tier"], c["tool"]))
    full_json_path = ROOT / "native" / "generated" / "full_tool_smoke_cases.json"
    full_json_path.write_text(json.dumps(full_cases, indent=2), encoding="utf-8")

    read_n = sum(1 for c in cases if c["tier"] == TIER_READ)
    fix_n = sum(1 for c in cases if c["tier"] == TIER_FIXTURE)
    mut_n = sum(1 for c in cases if c["tier"] == TIER_MUTATE)
    skip_n = sum(1 for c in cases if c["flags"] & FLAG_SKIP_DEFAULT)
    print(
        f"[gen_tool_smoke] wrote {len(cases)} cases "
        f"(read={read_n}, fixture={fix_n}, mutate={mut_n}, skip_default={skip_n}) "
        f"-> {OUT_PATH.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
