"""Grid-based 2D floor plan generator for 3ds Max.

Outputs a SplineShape with wall segments and optional Text labels for rooms.
Rooms are defined by grid cells; walls are auto-detected at cell boundaries;
doors cut gaps in wall segments.

All coordinate math is done in Python — MAXScript only creates the final objects.
"""

from __future__ import annotations

import json
from typing import Any

from ..server import mcp, client
from ..helpers.construction import DOOR_OPENING_WIDTH, LABEL_SIZE


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _create_dummy(name: str, pos: list[float], box_size: list[float]) -> str:
    safe = _safe_name(name)
    px, py, pz = pos
    bx, by, bz = box_size
    cmd = f"""(
        local d = Dummy name:"{safe}" pos:[{px},{py},{pz}] boxsize:[{bx},{by},{bz}]
        d.pivot = [{px},{py},{pz - bz / 2.0}]
        d.name
    )"""
    resp = client.send_command(cmd)
    return resp.get("result", name)


def _parent_objects(children: list[str], parent: str) -> str:
    names_arr = "#(" + ", ".join(f'"{_safe_name(n)}"' for n in children) + ")"
    safe_p = _safe_name(parent)
    cmd = f"""(
        local parentObj = getNodeByName "{safe_p}"
        local childNames = {names_arr}
        local cnt = 0
        for n in childNames do (
            local c = getNodeByName n
            if c != undefined and parentObj != undefined do (
                c.parent = parentObj
                cnt += 1
            )
        )
        "Parented " + (cnt as string) + " objects under " + parentObj.name
    )"""
    resp = client.send_command(cmd)
    return resp.get("result", "")


# ---------------------------------------------------------------------------
# Grid / wall logic (pure Python)
# ---------------------------------------------------------------------------

# Edge directions: each cell (col, row) has 4 edges.
# We normalise edges as tuples of two endpoints so shared edges are identical.
# Convention: edges are ((x1,y1), (x2,y2)) with the smaller tuple first.

def _cell_edges(col: int, row: int) -> list[tuple]:
    """Return the 4 boundary edges of a cell as normalised endpoint pairs.

    Cell (col, row) occupies [col, col+1] x [row, row+1] in grid space.
    Returns: [(edge, direction), ...] where direction is the neighbouring
    cell that shares this edge (or None for outer edges).
    """
    # Corners: bottom-left (col, row), bottom-right (col+1, row),
    #          top-right (col+1, row+1), top-left (col, row+1)
    bottom = ((col, row), (col + 1, row))        # neighbor below: (col, row-1)
    right  = ((col + 1, row), (col + 1, row + 1))  # neighbor right: (col+1, row)
    top    = ((col, row + 1), (col + 1, row + 1))  # neighbor above: (col, row+1)
    left   = ((col, row), (col, row + 1))          # neighbor left:  (col-1, row)
    return [
        (bottom, (col, row - 1)),
        (right,  (col + 1, row)),
        (top,    (col, row + 1)),
        (left,   (col - 1, row)),
    ]


def _build_occupancy(rooms: list[dict]) -> dict[tuple, str]:
    """Map (col, row) -> room name."""
    grid = {}
    for room in rooms:
        name = room.get("name", "Room")
        for cell in room.get("cells", []):
            col, row = int(cell[0]), int(cell[1])
            grid[(col, row)] = name
    return grid


def _extract_wall_edges(grid: dict[tuple, str]) -> list[tuple]:
    """Find all edges that are walls (boundary between different rooms or exterior).

    Returns list of (edge, room_a, room_b) where room_b may be None (exterior).
    edge is ((x1,y1), (x2,y2)) in grid coordinates.
    """
    walls = {}  # edge -> (room_a, room_b)
    for (col, row), room_name in grid.items():
        for edge, neighbor_cell in _cell_edges(col, row):
            # Normalise edge so both sides agree on the same key
            norm_edge = tuple(sorted(edge))
            neighbor_room = grid.get(neighbor_cell)
            if neighbor_room == room_name:
                # Same room — not a wall; remove if previously added
                walls.pop(norm_edge, None)
            elif norm_edge not in walls:
                walls[norm_edge] = (room_name, neighbor_room)
    return [(e, ra, rb) for e, (ra, rb) in walls.items()]


def _is_horizontal(edge: tuple) -> bool:
    """Edge is horizontal if both endpoints have the same Y."""
    return edge[0][1] == edge[1][1]


def _is_vertical(edge: tuple) -> bool:
    """Edge is vertical if both endpoints have the same X."""
    return edge[0][0] == edge[1][0]


def _merge_collinear(wall_edges: list[tuple]) -> list[tuple]:
    """Merge collinear, contiguous wall segments into longer lines.

    Input:  list of (edge, room_a, room_b)
    Output: list of (merged_edge, room_a, room_b)
    Edges within a merged segment all share the same (room_a, room_b) pair
    (normalised as a frozenset for matching).
    """
    # Group by orientation + fixed coordinate + room pair
    h_groups: dict[tuple, list] = {}  # (y, rooms_key) -> [edges]
    v_groups: dict[tuple, list] = {}  # (x, rooms_key) -> [edges]

    for edge, ra, rb in wall_edges:
        rooms_key = frozenset([ra, rb])
        if _is_horizontal(edge):
            y = edge[0][1]
            key = (y, rooms_key)
            h_groups.setdefault(key, []).append((edge, ra, rb))
        elif _is_vertical(edge):
            x = edge[0][0]
            key = (x, rooms_key)
            v_groups.setdefault(key, []).append((edge, ra, rb))

    merged = []

    # Merge horizontal segments
    for (y, rooms_key), segs in h_groups.items():
        # Sort by X start
        intervals = sorted([(min(e[0][0], e[1][0]), max(e[0][0], e[1][0]), ra, rb)
                             for e, ra, rb in segs])
        # Merge contiguous
        cur_start, cur_end, ra, rb = intervals[0]
        for i in range(1, len(intervals)):
            s, e, ra2, rb2 = intervals[i]
            if s <= cur_end:  # contiguous or overlapping
                cur_end = max(cur_end, e)
            else:
                merged.append((((cur_start, y), (cur_end, y)), ra, rb))
                cur_start, cur_end, ra, rb = s, e, ra2, rb2
        merged.append((((cur_start, y), (cur_end, y)), ra, rb))

    # Merge vertical segments
    for (x, rooms_key), segs in v_groups.items():
        intervals = sorted([(min(e[0][1], e[1][1]), max(e[0][1], e[1][1]), ra, rb)
                             for e, ra, rb in segs])
        cur_start, cur_end, ra, rb = intervals[0]
        for i in range(1, len(intervals)):
            s, e, ra2, rb2 = intervals[i]
            if s <= cur_end:
                cur_end = max(cur_end, e)
            else:
                merged.append((((x, cur_start), (x, cur_end)), ra, rb))
                cur_start, cur_end, ra, rb = s, e, ra2, rb2
        merged.append((((x, cur_start), (x, cur_end)), ra, rb))

    return merged


def _find_shared_wall(
    walls: list[tuple], room_a: str, room_b: str | None
) -> list[int]:
    """Find indices of wall segments between room_a and room_b.

    room_b=None means exterior wall of room_a.
    """
    indices = []
    for i, (edge, ra, rb) in enumerate(walls):
        pair = {ra, rb}
        if room_b is None:
            # Exterior wall: one side is room_a, other side is None
            if room_a in pair and None in pair:
                indices.append(i)
        else:
            if room_a in pair and room_b in pair:
                indices.append(i)
    return indices


def _segment_length(edge: tuple) -> float:
    """Length of a segment in grid units."""
    dx = edge[1][0] - edge[0][0]
    dy = edge[1][1] - edge[0][1]
    return (dx * dx + dy * dy) ** 0.5


def _cut_door(
    walls: list[tuple],
    door: dict,
    cell_size: float,
) -> list[tuple]:
    """Cut a door opening in the wall list, returning updated wall list.

    Door spec: {between: [roomA, roomB|null], position: 0-1, width: cm}
    """
    between = door.get("between", [])
    if len(between) < 2:
        return walls

    room_a = between[0]
    room_b = between[1]  # may be None for exterior
    position = door.get("position", 0.5)
    door_width = door.get("width", DOOR_OPENING_WIDTH)

    # Convert door width from cm to grid units
    door_grid_width = door_width / cell_size

    # Find shared wall segments
    shared_indices = _find_shared_wall(walls, room_a, room_b)
    if not shared_indices:
        return walls

    # Pick the longest shared wall segment for the door
    best_idx = max(shared_indices, key=lambda i: _segment_length(walls[i][0]))
    edge, ra, rb = walls[best_idx]

    # Compute door gap position along the segment
    seg_len = _segment_length(edge)
    if door_grid_width >= seg_len:
        # Door wider than wall — remove entire segment
        walls.pop(best_idx)
        return walls

    # Direction vector
    dx = edge[1][0] - edge[0][0]
    dy = edge[1][1] - edge[0][1]

    # Door centre along segment (0..1)
    t_center = max(0.0, min(1.0, position))
    half_door = door_grid_width / (2.0 * seg_len)
    t_start = max(0.0, t_center - half_door)
    t_end = min(1.0, t_center + half_door)

    # Create sub-segments (before gap and after gap)
    new_segments = []
    if t_start > 0.001:
        p1 = edge[0]
        p2 = (edge[0][0] + dx * t_start, edge[0][1] + dy * t_start)
        new_segments.append(((p1, p2), ra, rb))
    if t_end < 0.999:
        p1 = (edge[0][0] + dx * t_end, edge[0][1] + dy * t_end)
        p2 = edge[1]
        new_segments.append(((p1, p2), ra, rb))

    # Replace original segment with sub-segments
    walls.pop(best_idx)
    for seg in reversed(new_segments):
        walls.insert(best_idx, seg)

    return walls


def _room_centroid(cells: list[list[int]]) -> tuple[float, float]:
    """Centroid of a room's cells in grid coordinates."""
    if not cells:
        return (0.0, 0.0)
    cx = sum(c[0] + 0.5 for c in cells) / len(cells)
    cy = sum(c[1] + 0.5 for c in cells) / len(cells)
    return (cx, cy)


def _grid_to_world(
    gx: float, gy: float, cell_size: float, origin: list[float],
) -> tuple[float, float]:
    """Convert grid coordinate to world XY."""
    return (origin[0] + gx * cell_size, origin[1] + gy * cell_size)


# ---------------------------------------------------------------------------
# MCP Tool
# ---------------------------------------------------------------------------

@mcp.tool()
def build_floor_plan(
    location: list[float] = [0, 0, 0],
    cell_size: float = 100.0,
    rooms: list[dict[str, Any]] = [],
    doors: list[dict[str, Any]] = [],
    options: dict[str, Any] | None = None,
) -> str:
    """Build a 2D floor plan from grid-based room definitions.

    Creates a SplineShape with wall segments and optional room labels.
    Rooms are defined by grid cells; walls auto-detected at boundaries;
    doors cut gaps in wall segments.

    Args:
        location: Centre point [x, y, z]. Z=0 means ground level.
        cell_size: Grid cell size in cm (default 100 = 1 meter per cell).
        rooms: List of room definitions. Each: {"name": str, "cells": [[col,row], ...]}.
        doors: List of door specs. Each: {"between": ["RoomA", "RoomB"|null], "position": 0-1, "width": cm}.
        options: Optional overrides:
            name_prefix (str, default "FP"),
            show_labels (bool, default true),
            label_size (float, default 20),
            extrude_height (float) — add Extrude modifier for 3D walls,
            wall_thickness (float) — add Shell modifier,
            wall_color ([r,g,b]) — wirecolor for wall spline,
            label_color ([r,g,b]) — wirecolor for text labels.

    Returns:
        JSON with created objects and organiser dummy name.
    """
    opts = options or {}
    prefix = opts.get("name_prefix", "FP")
    show_labels = opts.get("show_labels", True)
    label_size = opts.get("label_size", LABEL_SIZE)
    extrude_height = opts.get("extrude_height", None)
    wall_thickness = opts.get("wall_thickness", None)
    wall_color = opts.get("wall_color", [255, 255, 255])
    label_color = opts.get("label_color", [80, 80, 80])

    ox, oy, oz = location

    # 1. Build occupancy grid
    grid = _build_occupancy(rooms)
    if not grid:
        return json.dumps({"error": "No rooms/cells defined."})

    # 2. Extract wall edges
    raw_walls = _extract_wall_edges(grid)

    # 3. Merge collinear segments
    walls = _merge_collinear(raw_walls)

    # 4. Cut door openings
    for door in doors:
        walls = _cut_door(walls, door, cell_size)

    # 5. Convert to world coords and build MAXScript
    created = []

    # --- Wall spline ---
    spline_idx = 1
    spline_lines = []
    spline_lines.append(f'ss = SplineShape name:"{_safe_name(prefix)}_Walls" pos:[{ox},{oy},{oz}]')

    for edge, ra, rb in walls:
        (gx1, gy1), (gx2, gy2) = edge
        wx1, wy1 = _grid_to_world(gx1, gy1, cell_size, location)
        wx2, wy2 = _grid_to_world(gx2, gy2, cell_size, location)
        # Positions relative to the SplineShape's pos (which is at origin)
        rx1, ry1 = wx1 - ox, wy1 - oy
        rx2, ry2 = wx2 - ox, wy2 - oy
        spline_lines.append(f"addNewSpline ss")
        spline_lines.append(f"addKnot ss {spline_idx} #corner #line [{rx1},{ry1},{0}]")
        spline_lines.append(f"addKnot ss {spline_idx} #corner #line [{rx2},{ry2},{0}]")
        spline_idx += 1

    spline_lines.append("updateShape ss")
    wr, wg, wb = wall_color
    spline_lines.append(f"ss.wirecolor = color {wr} {wg} {wb}")

    # Optional extrude
    if extrude_height is not None:
        spline_lines.append(f"addModifier ss (Extrude amount:{extrude_height})")

    # Optional shell (only if extruded)
    if wall_thickness is not None and extrude_height is not None:
        spline_lines.append(f"addModifier ss (Shell innerAmount:0 outerAmount:{wall_thickness})")

    spline_lines.append("ss.name")

    cmd = "(\n" + "\n".join(spline_lines) + "\n)"
    resp = client.send_command(cmd)
    wall_name = resp.get("result", f"{prefix}_Walls")
    created.append(wall_name)

    # --- Room labels ---
    if show_labels:
        for room in rooms:
            rname = room.get("name", "Room")
            cells = room.get("cells", [])
            if not cells:
                continue
            gcx, gcy = _room_centroid(cells)
            wx, wy = _grid_to_world(gcx, gcy, cell_size, location)
            safe = _safe_name(f"{prefix}_{rname}")
            lr, lg, lb = label_color
            label_cmd = f"""(
                txt = Text name:"{safe}" text:"{_safe_name(rname)}" size:{label_size} pos:[{wx},{wy},{oz}] alignment:2
                txt.wirecolor = color {lr} {lg} {lb}
                txt.name
            )"""
            resp = client.send_command(label_cmd)
            label_name = resp.get("result", f"{prefix}_{rname}")
            created.append(label_name)

    # --- Organiser Dummy ---
    # Compute bounding box of all grid cells
    all_cols = [c[0] for c in grid.keys()]
    all_rows = [c[1] for c in grid.keys()]
    min_col, max_col = min(all_cols), max(all_cols) + 1
    min_row, max_row = min(all_rows), max(all_rows) + 1

    world_min_x = ox + min_col * cell_size
    world_max_x = ox + max_col * cell_size
    world_min_y = oy + min_row * cell_size
    world_max_y = oy + max_row * cell_size

    bbox_cx = (world_min_x + world_max_x) / 2.0
    bbox_cy = (world_min_y + world_max_y) / 2.0
    bbox_w = world_max_x - world_min_x
    bbox_d = world_max_y - world_min_y
    bbox_h = extrude_height if extrude_height else 1.0

    dummy_name = _create_dummy(
        f"{prefix}_FloorPlan",
        [bbox_cx, bbox_cy, oz + bbox_h / 2.0],
        [bbox_w, bbox_d, bbox_h],
    )
    _parent_objects(created, dummy_name)

    return json.dumps({
        "organiser": dummy_name,
        "walls": wall_name,
        "labels": [n for n in created if n != wall_name],
        "wall_segments": len(walls),
        "rooms": len(rooms),
        "doors": len(doors),
    })
