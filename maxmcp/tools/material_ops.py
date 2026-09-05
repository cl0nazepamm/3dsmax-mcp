"""Material creation, assignment, and property manipulation tools for 3ds Max.

Covers the full material workflow: creating materials by class, assigning them
to objects, setting properties, creating texture maps, writing OSL shaders,
and managing Multi/Sub-Object sub-material slots.
Works with all material/map types: OpenPBR, Arnold (ai_standard_surface),
V-Ray (VRayMtl), Physical, Standard, OSLMap, Bitmaptexture, ai_bump2d,
and any MAXScript-creatable class.
"""

import json
from pathlib import Path
from typing import Optional
from ..server import mcp, client
from ..coerce import IntList, StrList
from ..helpers.maxscript import safe_string, safe_value
from ..helpers.material_assign import existing_material_script, existing_material_result
from ..helpers.native_compat import is_missing_native_route_error
from ..helpers.material_tripback import (
    material_class_hint,
    unsupported_material_class_result,
    wrap_material_tool_result,
)
from ..max_client import MaxBridgeError
from ..helpers.palette_sampling import (
    collect_one_sample_per_subfolder,
    normalize_overflow_mode,
    normalize_sample_mode,
    split_palette_and_library,
)
from ..helpers.path_filter import filter_by_name_pattern
from .material_detection import (
    _COLOR_CHANNELS,
    _DEFAULT_CHANNEL_PATTERNS,
    _IMAGE_EXTENSIONS,
    _group_texture_files_for_pbr,
    _match_textures_to_channels,
    _renderer_from_material_class,
    _scan_texture_folder,
)
from .material_shell import build_shell_wrap_maxscript, _extract_material_builder_body
from ._pbr_material_builder import (
    RENDERER_LABELS as _PBR_RENDERER_LABELS,
    groups_need_uberbitmap_osl as _pbr_groups_need_uberbitmap_osl,
    pbr_helpers_preamble_lines as _pbr_helpers_preamble_lines,
    pbr_per_group_lines as _pbr_per_group_lines,
    pbr_renderer_setup_lines as _pbr_renderer_setup_lines,
)


# ---------------------------------------------------------------------------
# Renderer configs and material-builder helpers
# ---------------------------------------------------------------------------

def _ms_path(p: Path) -> str:
    """Convert a Path to a MAXScript-safe forward-slash string."""
    return str(p).replace("\\", "/")


def _material_slot_hints(material_class: str) -> dict[str, str]:
    """Return compact map-class hints by material class."""
    cls = material_class.lower()
    if cls == "ai_standard_surface":
        return {
            "preferredBitmapClass": "ai_image",
            "normalHelperClass": "ai_normal_map",
            "bumpHelperClass": "ai_bump2d",
        }
    if cls == "rs_standard_material":
        return {
            "preferredBitmapClass": "Bitmaptexture",
            "normalHelperClass": "RS_BumpMap",
            "bumpHelperClass": "RS_BumpMap",
        }
    if cls in {"vraymtl", "v_ray_mtl", "vray_mtl"}:
        return {
            "preferredBitmapClass": "Bitmaptexture",
            "normalHelperClass": "Normal_Bump",
            "bumpHelperClass": "Normal_Bump",
        }
    if cls in {"openpbrmaterial", "openpbr_material", "physicalmaterial", "standardmaterial", "gltfmaterial", "maxusdpreviewsurface"}:
        return {
            "preferredBitmapClass": "Bitmaptexture",
            "normalHelperClass": "Normal_Bump",
            "bumpHelperClass": "Normal_Bump",
        }
    if cls in {"std_surface_mtl", "open_pbr_surf__mtl", "open_pbr_surf_mtl",
               "universal_material"} or "std_surface_mtl" in cls or cls.startswith("octane"):
        return {
            "preferredBitmapClass": "Image_MTX",
            "normalHelperClass": "",
            "bumpHelperClass": "",
            "channelPickerClass": "Channel_picker",
            "compositeMultiplyClass": "Multiply_MTX",
            "invertClass": "Invert_MTX",
            "texInputTypeFlag": "_input_type=2",
        }
    return {
        "preferredBitmapClass": "Bitmaptexture",
        "normalHelperClass": "",
        "bumpHelperClass": "",
    }


def _truncate_slots(payload: dict, key: str, max_per_group: int, out: dict, trunc: dict) -> None:
    items = payload.get(key, [])
    if not isinstance(items, list):
        out[key] = []
        return
    out[key] = items[:max_per_group]
    if len(items) > max_per_group:
        trunc[key] = len(items)


def _build_shared_pbr_maxscript(
    matched: dict[str, Path],
    material_name: str,
    renderer: str,
    assign_to: list[str] | None,
    include_displacement: bool = True,
) -> str:
    """Build one PBR material via the shared pbr_per_group_lines builder."""
    group = {"name": material_name, "channels": matched, "aliases": {}}
    groups = [group]
    safe_mat = safe_string(material_name)
    lines = list(_pbr_helpers_preamble_lines())
    lines.extend(_pbr_renderer_setup_lines(
        renderer,
        needs_uberbitmap_osl=_pbr_groups_need_uberbitmap_osl(groups, renderer),
    ))
    lines.append('summary = ""')
    lines.extend(_pbr_per_group_lines(
        group,
        idx=1,
        mat_var="mat",
        mat_name=safe_mat,
        renderer=renderer,
        include_displacement=include_displacement,
    ))
    lines.append(f'summary = mat.name + " (" + ((classOf mat) as string) + ")"')
    if assign_to:
        names_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in assign_to) + ")"
        lines.extend([
            f"nameList = {names_arr}",
            "assignCount = 0",
            "for n in nameList do (obj = getNodeByName n; if obj != undefined then (obj.material = mat; assignCount += 1))",
            'summary += " | Assigned to " + (assignCount as string) + " object(s)"',
        ])
    lines.extend([
        'summary += " | Channels: " + channelList',
        'if skippedList != "" do summary += " | Skipped: " + skippedList',
        "summary",
    ])
    return "(\n    " + "\n    ".join(lines) + "\n)"


@mcp.tool()
def assign_material(
    names: StrList | None = None,
    material_class: str = "",
    material_name: str = "",
    params: str = "",
    handles: IntList | None = None,
    source_name: str = "",
    source_handle: int = 0,
) -> str | dict:
    """Create a material, or share an existing object's material with targets.

    Existing material: pass source_name/source_handle instead of material_class,
    material_name and params. Both source selectors cross-check when supplied.
    This instances the actual material, including maps and sub-materials, so
    later material edits remain shared. Targets are preflighted and assigned in
    one undo step; missing/ambiguous nodes fail without partial assignment.
    """
    names = names or []
    handles = handles or []
    if source_name or source_handle:
        if material_class or material_name or params:
            raise ValueError("Choose existing source_name/source_handle or material creation arguments")
        script = existing_material_script(names, handles, source_name, source_handle)
        response = client.send_command(script)
        return existing_material_result(str(response.get("result", "")))
    if client.native_available:
        payload = {
            "names": names,
            "handles": handles,
            "material_class": material_class,
            "material_name": material_name,
            "params": params,
        }
        response = client.send_command(json.dumps(payload), cmd_type="native:assign_material")
        return response.get("result", "")

    safe_mat_name = safe_string(material_name)
    name_param = f' name:"{safe_mat_name}"' if material_name else ""
    name_arr = "#(" + ", ".join(f'"{safe_string(n)}"' for n in names) + ")"

    maxscript = f"""(
        try (
            mat = {material_class}{name_param} {params}
            nameList = {name_arr}
            assignCount = 0
            notFound = #()
            for n in nameList do (
                obj = getNodeByName n
                if obj != undefined then (
                    obj.material = mat
                    assignCount += 1
                ) else (
                    append notFound n
                )
            )
            msg = "Created " + (classof mat) as string + " \\\"" + mat.name + "\\\" and assigned to " + (assignCount as string) + " object(s)"
            if notFound.count > 0 do msg += " | Not found: " + (notFound as string)
            msg
        ) catch (
            "Error: " + (getCurrentException())
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_material_property(
    name: str = "",
    property: str = "",
    value: str = "",
    sub_material_index: int = 0,
    handle: int = 0,
) -> str:
    """Set a property on an object's material (or sub-material)."""
    if client.native_available:
        payload = {
            "name": name,
            "property": property,
            "value": value,
            "sub_material_index": sub_material_index,
        }
        if handle:
            payload["handle"] = handle
        response = client.send_command(json.dumps(payload), cmd_type="native:set_material_property")
        return response.get("result", "")

    if not name:
        return "Object name is required for TCP fallback"

    safe = safe_string(name)
    safe_prop = safe_string(property)

    if sub_material_index > 0:
        mat_expr = f"obj.material[{sub_material_index}]"
        mat_label = f"sub-material [{sub_material_index}]"
    else:
        mat_expr = "obj.material"
        mat_label = "material"

    maxscript = f"""(
        obj = getNodeByName "{safe}"
        if obj == undefined then (
            "Object not found: {safe}"
        ) else if obj.material == undefined then (
            "No material assigned to {safe}"
        ) else (
            mat = {mat_expr}
            if mat == undefined then (
                "Sub-material index {sub_material_index} not found on {safe}"
            ) else (
                try (
                    mat.{safe_prop} = {safe_value(value)}
                    readback = (getproperty mat #{safe_prop}) as string
                    "Set " + mat.name + ".{safe_prop} = " + readback
                ) catch (
                    "Error setting {safe_prop}: " + (getCurrentException())
                )
            )
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_material_properties(
    name: str = "",
    properties: dict[str, str] | None = None,
    sub_material_index: int = 0,
    handle: int = 0,
) -> str:
    """Set multiple properties on an object's material in a single call."""
    properties = properties or {}
    if client.native_available:
        payload = {
            "name": name,
            "properties": properties,
            "sub_material_index": sub_material_index,
        }
        if handle:
            payload["handle"] = handle
        response = client.send_command(json.dumps(payload), cmd_type="native:set_material_properties")
        return response.get("result", "")

    if not name:
        return "Object name is required for TCP fallback"

    safe = safe_string(name)

    if sub_material_index > 0:
        mat_expr = f"obj.material[{sub_material_index}]"
    else:
        mat_expr = "obj.material"

    # Build the property-setting lines
    set_lines = []
    for prop, val in properties.items():
        safe_prop = safe_string(prop)
        set_lines.append(
            f'try (mat.{safe_prop} = {safe_value(val)}; append okList "{safe_prop}") '
            f'catch (append errList ("{safe_prop}: " + (getCurrentException())))'
        )
    set_block = "\n            ".join(set_lines)

    maxscript = f"""(
        obj = getNodeByName "{safe}"
        if obj == undefined then (
            "Object not found: {safe}"
        ) else if obj.material == undefined then (
            "No material assigned to {safe}"
        ) else (
            mat = {mat_expr}
            if mat == undefined then (
                "Sub-material index {sub_material_index} not found on {safe}"
            ) else (
                okList = #()
                errList = #()
                {set_block}
                msg = "Set " + (okList.count as string) + " properties on " + mat.name
                if okList.count > 0 do (
                    msg += ": "
                    for i = 1 to okList.count do (
                        if i > 1 do msg += ", "
                        msg += okList[i]
                    )
                )
                if errList.count > 0 do (
                    msg += " | Errors: "
                    for i = 1 to errList.count do (
                        if i > 1 do msg += "; "
                        msg += errList[i]
                    )
                )
                msg
            )
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def get_material_slots(
    name: str,
    sub_material_index: int = 0,
    include_values: bool = False,
    max_slots: int = 40,
    slot_scope: str = "map",
    max_per_group: int = 15,
) -> str:
    """Get compact material slot/property info without schema caches.

    Use when: inspecting map/param slots on a known material (prefer slot_scope='map').
    Not when: listing scene materials (get_materials) or building materials from textures
    (create_material_from_textures / smart_import). Avoid slot_scope='all' + include_values
    on Arnold/Physical — output is huge.
    """
    if client.native_available:
        try:
            payload = json.dumps({
                "name": name,
                "sub_material_index": sub_material_index,
                "include_values": include_values,
                "max_slots": max(1, int(max_slots)),
                "slot_scope": (slot_scope or "map").strip().lower(),
                "max_per_group": max(1, int(max_per_group)),
            })
            response = client.send_command(payload, cmd_type="native:get_material_slots")
            raw = response.get("result", "")
            if not raw:
                return raw
            try:
                payload_data = json.loads(raw)
            except Exception:
                return raw
            if isinstance(payload_data, dict):
                material_class = str(payload_data.get("class", ""))
                payload_data["hints"] = _material_slot_hints(material_class)
            return json.dumps(payload_data, separators=(",", ":"))
        except RuntimeError:
            pass

    safe = safe_string(name)
    max_slots = max(1, int(max_slots))
    max_per_group = max(1, int(max_per_group))
    slot_scope = (slot_scope or "map").strip().lower()
    if slot_scope not in {"map", "summary", "all"}:
        slot_scope = "map"
    include_vals = "true" if include_values else "false"

    if sub_material_index > 0:
        mat_expr = f"obj.material[{sub_material_index}]"
    else:
        mat_expr = "obj.material"

    maxscript = f"""(
        local esc = MCP_Server.escapeJsonString

        fn toJsonNameArray arr = (
            local out = "["
            local q = (bit.intAsChar 34)
            for i = 1 to arr.count do (
                if i > 1 do out += ","
                out += q + (esc arr[i]) + q
            )
            out += "]"
            out
        )

        fn toJsonPairArray names vals = (
            local out = "["
            local q = (bit.intAsChar 34)
            local lb = (bit.intAsChar 123)
            local rb = (bit.intAsChar 125)
            local lim = amin #(names.count, vals.count)
            for i = 1 to lim do (
                if i > 1 do out += ","
                out += lb + q + "name" + q + ":" + q + (esc names[i]) + q + "," + q + "value" + q + ":" + q + (esc vals[i]) + q + rb
            )
            out += "]"
            out
        )

        fn classifyDeclType decl = (
            local d = toLower decl
            if (findString d "texturemap") != undefined or (findString d "texmap") != undefined then "map"
            else if (findString d "color") != undefined then "color"
            else if (findString d "bool") != undefined then "bool"
            else if (findString d "float") != undefined or (findString d "integer") != undefined or (findString d "double") != undefined or (findString d "worldunits") != undefined or (findString d "percent") != undefined then "numeric"
            else "other"
        )

        local obj = getNodeByName "{safe}"
        if obj == undefined then (
            "{{\\"error\\":\\"Object not found: {safe}\\"}}"
        ) else if obj.material == undefined then (
            "{{\\"error\\":\\"No material assigned to {safe}\\"}}"
        ) else (
            local mat = {mat_expr}
            if mat == undefined then (
                "{{\\"error\\":\\"Sub-material index {sub_material_index} not found on {safe}\\"}}"
            ) else (
                local includeValues = {include_vals}
                local maxSlots = {max_slots}
                local subIdx = {sub_material_index}

                local props = #()
                try (props = makeUniqueArray (getPropNames mat)) catch ()

                -- Build declared type map from showProperties output
                local typeNames = #()
                local typeVals = #()
                try (
                    local ss = stringstream ""
                    showProperties mat to:ss
                    seek ss 0
                    while not (eof ss) do (
                        local ln = readline ss
                        local chunks = filterString ln ":"
                        if chunks.count >= 2 do (
                            local lhs = trimRight chunks[1]
                            local rhs = trimLeft chunks[2]
                            local lhsParts = filterString lhs ". "
                            if lhsParts.count >= 1 do (
                                local pnm = toLower lhsParts[lhsParts.count]
                                append typeNames pnm
                                append typeVals rhs
                            )
                        )
                    )
                ) catch ()

                fn getDeclType pname tNames tVals = (
                    local idx = findItem tNames (toLower pname)
                    if idx != 0 then tVals[idx] else ""
                )

                local mapNames = #();     local mapVals = #()
                local colorNames = #();   local colorVals = #()
                local numNames = #();     local numVals = #()
                local boolNames = #();    local boolVals = #()
                local otherNames = #();   local otherVals = #()

                local scanned = 0
                for p in props while scanned < maxSlots do (
                    local pname = p as string
                    if pname == "materialList" or pname == "maps" then continue

                    local val = undefined
                    local ok = true
                    try (val = getProperty mat p) catch (ok = false)
                    if not ok then continue

                    local decl = getDeclType pname typeNames typeVals
                    local cls = classifyDeclType decl
                    local rt = try ((classOf val) as string) catch "undefined"
                    local valStr = try (val as string) catch ""

                    if valStr.count > 120 do valStr = (substring valStr 1 120) + "..."

                    -- Fallback map detection for undeclared cases
                    local pnameL = toLower pname
                    if cls == "other" and ((matchPattern pnameL pattern:"*_map*" ignoreCase:true) or (matchPattern pnameL pattern:"*_shader*" ignoreCase:true) or ((findString (toLower rt) "texture") != undefined)) do cls = "map"

                    case cls of (
                        "map": (
                            append mapNames pname
                            append mapVals valStr
                        )
                        "color": (
                            append colorNames pname
                            append colorVals valStr
                        )
                        "numeric": (
                            append numNames pname
                            append numVals valStr
                        )
                        "bool": (
                            append boolNames pname
                            append boolVals valStr
                        )
                        default: (
                            append otherNames pname
                            append otherVals valStr
                        )
                    )
                    scanned += 1
                )

                local result = "{{"
                result += "\\"name\\":\\"" + (esc mat.name) + "\\","
                result += "\\"class\\":\\"" + (esc ((classOf mat) as string)) + "\\","
                result += "\\"subMaterialIndex\\":" + (subIdx as string) + ","
                result += "\\"inspectedCount\\":" + (scanned as string) + ","
                result += "\\"counts\\":{{"
                result += "\\"map\\":" + (mapNames.count as string) + ","
                result += "\\"color\\":" + (colorNames.count as string) + ","
                result += "\\"numeric\\":" + (numNames.count as string) + ","
                result += "\\"bool\\":" + (boolNames.count as string) + ","
                result += "\\"other\\":" + (otherNames.count as string)
                result += "}},"

                if includeValues then (
                    result += "\\"mapSlots\\":" + (toJsonPairArray mapNames mapVals) + ","
                    result += "\\"colorSlots\\":" + (toJsonPairArray colorNames colorVals) + ","
                    result += "\\"numericSlots\\":" + (toJsonPairArray numNames numVals) + ","
                    result += "\\"boolSlots\\":" + (toJsonPairArray boolNames boolVals) + ","
                    result += "\\"otherSlots\\":" + (toJsonPairArray otherNames otherVals)
                ) else (
                    result += "\\"mapSlots\\":" + (toJsonNameArray mapNames) + ","
                    result += "\\"colorSlots\\":" + (toJsonNameArray colorNames) + ","
                    result += "\\"numericSlots\\":" + (toJsonNameArray numNames) + ","
                    result += "\\"boolSlots\\":" + (toJsonNameArray boolNames) + ","
                    result += "\\"otherSlots\\":" + (toJsonNameArray otherNames)
                )

                result += "}}"
                result
            )
        )
    )"""
    response = client.send_command(maxscript, timeout=45.0)
    raw = response.get("result", "")
    if not raw:
        return raw

    try:
        payload = json.loads(raw)
    except Exception:
        return raw

    if not isinstance(payload, dict):
        return raw

    material_class = str(payload.get("class", ""))
    compact: dict[str, object] = {
        "name": payload.get("name", ""),
        "class": material_class,
        "subMaterialIndex": payload.get("subMaterialIndex", sub_material_index),
        "inspectedCount": payload.get("inspectedCount", 0),
        "counts": payload.get("counts", {}),
        "hints": _material_slot_hints(material_class),
    }

    if "error" in payload:
        compact = {
            "error": payload.get("error"),
            "hints": _material_slot_hints(material_class),
        }
        return json.dumps(compact, separators=(",", ":"))

    trunc: dict[str, int] = {}
    if slot_scope in {"map", "all"}:
        _truncate_slots(payload, "mapSlots", max_per_group, compact, trunc)
    if slot_scope == "all":
        _truncate_slots(payload, "colorSlots", max_per_group, compact, trunc)
        _truncate_slots(payload, "numericSlots", max_per_group, compact, trunc)
        _truncate_slots(payload, "boolSlots", max_per_group, compact, trunc)
        _truncate_slots(payload, "otherSlots", max_per_group, compact, trunc)

    if trunc:
        compact["truncatedFrom"] = trunc

    return json.dumps(compact, separators=(",", ":"))


@mcp.tool()
def create_texture_map(
    map_class: str,
    map_name: str = "",
    params: str = "",
    properties: dict[str, str] | None = None,
    global_var: str = "",
) -> str:
    """Create a texture map and store it as a MAXScript global variable."""
    if client.native_available:
        payload = {
            "map_class": map_class,
            "map_name": map_name,
            "params": params,
            "properties": properties or {},
            "global_var": global_var,
        }
        response = client.send_command(json.dumps(payload), cmd_type="native:create_texture_map")
        return response.get("result", "")

    safe_map_name = safe_string(map_name)
    name_param = f' name:"{safe_map_name}"' if map_name else ""

    # Generate global var name if not provided
    if not global_var:
        base = map_name if map_name else map_class
        # Clean to valid MAXScript identifier
        global_var = "".join(c if c.isalnum() or c == "_" else "_" for c in base)
        if global_var[0].isdigit():
            global_var = "m_" + global_var

    # Build property-setting lines
    prop_lines = ""
    if properties:
        lines = []
        for prop, val in properties.items():
            safe_prop = safe_string(prop)
            lines.append(
                f'try (global {global_var} ; {global_var}.{safe_prop} = {safe_value(val)}; '
                f'append okList "{safe_prop}") '
                f'catch (append errList ("{safe_prop}: " + (getCurrentException())))'
            )
        prop_lines = "\n            ".join(lines)

    maxscript = f"""(
        try (
            global {global_var} = {map_class}{name_param} {params}
            okList = #()
            errList = #()
            {"" if not prop_lines else prop_lines}
            msg = "Created " + (classof {global_var}) as string
            if {global_var}.name != undefined do msg += " \\\"" + {global_var}.name + "\\\""
            msg += " as global '{global_var}'"
            if okList.count > 0 do (
                msg += " | Set: "
                for i = 1 to okList.count do (if i > 1 do msg += ", "; msg += okList[i])
            )
            if errList.count > 0 do (
                msg += " | Errors: "
                for i = 1 to errList.count do (if i > 1 do msg += "; "; msg += errList[i])
            )
            msg
        ) catch (
            "Error: " + (getCurrentException())
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_texture_map_properties(
    global_var: str,
    properties: dict[str, str],
) -> str:
    """Set properties on a texture map stored as a MAXScript global variable."""
    if client.native_available:
        payload = json.dumps({"global_var": global_var, "properties": properties})
        response = client.send_command(payload, cmd_type="native:set_texture_map_properties")
        return response.get("result", "")

    lines = []
    for prop, val in properties.items():
        safe_prop = safe_string(prop)
        lines.append(
            f'try ({global_var}.{safe_prop} = {val}; append okList "{safe_prop}") '
            f'catch (append errList ("{safe_prop}: " + (getCurrentException())))'
        )
    set_block = "\n            ".join(lines)

    maxscript = f"""(
        try (
            global {global_var}
            if {global_var} == undefined then (
                "Error: global '{global_var}' not found"
            ) else (
                okList = #()
                errList = #()
                {set_block}
                msg = "Set " + (okList.count as string) + " properties on " + {global_var}.name
                if okList.count > 0 do (
                    msg += ": "
                    for i = 1 to okList.count do (if i > 1 do msg += ", "; msg += okList[i])
                )
                if errList.count > 0 do (
                    msg += " | Errors: "
                    for i = 1 to errList.count do (if i > 1 do msg += "; "; msg += errList[i])
                )
                msg
            )
        ) catch (
            "Error: " + (getCurrentException())
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def set_sub_material(
    name: str = "",
    sub_material_index: int = 0,
    material_class: str = "",
    material_name: str = "",
    params: str = "",
    source_index: int = 0,
    handle: int = 0,
) -> str:
    """Create or assign a sub-material in a Multi/Sub-Object material slot."""
    if client.native_available:
        payload = {
            "name": name,
            "sub_material_index": sub_material_index,
            "material_class": material_class,
            "material_name": material_name,
            "params": params,
            "source_index": source_index,
        }
        if handle:
            payload["handle"] = handle
        response = client.send_command(json.dumps(payload), cmd_type="native:set_sub_material")
        return response.get("result", "")

    if not name:
        return "Object name is required for TCP fallback"

    safe = safe_string(name)
    safe_mat_name = safe_string(material_name)
    name_param = f' name:"{safe_mat_name}"' if material_name else ""

    if source_index > 0:
        # Reference from another slot
        maxscript = f"""(
            obj = getNodeByName "{safe}"
            if obj == undefined then "Object not found: {safe}"
            else if obj.material == undefined then "No material on {safe}"
            else if (classof obj.material) != Multimaterial then "Material is not Multimaterial"
            else (
                try (
                    srcMat = obj.material.materialList[{source_index}]
                    if srcMat == undefined then "Source slot {source_index} is empty"
                    else (
                        obj.material.materialList[{sub_material_index}] = srcMat
                        "Sub[{sub_material_index}] = Sub[{source_index}] (" + srcMat.name + ") — shared reference"
                    )
                ) catch ("Error: " + (getCurrentException()))
            )
        )"""
    else:
        # Create new material at slot
        maxscript = f"""(
            obj = getNodeByName "{safe}"
            if obj == undefined then "Object not found: {safe}"
            else if obj.material == undefined then "No material on {safe}"
            else if (classof obj.material) != Multimaterial then "Material is not Multimaterial"
            else (
                try (
                    newMat = {material_class}{name_param} {params}
                    obj.material.materialList[{sub_material_index}] = newMat
                    "Sub[{sub_material_index}] = " + newMat.name + " (" + (classof newMat) as string + ")"
                ) catch ("Error: " + (getCurrentException()))
            )
        )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def write_osl_shader(
    shader_name: str,
    osl_code: str,
    global_var: str = "",
    properties: dict[str, str] | None = None,
) -> str:
    """Write an OSL shader to disk and create an OSLMap from it."""
    if not global_var:
        global_var = "".join(c if c.isalnum() or c == "_" else "_" for c in shader_name)
        if global_var[0].isdigit():
            global_var = "m_" + global_var

    if client.native_available:
        payload = {
            "shader_name": shader_name,
            "osl_code": osl_code,
            "global_var": global_var,
        }
        if properties:
            payload["properties"] = properties
        response = client.send_command(json.dumps(payload), cmd_type="native:write_osl_shader")
        raw = response.get("result", "")
        try:
            data = json.loads(raw)
            return data.get("message", raw)
        except Exception:
            return raw

    # Escape the OSL code for MAXScript string embedding
    safe_osl = osl_code.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    safe_shader_name = safe_string(shader_name)

    # Build property-setting lines
    prop_lines = ""
    if properties:
        lines = []
        for prop, val in properties.items():
            safe_prop = safe_string(prop)
            lines.append(
                f'try (global {global_var} ; {global_var}.{safe_prop} = {safe_value(val)}; '
                f'append okList "{safe_prop}") '
                f'catch (append errList ("{safe_prop}: " + (getCurrentException())))'
            )
        prop_lines = "\n            ".join(lines)

    maxscript = f"""(
        try (
            oslDir = (getDir #temp) + "\\\\osl_shaders\\\\"
            makeDir oslDir
            oslPath = oslDir + "{safe_shader_name}.osl"
            oslContent = "{safe_osl}"
            f = createFile oslPath
            format "%" oslContent to:f
            close f

            global {global_var} = OSLMap name:"{safe_shader_name}"
            {global_var}.OSLCode = oslContent
            {global_var}.OSLAutoUpdate = true
            {global_var}.OSLPath = oslPath

            okList = #()
            errList = #()
            {"" if not prop_lines else prop_lines}

            msg = "OSL shader written to " + oslPath + " | Global: {global_var}"
            if okList.count > 0 do (
                msg += " | Set: "
                for i = 1 to okList.count do (if i > 1 do msg += ", "; msg += okList[i])
            )
            if errList.count > 0 do (
                msg += " | Errors: "
                for i = 1 to errList.count do (if i > 1 do msg += "; "; msg += errList[i])
            )
            msg
        ) catch (
            "Error: " + (getCurrentException())
        )
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")


@mcp.tool()
def create_material_from_textures(
    texture_folder: str,
    material_class: str = "",
    material_name: str = "",
    assign_to: StrList | None = None,
    custom_patterns: dict[str, list[str]] | None = None,
) -> str | dict:
    """Create a fully-wired PBR material from a folder of texture maps.

    Pass ``material_class`` to pick the renderer — any value from tripback
    ``supported_material_classes`` / ``hint.renderers`` (OpenPBR default, Physical,
    Arnold, Redshift, V-Ray, MaterialX, Octane, etc.).
    """
    # -- Step 1: Scan folder (Python-side) --
    files = _scan_texture_folder(texture_folder)
    if not files:
        return f"No image files found in: {texture_folder}"

    # -- Step 2: Match textures to channels (Python-side) --
    patterns = dict(_DEFAULT_CHANNEL_PATTERNS)
    if custom_patterns:
        patterns.update(custom_patterns)

    matched = _match_textures_to_channels(files, patterns)
    if not matched:
        suffixes = [f.stem for f in files[:10]]
        return f"No textures matched any channel pattern. File stems: {suffixes}"

    # -- Step 3: Determine renderer / material class --
    if not material_class:
        renderer = "openpbr"
    else:
        renderer = _renderer_from_material_class(material_class)
        if renderer is None:
            return unsupported_material_class_result(
                material_class, tool="create_material_from_textures",
            )

    # -- Step 4: Derive material name --
    if not material_name:
        material_name = Path(texture_folder).name

    # -- Step 5: Build MAXScript (one shared builder for every renderer) --
    maxscript = _build_shared_pbr_maxscript(matched, material_name, renderer, assign_to)

    # Wrap in try/catch
    maxscript = f"""(
    try (
        {maxscript}
    ) catch (
        "Error: " + (getCurrentException())
    )
)"""

    # -- Step 6: Send to Max --
    response = client.send_command(maxscript)
    raw = response.get("result", "")
    return wrap_material_tool_result(
        str(raw),
        material_class=material_class,
        renderer=renderer,
        tool="create_material_from_textures",
    )


def _scan_material_editor_palette_files(folder: str, recursive: bool) -> list[Path]:
    from .material_detection import scan_texture_files

    return scan_texture_files(folder, recursive)


def _build_material_editor_palette_maxscript(
    files: list[Path],
    start_slot: int,
    open_editor: bool,
    material_prefix: str,
    slot_content: str,
    library_files: list[Path] | None = None,
) -> str:
    library_files = library_files or []
    lines: list[str] = [
        "fn mcp_setFirstMap target propNames tex = (",
        "    for propName in propNames do (",
        "        try (setProperty target (propName as name) tex; return propName) catch ()",
        "    )",
        "    undefined",
        ")",
        "fn mcp_setFirstValue target propNames value = (",
        "    for propName in propNames do (",
        "        try (setProperty target (propName as name) value; return propName) catch ()",
        "    )",
        "    undefined",
        ")",
        "fn mcp_createOpenPbrPreferred matName = (",
        "    local m = undefined",
        "    try (m = OpenPBRMaterial name:matName) catch ()",
        "    if m == undefined do try (m = OpenPBR_Material name:matName) catch ()",
        "    if m == undefined do try (m = OpenPBR_Mtl name:matName) catch ()",
        "    if m == undefined do try (m = PhysicalMaterial name:matName) catch ()",
        '    if m == undefined do throw "OpenPBRMaterial/OpenPBR_Material/OpenPBR_Mtl/PhysicalMaterial are unavailable"',
        "    m",
        ")",
        "local loaded = #()",
        "local libraryLoaded = #()",
        "local classes = #()",
        "local errors = #()",
        f"local slotIndex = {start_slot}",
    ]

    if open_editor:
        lines.extend([
            "try (MatEditor.mode = #basic) catch ()",
            "try (MatEditor.Open()) catch ()",
        ])

    for idx, fpath in enumerate(files, start=1):
        path_literal = safe_string(_ms_path(fpath))
        tex_name = safe_string(fpath.stem)
        mat_name = safe_string(f"{material_prefix}{fpath.stem}")
        tex_var = f"tex_{idx}"
        mat_var = f"mat_{idx}"
        if slot_content == "bitmap":
            lines.extend([
                "try (",
                f'    local {tex_var} = Bitmaptexture name:"{tex_name}" filename:@"{path_literal}"',
                f"    try (medit.PutMtlToMtlEditor {tex_var} slotIndex) catch (meditMaterials[slotIndex] = {tex_var})",
                "    try (medit.SetActiveMtlSlot slotIndex true) catch (activeMeditSlot = slotIndex)",
                f'    append loaded ((slotIndex as string) + ": {safe_string(fpath.name)} -> " + ((classOf {tex_var}) as string))',
                f"    appendIfUnique classes ((classOf {tex_var}) as string)",
                "    slotIndex += 1",
                f') catch (append errors ("{safe_string(fpath.name)}: " + (getCurrentException())))',
            ])
        else:
            lines.extend([
                "try (",
                f'    local {tex_var} = Bitmaptexture name:"{tex_name}" filename:@"{path_literal}"',
                f'    local {mat_var} = mcp_createOpenPbrPreferred "{mat_name}"',
                f'    local slotName = mcp_setFirstMap {mat_var} #("base_color_map", "baseColor_map", "basecolor_map", "base_map", "diffuse_map") {tex_var}',
                f'    if slotName == undefined do try ({mat_var}.base_color_map = {tex_var}; slotName = "base_color_map") catch ()',
                f'    local specName = mcp_setFirstValue {mat_var} #("specular_color", "specularColor", "specular", "refl_color") (color 0 0 0)',
                f"    try (medit.PutMtlToMtlEditor {mat_var} slotIndex) catch (meditMaterials[slotIndex] = {mat_var})",
                "    try (medit.SetActiveMtlSlot slotIndex true) catch (activeMeditSlot = slotIndex)",
                f'    append loaded ((slotIndex as string) + ": {safe_string(fpath.name)} -> " + ((classOf {mat_var}) as string) + ", spec=" + (specName as string))',
                f"    appendIfUnique classes ((classOf {mat_var}) as string)",
                "    slotIndex += 1",
                f') catch (append errors ("{safe_string(fpath.name)}: " + (getCurrentException())))',
            ])

    for idx, fpath in enumerate(library_files, start=len(files) + 1):
        path_literal = safe_string(_ms_path(fpath))
        tex_name = safe_string(fpath.stem)
        mat_name = safe_string(f"{material_prefix}{fpath.stem}")
        tex_var = f"libtex_{idx}"
        mat_var = f"libmat_{idx}"
        if slot_content == "bitmap":
            lines.extend([
                "try (",
                f'    local {tex_var} = Bitmaptexture name:"{tex_name}" filename:@"{path_literal}"',
                f"    try (append currentMaterialLibrary {tex_var}) catch ()",
                f'    append libraryLoaded ("lib: {safe_string(fpath.name)} -> " + ((classOf {tex_var}) as string))',
                f"    appendIfUnique classes ((classOf {tex_var}) as string)",
                f') catch (append errors ("{safe_string(fpath.name)}: " + (getCurrentException())))',
            ])
        else:
            lines.extend([
                "try (",
                f'    local {tex_var} = Bitmaptexture name:"{tex_name}" filename:@"{path_literal}"',
                f'    local {mat_var} = mcp_createOpenPbrPreferred "{mat_name}"',
                f'    local slotName = mcp_setFirstMap {mat_var} #("base_color_map", "baseColor_map", "basecolor_map", "base_map", "diffuse_map") {tex_var}',
                f'    if slotName == undefined do try ({mat_var}.base_color_map = {tex_var}; slotName = "base_color_map") catch ()',
                f'    local specName = mcp_setFirstValue {mat_var} #("specular_color", "specularColor", "specular", "refl_color") (color 0 0 0)',
                f"    try (append currentMaterialLibrary {mat_var}) catch ()",
                f'    append libraryLoaded ("lib: " + {mat_var}.name + " -> " + ((classOf {mat_var}) as string))',
                f"    appendIfUnique classes ((classOf {mat_var}) as string)",
                f') catch (append errors ("{safe_string(fpath.name)}: " + (getCurrentException())))',
            ])

    content_label = "bitmap texture map" if slot_content == "bitmap" else "OpenPBR-first texture material"
    lines.extend([
        f'local msg = "Loaded " + (loaded.count as string) + " {content_label}(s) into Material Editor slots"',
        'if loaded.count > 0 do msg += " [" + loaded[1] + " .. " + loaded[loaded.count] + "]"',
        'if libraryLoaded.count > 0 do msg += " | Library: " + (libraryLoaded.count as string) + " item(s)"',
        'if libraryLoaded.count > 0 do msg += " [" + libraryLoaded[1] + " .. " + libraryLoaded[libraryLoaded.count] + "]"',
        'if classes.count > 0 do msg += " | Classes: " + (classes as string)',
        'if errors.count > 0 do (',
        '    msg += " | Errors: "',
        '    for i = 1 to errors.count do (',
        '        if i > 1 do msg += "; "',
        '        msg += errors[i]',
        '    )',
        ')',
        "msg",
    ])
    return "(\n    " + "\n    ".join(lines) + "\n)"


def _build_material_editor_pbr_palette_maxscript(
    groups: list[dict],
    start_slot: int,
    open_editor: bool,
    material_prefix: str,
    renderer: str,
    include_displacement: bool = True,
    unmatched_count: int = 0,
    duplicate_count: int = 0,
    library_groups: list[dict] | None = None,
) -> str:
    """Generate MAXScript for one fully wired PBR material per texture set, placed in Material Editor slots."""
    renderer_label = _PBR_RENDERER_LABELS[renderer]
    library_groups = library_groups or []

    lines = list(_pbr_helpers_preamble_lines())
    lines.extend([
        "local loaded = #()",
        "local libraryLoaded = #()",
        "local classes = #()",
        "local errors = #()",
        f"local slotIndex = {start_slot}",
    ])
    lines.extend(_pbr_renderer_setup_lines(
        renderer,
        needs_uberbitmap_osl=_pbr_groups_need_uberbitmap_osl(groups + library_groups, renderer),
    ))

    if open_editor:
        lines.extend([
            "try (MatEditor.mode = #basic) catch ()",
            "try (MatEditor.Open()) catch ()",
        ])

    for idx, group in enumerate(groups, start=1):
        mat_name = safe_string(f"{material_prefix}{group['name']}")
        mat_var = f"mat_{idx}"

        lines.append("try (")
        lines.extend(_pbr_per_group_lines(
            group,
            idx=idx,
            mat_var=mat_var,
            mat_name=mat_name,
            renderer=renderer,
            include_displacement=include_displacement,
        ))
        lines.extend([
            f"    try (medit.PutMtlToMtlEditor {mat_var} slotIndex) catch (meditMaterials[slotIndex] = {mat_var})",
            "    try (medit.SetActiveMtlSlot slotIndex true) catch (activeMeditSlot = slotIndex)",
            f'    append loaded ((slotIndex as string) + ": " + {mat_var}.name + " [" + channelList + "]")',
            f"    appendIfUnique classes ((classOf {mat_var}) as string)",
            "    slotIndex += 1",
            f') catch (append errors ("{mat_name}: " + (getCurrentException())))',
        ])

    for idx, group in enumerate(library_groups, start=len(groups) + 1):
        mat_name = safe_string(f"{material_prefix}{group['name']}")
        mat_var = f"lib_{idx}"

        lines.append("try (")
        lines.extend(_pbr_per_group_lines(
            group,
            idx=idx,
            mat_var=mat_var,
            mat_name=mat_name,
            renderer=renderer,
            include_displacement=include_displacement,
        ))
        lines.extend([
            f"    try (append currentMaterialLibrary {mat_var}) catch ()",
            f'    append libraryLoaded ("lib: " + {mat_var}.name + " [" + channelList + "]")',
            f"    appendIfUnique classes ((classOf {mat_var}) as string)",
            f') catch (append errors ("{mat_name}: " + (getCurrentException())))',
        ])

    lines.extend([
        f'local msg = "Loaded " + (loaded.count as string) + " grouped PBR material(s) into Material Editor slots using {renderer_label}"',
        'if loaded.count > 0 do msg += " [" + loaded[1] + " .. " + loaded[loaded.count] + "]"',
        'if libraryLoaded.count > 0 do msg += " | Library: " + (libraryLoaded.count as string) + " material(s)"',
        'if libraryLoaded.count > 0 do msg += " [" + libraryLoaded[1] + " .. " + libraryLoaded[libraryLoaded.count] + "]"',
        'if classes.count > 0 do msg += " | Classes: " + (classes as string)',
        f'if {unmatched_count} > 0 do msg += " | Unmatched image(s) skipped: {unmatched_count}"',
        f'if {duplicate_count} > 0 do msg += " | Duplicate channel file(s) skipped: {duplicate_count}"',
        'if errors.count > 0 do (',
        '    msg += " | Errors: "',
        '    for i = 1 to errors.count do (',
        '        if i > 1 do msg += "; "',
        '        msg += errors[i]',
        '    )',
        ')',
        "msg",
    ])
    return "(\n    " + "\n    ".join(lines) + "\n)"


def _palette_laydown_impl(
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
    """Load image files from a folder into Compact Material Editor sample slots.

    slot_content="material" creates OpenPBR-first preview materials, wires each
    bitmap into base color, and sets specular color to black. slot_content="bitmap"
    places raw Bitmaptexture maps directly into the palette slots. slot_content
    values like "pbr_material" or "full_pbr" group texture sets by filename and
    create one fully wired PBR material per slot. For grouped mode, material_class
    may be OpenPBRMaterial, PhysicalMaterial, ai_standard_surface,
    RS_Standard_Material, VRayMtl, MaterialX, Std_Surface_Mtl (octane_standard),
    Open_PBR_Surf__Mtl (octane_pbr), or Universal_material (octane_universal);
    OpenPBR is the default. Octane variants build with Image_MTX, Channel_picker
    (for packed ORM), Multiply_MTX (diffuse x AO), and Invert_MTX (gloss to
    roughness), and flip each material slot's *_input_type to 2 so the texture
    actually drives the channel. include_displacement=False skips wiring
    height/displacement maps in grouped PBR mode.

    sample_mode:
        first — first items alphabetically from a flat folder scan (default).
        random — random flat-folder selection (use random_seed for repeatability).
        one_per_subfolder — first texture set / image from each immediate child
            folder (one set per subfolder, asset-pack style).
        random_per_subfolder — one random set / image per child folder.

    overflow_mode:
        truncate — only fill up to max_slots (default).
        palette_then_library — put max_slots in Compact palette; overflow goes to
            currentMaterialLibrary for browsing/assignment.
    """
    start_slot = max(1, min(24, int(start_slot)))
    max_slots = max(1, min(24 - start_slot + 1, int(max_slots)))
    raw_slot_content = (slot_content or "material").strip().lower()
    if raw_slot_content in {"material", "materials", "openpbr", "openpbr_material"}:
        slot_content = "material"
    elif raw_slot_content in {"bitmap", "bitmaps", "map", "maps", "texture", "textures"}:
        slot_content = "bitmap"
    elif raw_slot_content in {
        "pbr", "pbr_material", "pbr_materials", "full_pbr", "full_pbr_material",
        "grouped", "grouped_material", "grouped_materials", "renderer_material",
        "renderer_materials",
    }:
        slot_content = "pbr_material"
    else:
        return (
            f"Unsupported slot_content: {slot_content}. "
            "Use 'material' for OpenPBR preview materials, 'bitmap' for raw Bitmaptexture maps, "
            "or 'pbr_material' for grouped full PBR materials."
        )

    try:
        sample_mode_norm = normalize_sample_mode(sample_mode)
        overflow_mode_norm = normalize_overflow_mode(overflow_mode)
    except ValueError as exc:
        return str(exc)

    renderer = _renderer_from_material_class(material_class)
    if slot_content == "pbr_material" and renderer is None:
        return unsupported_material_class_result(material_class, tool="palette_laydown")

    filter_extra: dict[str, object] = {
        "sample_mode": sample_mode_norm,
        "overflow_mode": overflow_mode_norm,
    }
    if random_seed is not None:
        filter_extra["random_seed"] = random_seed

    unmatched_count = 0
    duplicate_count = 0
    groups = None

    if sample_mode_norm in {"one_per_subfolder", "random_per_subfolder"}:
        picked, sub_meta, unmatched_count, duplicate_count = collect_one_sample_per_subfolder(
            texture_folder,
            recursive=recursive,
            pick_random=sample_mode_norm == "random_per_subfolder",
            random_seed=random_seed,
            slot_content=slot_content,
        )
        filter_extra.update(sub_meta)
        if not picked:
            files = _scan_material_editor_palette_files(texture_folder, recursive)
            if not files:
                return f"No image files found in: {texture_folder}"
            filter_extra["used_subfolder_sampling"] = False
            filter_extra["subfolder_count"] = 0
        elif slot_content == "pbr_material":
            groups = picked
        else:
            files = picked
            groups = None
    else:
        groups = None
        files = _scan_material_editor_palette_files(texture_folder, recursive)
        if not files:
            return f"No image files found in: {texture_folder}"

    if slot_content == "pbr_material":
        if groups is None:
            groups, unmatched, duplicates = _group_texture_files_for_pbr(files, _DEFAULT_CHANNEL_PATTERNS)
            unmatched_count += len(unmatched)
            duplicate_count += len(duplicates)
            if not groups:
                stems = [f.stem for f in files[:10]]
                return f"No texture sets matched any PBR channel pattern. File stems: {stems}"

            if sample_mode_norm == "random":
                import random as _random

                rng = _random.Random(random_seed)
                groups = list(groups)
                rng.shuffle(groups)

        groups, name_filtered = filter_by_name_pattern(
            groups, name_pattern, key=lambda g: str(g["name"]), exclude=exclude_pattern,
        )
        if not groups:
            return (
                f"No texture sets matched name_pattern={name_pattern!r} / "
                f"exclude_pattern={exclude_pattern!r} ({name_filtered} filtered out)"
            )
        if name_filtered:
            filter_extra["name_pattern"] = name_pattern
            if exclude_pattern:
                filter_extra["exclude_pattern"] = exclude_pattern
            filter_extra["name_filtered_out"] = name_filtered

        palette_groups, library_groups = split_palette_and_library(
            groups,
            max_slots=max_slots,
            overflow_mode=overflow_mode_norm,
        )
        filter_extra["palette_count"] = len(palette_groups)
        filter_extra["library_count"] = len(library_groups)
        if overflow_mode_norm == "truncate" and len(groups) > max_slots:
            filter_extra["truncated"] = len(groups) - max_slots

        maxscript = f"""(
    try (
        {_build_material_editor_pbr_palette_maxscript(
            palette_groups,
            start_slot,
            open_editor,
            material_prefix,
            renderer or "openpbr",
            include_displacement=include_displacement,
            unmatched_count=unmatched_count,
            duplicate_count=duplicate_count,
            library_groups=library_groups,
        )}
    ) catch (
        "Error: " + (getCurrentException())
    )
)"""
        response = client.send_command(maxscript)
        raw = response.get("result", "")
        return wrap_material_tool_result(
            str(raw),
            material_class=material_class,
            renderer=renderer or "openpbr",
            tool="palette_laydown",
            slot_content=slot_content,
            **filter_extra,
        )

    if sample_mode_norm == "random":
        import random as _random

        rng = _random.Random(random_seed)
        files = list(files)
        rng.shuffle(files)

    files, name_filtered = filter_by_name_pattern(
        files, name_pattern, key=lambda p: p.stem, exclude=exclude_pattern,
    )
    if not files:
        return (
            f"No image files matched name_pattern={name_pattern!r} / "
            f"exclude_pattern={exclude_pattern!r} ({name_filtered} filtered out)"
        )
    if name_filtered:
        filter_extra["name_pattern"] = name_pattern
        if exclude_pattern:
            filter_extra["exclude_pattern"] = exclude_pattern
        filter_extra["name_filtered_out"] = name_filtered

    selected, library_files = split_palette_and_library(
        files,
        max_slots=max_slots,
        overflow_mode=overflow_mode_norm,
    )
    filter_extra["palette_count"] = len(selected)
    filter_extra["library_count"] = len(library_files)
    if overflow_mode_norm == "truncate" and len(files) > max_slots:
        filter_extra["truncated"] = len(files) - max_slots

    maxscript = f"""(
    try (
        {_build_material_editor_palette_maxscript(selected, start_slot, open_editor, material_prefix, slot_content, library_files=library_files)}
    ) catch (
        "Error: " + (getCurrentException())
    )
)"""
    response = client.send_command(maxscript)
    raw = response.get("result", "")
    return {
        "message": str(raw),
        "slot_content": slot_content,
        **filter_extra,
        "hint": material_class_hint(
            tool="palette_laydown",
            applies_to="palette_laydown with slot_content=pbr_material for full PBR wiring",
        ),
        "supported_material_classes": material_class_hint(tool="palette_laydown")[
            "supported_material_classes"
        ],
    }


def _pbr_maxscript_for_matched(
    matched: dict[str, Path],
    material_name: str,
    renderer: str,
) -> str:
    """Return a PBR builder MAXScript block for *renderer* (no assignment)."""
    return _build_shared_pbr_maxscript(matched, material_name, renderer, None)


@mcp.tool()
def create_shell_material(
    shell_name: str,
    render_material: str = "",
    export_material: str = "",
    assign_to: StrList | None = None,
    texture_folder: str = "",
    render_material_class: str = "",
    export_material_class: str = "",
    render_slot: int = 0,
    viewport_slot: int = 1,
) -> str:
    """Wrap render + export materials in a Shell Material (dual pipeline).

    **Wrap existing materials** — pass ``render_material`` and optionally
    ``export_material`` (material names already in the scene).

    **Build from textures** — pass ``texture_folder`` and ``render_material_class``
    (any PBR class from tripback: OpenPBR, Physical, Arnold, Octane, etc.).
    Optionally set ``export_material_class`` for a different export/viewport material;
    defaults to the same class as the render side. Names default to
    ``{shell_name}_render`` / ``{shell_name}_export`` unless overridden via
    ``render_material`` / ``export_material``.
    """
    preface: list[str] = []
    render_name = (render_material or "").strip()
    export_name = (export_material or "").strip()

    if texture_folder:
        files = _scan_texture_folder(texture_folder)
        if not files:
            return f"No image files found in: {texture_folder}"
        matched = _match_textures_to_channels(files, _DEFAULT_CHANNEL_PATTERNS)
        if not matched:
            stems = [f.stem for f in files[:10]]
            return f"No textures matched any channel pattern. File stems: {stems}"

        render_renderer = _renderer_from_material_class(render_material_class or "OpenPBRMaterial")
        if render_renderer is None:
            return unsupported_material_class_result(
                render_material_class or "OpenPBRMaterial", tool="create_shell_material",
            )
        export_renderer = render_renderer
        if export_material_class.strip():
            export_renderer = _renderer_from_material_class(export_material_class)
            if export_renderer is None:
                return unsupported_material_class_result(
                    export_material_class, tool="create_shell_material",
                )

        if not render_name:
            render_name = f"{shell_name}_render"
        if not export_name:
            export_name = f"{shell_name}_export"

        preface.extend(_extract_material_builder_body(
            _pbr_maxscript_for_matched(matched, render_name, render_renderer),
            mat_var="renderMat",
        ))
        preface.extend(_extract_material_builder_body(
            _pbr_maxscript_for_matched(matched, export_name, export_renderer),
            mat_var="exportMat",
        ))
    elif not render_name:
        return (
            "render_material is required when texture_folder is empty. "
            "Pass existing material names to wrap, or texture_folder + render_material_class to build."
        )

    # Existing-material wrapping has a complete pure-SDK implementation. Texture
    # folder mode remains in Python/MAXScript because its renderer-specific
    # builders intentionally cover third-party material classes.
    if client.native_available and not texture_folder:
        payload = {
            "shell_name": shell_name,
            "render_material": render_name,
            "export_material": export_name,
            "assign_to": list(assign_to or []),
            "render_slot": int(render_slot),
            "viewport_slot": int(viewport_slot),
        }
        try:
            response = client.send_command(
                json.dumps(payload),
                cmd_type="native:create_shell_material",
            )
            return response.get("result", "{}")
        except (MaxBridgeError, RuntimeError) as exc:
            # Compatibility with an older bridge binary that predates the
            # generic Shell Material payload.
            if not is_missing_native_route_error(exc):
                raise

    maxscript = build_shell_wrap_maxscript(
        shell_name=shell_name,
        render_material=render_name,
        export_material=export_name,
        assign_to=assign_to,
        render_slot=render_slot,
        viewport_slot=viewport_slot,
        preface_lines=preface or None,
    )

    maxscript = f"""(
    try (
        {maxscript}
    ) catch (
        "{{\\"status\\":\\"error\\",\\"error\\":\\"" + (getCurrentException()) + "\\"}}"
    )
)"""

    response = client.send_command(maxscript)
    return response.get("result", "{}")
