"""Grid-based object placement tools for 3ds Max.

Low-level tools that let Claude place objects on a mathematical grid —
no guessing positions.  Every coordinate is computed in Python.
"""

from __future__ import annotations

import json
from typing import Any

from ..server import mcp, client
from ..helpers.construction import grid_position, circular_position


def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _create_at(
    obj_type: str,
    name: str,
    cx: float, cy: float, cz: float,
    w: float, d: float, h: float,
    color: tuple[int, int, int] | None = None,
) -> str:
    """Create a primitive centred at (cx, cy, cz).

    Supports Box, Cylinder, Sphere, and Plane.
    For Box: pivot at centre-bottom, so we shift pos Z down by h/2.
    For Sphere/Cylinder: similar centre logic.
    """
    safe = _safe_name(name)
    t = obj_type.lower()

    if t == "box":
        pos_z = cz - h / 2.0
        params = (
            f'name:"{safe}" width:{w} length:{d} height:{h} '
            f"pos:[{cx},{cy},{pos_z}] "
            f"lengthsegs:1 widthsegs:1 heightsegs:1"
        )
        ctor = "Box"
    elif t == "cylinder":
        radius = min(w, d) / 2.0
        pos_z = cz - h / 2.0
        params = f'name:"{safe}" radius:{radius} height:{h} pos:[{cx},{cy},{pos_z}]'
        ctor = "Cylinder"
    elif t == "sphere":
        radius = min(w, d, h) / 2.0
        params = f'name:"{safe}" radius:{radius} pos:[{cx},{cy},{cz}]'
        ctor = "Sphere"
    elif t == "plane":
        params = f'name:"{safe}" width:{w} length:{d} pos:[{cx},{cy},{cz}]'
        ctor = "Plane"
    else:
        # Fallback: treat as Box
        pos_z = cz - h / 2.0
        params = (
            f'name:"{safe}" width:{w} length:{d} height:{h} '
            f"pos:[{cx},{cy},{pos_z}]"
        )
        ctor = obj_type

    if color:
        r, g, b = color
        cmd = f'(local o = {ctor} {params}; o.wirecolor = color {r} {g} {b}; o.name)'
    else:
        cmd = f"({ctor} {params}).name"

    resp = client.send_command(cmd)
    return resp.get("result", name)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def place_on_grid(
    type: str = "Box",
    grid_origin: list[float] = [0, 0, 0],
    grid_index: list[float] = [0, 0, 0],
    cell_size: float = 100.0,
    object_size: list[float] = [100, 100, 100],
    name: str = "",
    color: list[int] | None = None,
) -> str:
    """Place a single object at a grid cell position.

    Position is computed as: ``grid_origin + grid_index * cell_size``.
    *grid_index* can be fractional (e.g. [0.5, 0, 0]) for sub-grid precision.

    Args:
        type: Object type — "Box", "Cylinder", "Sphere", "Plane".
        grid_origin: Grid centre [x, y, z].
        grid_index: Cell index [ix, iy, iz] — can be fractional.
        cell_size: Spacing between grid cells (cm).
        object_size: [width, depth, height] of the object (cm).
        name: Object name (auto-generated if empty).
        color: Optional wirecolor [r, g, b] (0-255).

    Returns:
        JSON with the created object name and its world position.
    """
    ox, oy, oz = grid_origin if len(grid_origin) >= 3 else (0, 0, 0)
    ix, iy, iz = grid_index if len(grid_index) >= 3 else (0, 0, 0)
    w, d, h = object_size if len(object_size) >= 3 else (100, 100, 100)

    world_x = grid_position(ox, ix, cell_size)
    world_y = grid_position(oy, iy, cell_size)
    world_z = grid_position(oz, iz, cell_size)

    obj_name = name or f"Grid_{type}_{int(ix)}_{int(iy)}_{int(iz)}"
    col = tuple(color) if color and len(color) >= 3 else None

    actual_name = _create_at(type, obj_name, world_x, world_y, world_z, w, d, h, col)

    return json.dumps({
        "name": actual_name,
        "position": [world_x, world_y, world_z],
        "size": [w, d, h],
    })


@mcp.tool()
def place_grid_array(
    type: str = "Box",
    grid_origin: list[float] = [0, 0, 0],
    rows: int = 3,
    cols: int = 3,
    layers: int = 1,
    cell_size: float = 100.0,
    object_size: list[float] = [80, 80, 80],
    name_prefix: str = "Grid",
    color: list[int] | None = None,
) -> str:
    """Fill a 3D grid with objects.

    Creates ``rows * cols * layers`` objects evenly spaced around *grid_origin*.

    Args:
        type: Object type — "Box", "Cylinder", "Sphere", "Plane".
        grid_origin: Grid centre [x, y, z].
        rows: Number of rows (Y axis).
        cols: Number of columns (X axis).
        layers: Number of layers (Z axis).
        cell_size: Spacing between cells (cm).
        object_size: [width, depth, height] of each object (cm).
        name_prefix: Prefix for auto-generated names.
        color: Optional wirecolor [r, g, b] (0-255).

    Returns:
        JSON with list of created objects.
    """
    ox, oy, oz = grid_origin if len(grid_origin) >= 3 else (0, 0, 0)
    w, d, h = object_size if len(object_size) >= 3 else (80, 80, 80)
    col = tuple(color) if color and len(color) >= 3 else None

    created: list[dict[str, Any]] = []

    for lz in range(layers):
        for iy in range(rows):
            for ix in range(cols):
                # Centre the grid around the origin
                idx_x = ix - (cols - 1) / 2.0
                idx_y = iy - (rows - 1) / 2.0
                idx_z = lz  # layers stack upward from origin

                world_x = grid_position(ox, idx_x, cell_size)
                world_y = grid_position(oy, idx_y, cell_size)
                world_z = grid_position(oz, idx_z, cell_size)

                obj_name = f"{name_prefix}_{ix}_{iy}_{lz}"
                actual_name = _create_at(
                    type, obj_name, world_x, world_y, world_z,
                    w, d, h, col,
                )
                created.append({
                    "name": actual_name,
                    "position": [world_x, world_y, world_z],
                })

    return json.dumps({"count": len(created), "objects": created})


@mcp.tool()
def place_circle(
    type: str = "Box",
    center: list[float] = [0, 0, 0],
    radius: float = 200.0,
    count: int = 8,
    object_size: list[float] = [30, 30, 30],
    name_prefix: str = "Circle",
    color: list[int] | None = None,
) -> str:
    """Place objects evenly around a circle (e.g. fence posts, village houses, plaza benches).

    Uses ``cos(angle) * radius`` / ``sin(angle) * radius`` — the circular
    placement pattern from the Unreal MCP castle and town builders.

    Args:
        type: Object type — "Box", "Cylinder", "Sphere".
        center: Circle centre [x, y, z].
        radius: Distance from centre to each object.
        count: Number of objects to place.
        object_size: [width, depth, height] of each object (cm).
        name_prefix: Prefix for auto-generated names.
        color: Optional wirecolor [r, g, b] (0-255).

    Returns:
        JSON with list of created objects.
    """
    import math

    ox, oy, oz = center if len(center) >= 3 else (0, 0, 0)
    w, d, h = object_size if len(object_size) >= 3 else (30, 30, 30)
    col = tuple(color) if color and len(color) >= 3 else None

    created: list[dict[str, Any]] = []

    for i in range(count):
        angle = 2.0 * math.pi * i / count
        wx, wy = circular_position(ox, oy, radius, angle)

        obj_name = f"{name_prefix}_{i}"
        actual_name = _create_at(type, obj_name, wx, wy, oz, w, d, h, col)
        created.append({
            "name": actual_name,
            "position": [wx, wy, oz],
        })

    return json.dumps({"count": len(created), "objects": created})
