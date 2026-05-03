"""Orientation and pivot diagnostics for live 3ds Max nodes."""

from __future__ import annotations

import json as _json
from typing import Optional

from ..coerce import StrList
from ..server import mcp, client
from src.helpers.maxscript import safe_string


def _maxscript_name_array(names: StrList) -> str:
    return "#(" + ", ".join(f'"{safe_string(name)}"' for name in names) + ")"


@mcp.tool()
def analyze_node_orientation(
    names: Optional[StrList] = None,
    pattern: str = "",
    include_children: bool = False,
    max_nodes: int = 20,
) -> str:
    """Inspect world-space orientation, pivots, bounding boxes, and local axes for scene nodes."""
    if client.native_available:
        try:
            params: dict = {
                "include_children": include_children,
                "max_nodes": max_nodes,
            }
            if names:
                params["names"] = names
            if pattern:
                params["pattern"] = pattern
            response = client.send_command(_json.dumps(params), cmd_type="native:analyze_node_orientation")
            return response.get("result", "")
        except RuntimeError:
            pass

    safe_pattern = safe_string(pattern)
    max_count = max(1, min(int(max_nodes or 20), 100))

    if names:
        target_expr = f"""
            local nameList = {_maxscript_name_array(names)}
            for n in nameList do (
                local obj = getNodeByName n
                if obj != undefined do append targets obj
            )
        """
    elif pattern:
        target_expr = f"""
            for obj in objects where matchPattern obj.name pattern:"{safe_pattern}" do append targets obj
        """
    else:
        target_expr = "for obj in selection do append targets obj"

    child_expr = """
        if includeChildren do (
            local withChildren = #()
            fn addNodeAndChildren n = (
                append withChildren n
                for c in n.children do addNodeAndChildren c
            )
            for n in targets do addNodeAndChildren n
            targets = makeUniqueArray withChildren
        )
    """ if include_children else ""

    maxscript = f"""(
        local targets = #()
        local includeChildren = {str(bool(include_children)).lower()}
        {target_expr}
        targets = makeUniqueArray targets
        {child_expr}

        fn jsonEscape value = (
            local s = value as string
            s = substituteString s "\\\\" "\\\\\\\\"
            s = substituteString s "\\\"" "\\\\\\\""
            s = substituteString s "\\n" "\\\\n"
            s = substituteString s "\\r" "\\\\r"
            s
        )

        fn num value = (
            if value == undefined then "null" else (formattedPrint (value as float) format:".6f")
        )

        fn vec3Json p = (
            "[" + num p.x + "," + num p.y + "," + num p.z + "]"
        )

        fn safeNormalize p = (
            local len = length p
            if len < 0.000001 then [0,0,0] else (p / len)
        )

        fn matrixJson m = (
            "[" +
                vec3Json m.row1 + "," +
                vec3Json m.row2 + "," +
                vec3Json m.row3 + "," +
                vec3Json m.row4 +
            "]"
        )

        fn nodeJson obj = (
            local tm = obj.transform
            local pivot = tm.row4
            local bbMin = obj.min
            local bbMax = obj.max
            local center = (bbMin + bbMax) * 0.5
            local dims = bbMax - bbMin
            local pivotToCenter = center - pivot
            local xAxis = safeNormalize tm.row1
            local yAxis = safeNormalize tm.row2
            local zAxis = safeNormalize tm.row3
            local parentName = if obj.parent != undefined then ("\\\"" + jsonEscape obj.parent.name + "\\\"") else "null"

            "{{" +
                "\\\"name\\\":\\\"" + jsonEscape obj.name + "\\\"," +
                "\\\"class\\\":\\\"" + jsonEscape ((classOf obj) as string) + "\\\"," +
                "\\\"superclass\\\":\\\"" + jsonEscape ((superClassOf obj) as string) + "\\\"," +
                "\\\"parent\\\":" + parentName + "," +
                "\\\"pivot\\\":" + vec3Json pivot + "," +
                "\\\"position\\\":" + vec3Json obj.pos + "," +
                "\\\"bbox\\\":{{" +
                    "\\\"min\\\":" + vec3Json bbMin + "," +
                    "\\\"max\\\":" + vec3Json bbMax + "," +
                    "\\\"center\\\":" + vec3Json center + "," +
                    "\\\"dimensions\\\":" + vec3Json dims +
                "}}," +
                "\\\"pivotToBBoxCenter\\\":" + vec3Json pivotToCenter + "," +
                "\\\"localAxesWorld\\\":{{" +
                    "\\\"x\\\":" + vec3Json xAxis + "," +
                    "\\\"y\\\":" + vec3Json yAxis + "," +
                    "\\\"z\\\":" + vec3Json zAxis +
                "}}," +
                "\\\"worldMatrixRows\\\":" + matrixJson tm +
            "}}"
        )

        local systemUnitType = try ((units.SystemType) as string) catch ("unknown")
        local displayUnitType = try ((units.DisplayType) as string) catch ("unknown")
        local systemUnitScale = try (units.SystemScale as float) catch (1.0)

        local result = "{{" +
            "\\\"space\\\":{{" +
                "\\\"coordinateSystem\\\":\\\"3ds Max world\\\"," +
                "\\\"upAxis\\\":\\\"Z\\\"," +
                "\\\"groundPlane\\\":\\\"XY\\\"," +
                "\\\"rightHanded\\\":true" +
            "}}," +
            "\\\"units\\\":{{" +
                "\\\"systemType\\\":\\\"" + jsonEscape systemUnitType + "\\\"," +
                "\\\"displayType\\\":\\\"" + jsonEscape displayUnitType + "\\\"," +
                "\\\"systemScale\\\":" + num systemUnitScale +
            "}}," +
            "\\\"query\\\":{{" +
                "\\\"pattern\\\":\\\"" + jsonEscape "{safe_pattern}" + "\\\"," +
                "\\\"includeChildren\\\":" + (if includeChildren then "true" else "false") + "," +
                "\\\"maxNodes\\\":" + ({max_count} as string) +
            "}}," +
            "\\\"nodes\\\":["

        local count = amin targets.count {max_count}
        for i = 1 to count do (
            if i > 1 do result += ","
            result += nodeJson targets[i]
        )
        result += "],\\\"truncated\\\":" + (if targets.count > count then "true" else "false") + "}}"
        result
    )"""
    response = client.send_command(maxscript)
    return response.get("result", "")
