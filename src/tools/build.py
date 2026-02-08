"""Grid-based construction tools for 3ds Max.

All coordinate math is done in Python.  Only final positions / sizes are sent
to 3ds Max as MAXScript ``Box`` (or ``Prism``) creation commands.

Principle: **zero rotations** — every piece is an axis-aligned primitive with
explicit width / length / height so there is no pivot or rotation guesswork.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

from ..server import mcp, client
from ..helpers.construction import (
    WALL_THICKNESS,
    FLOOR_THICKNESS,
    DOOR_WIDTH,
    DOOR_HEIGHT,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    WINDOW_SILL_HEIGHT,
    ROOF_OVERHANG,
    ROOF_THICKNESS,
    STEP_HEIGHT,
    STEP_DEPTH,
    STEP_WIDTH,
    FOUNDATION_EXTRA,
    FOUNDATION_THICKNESS,
    FLOOR_HEIGHT,
    PILLAR_THICKNESS,
    CANOPY_THICKNESS,
    BATTLEMENT_HEIGHT,
    BATTLEMENT_SPACING,
    TOWER_RADIUS,
    MOAT_WIDTH,
    parabolic_z,
    circular_position,
    arch_z,
    arch_x,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _create_box(
    name: str,
    cx: float, cy: float, cz: float,
    w: float, d: float, h: float,
    color: tuple[int, int, int] | None = None,
) -> str:
    """Create an axis-aligned Box centred at (cx, cy, cz).

    3ds Max Box origin is at the centre of its base, so we offset Z down by
    half height so the *geometric centre* sits at the requested cz.
    """
    safe = _safe_name(name)
    # Box pivot is at centre-bottom → we position at (cx, cy, cz - h/2)
    pos_z = cz - h / 2.0
    params = (
        f'name:"{safe}" '
        f"length:{d} width:{w} height:{h} "
        f"pos:[{cx},{cy},{pos_z}] "
        f"lengthsegs:1 widthsegs:1 heightsegs:1"
    )
    if color:
        r, g, b = color
        cmd = f'(local b = Box {params}; b.wirecolor = color {r} {g} {b}; b.name)'
    else:
        cmd = f"(Box {params}).name"
    resp = client.send_command(cmd)
    return resp.get("result", name)


def _create_prism(
    name: str,
    cx: float, cy: float, cz: float,
    side1: float, side2: float, side3: float, height: float,
    color: tuple[int, int, int] | None = None,
) -> str:
    """Create a Prism (triangular cross-section) at centre-bottom position."""
    safe = _safe_name(name)
    params = (
        f'name:"{safe}" '
        f"side1Length:{side1} side2Length:{side2} side3Length:{side3} height:{height} "
        f"pos:[{cx},{cy},{cz}]"
    )
    if color:
        r, g, b = color
        cmd = f'(local p = Prism {params}; p.wirecolor = color {r} {g} {b}; p.name)'
    else:
        cmd = f"(Prism {params}).name"
    resp = client.send_command(cmd)
    return resp.get("result", name)


def _parent_objects(children: list[str], parent: str) -> str:
    """Parent a list of objects under *parent* in one MAXScript call."""
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


def _create_dummy(name: str, pos: list[float], box_size: list[float]) -> str:
    """Create a Dummy helper at *pos* with given boxsize, pivot at base Z."""
    safe = _safe_name(name)
    px, py, pz = pos
    bx, by, bz = box_size
    cmd = f"""(
        local d = Dummy name:"{safe}" pos:[{px},{py},{pz}] boxsize:[{bx},{by},{bz}]
        d.pivot = [{px},{py},{pz - bz/2.0}]
        d.name
    )"""
    resp = client.send_command(cmd)
    return resp.get("result", name)


def _create_cylinder(
    name: str,
    cx: float, cy: float, cz: float,
    radius: float, height: float,
    color: tuple[int, int, int] | None = None,
) -> str:
    """Create a Cylinder centred at (cx, cy, cz). Pivot at centre-bottom."""
    safe = _safe_name(name)
    pos_z = cz - height / 2.0
    params = f'name:"{safe}" radius:{radius} height:{height} pos:[{cx},{cy},{pos_z}] sides:18'
    if color:
        r, g, b = color
        cmd = f'(local cyl = Cylinder {params}; cyl.wirecolor = color {r} {g} {b}; cyl.name)'
    else:
        cmd = f"(Cylinder {params}).name"
    resp = client.send_command(cmd)
    return resp.get("result", name)


def _create_sphere(
    name: str,
    cx: float, cy: float, cz: float,
    radius: float,
    color: tuple[int, int, int] | None = None,
) -> str:
    """Create a Sphere centred at (cx, cy, cz)."""
    safe = _safe_name(name)
    params = f'name:"{safe}" radius:{radius} pos:[{cx},{cy},{cz}] segs:16'
    if color:
        r, g, b = color
        cmd = f'(local sph = Sphere {params}; sph.wirecolor = color {r} {g} {b}; sph.name)'
    else:
        cmd = f"(Sphere {params}).name"
    resp = client.send_command(cmd)
    return resp.get("result", name)


def _create_cone(
    name: str,
    cx: float, cy: float, cz: float,
    radius: float, height: float,
    color: tuple[int, int, int] | None = None,
) -> str:
    """Create a Cone (point-up) centred at (cx, cy, cz). Pivot at centre-bottom."""
    safe = _safe_name(name)
    pos_z = cz - height / 2.0
    params = f'name:"{safe}" radius1:{radius} radius2:0 height:{height} pos:[{cx},{cy},{pos_z}] sides:18'
    if color:
        r, g, b = color
        cmd = f'(local cn = Cone {params}; cn.wirecolor = color {r} {g} {b}; cn.name)'
    else:
        cmd = f"(Cone {params}).name"
    resp = client.send_command(cmd)
    return resp.get("result", name)


# ---------------------------------------------------------------------------
# Variation / randomisation helpers
# ---------------------------------------------------------------------------

def _init_variation(options: dict[str, Any]) -> tuple[random.Random, float]:
    """Return (rng, variation) from build options.

    ``variation=0`` (default) → fully deterministic, all jitter is a no-op.
    ``seed`` makes randomisation reproducible across runs.
    """
    seed = options.get("seed", None)
    variation = max(0.0, min(1.0, float(options.get("variation", 0.0))))
    rng = random.Random(seed)
    return rng, variation


def _jitter_color(
    color: tuple[int, int, int], rng: random.Random, variation: float,
    amount: int = 15,
) -> tuple[int, int, int]:
    """RGB jitter.  No-op when variation=0."""
    if variation <= 0:
        return color
    jit = int(amount * variation)
    r, g, b = color
    return (
        max(0, min(255, r + rng.randint(-jit, jit))),
        max(0, min(255, g + rng.randint(-jit, jit))),
        max(0, min(255, b + rng.randint(-jit, jit))),
    )


def _jitter_value(
    value: float, rng: random.Random, variation: float,
    factor: float = 0.15,
) -> float:
    """Scale a value by ±factor.  No-op when variation=0."""
    if variation <= 0:
        return value
    return value * (1.0 + rng.uniform(-factor, factor) * variation)


def _jitter_pos(
    x: float, y: float, rng: random.Random, variation: float,
    amount: float = 5.0,
) -> tuple[float, float]:
    """XY position noise.  No-op when variation=0."""
    if variation <= 0:
        return x, y
    jit = amount * variation
    return x + rng.uniform(-jit, jit), y + rng.uniform(-jit, jit)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_house(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a simple house from axis-aligned boxes.

    Returns dict with ``objects`` list and ``dummy`` name.
    """
    # Unpack options with defaults
    wt = options.get("wall_thickness", WALL_THICKNESS)
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    dw = options.get("door_width", DOOR_WIDTH)
    dh = options.get("door_height", DOOR_HEIGHT)
    rt = options.get("roof_thickness", ROOF_THICKNESS)
    ov = options.get("roof_overhang", ROOF_OVERHANG)
    prefix = options.get("name_prefix", "House")
    roof_style = options.get("roof_style", "flat")  # "flat" or "gable"

    fnd_extra = options.get("foundation_extra", FOUNDATION_EXTRA)
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    win_w = options.get("window_width", WINDOW_WIDTH)
    win_h = options.get("window_height", WINDOW_HEIGHT)
    win_sill = options.get("window_sill_height", WINDOW_SILL_HEIGHT)

    wall_color = (180, 170, 150)
    floor_color = (120, 100, 80)
    roof_color = (140, 60, 50)
    foundation_color = (100, 90, 75)

    created: list[str] = []

    # ---- Foundation (wider base slab) ----
    fnd_cz = cz - fnd_t / 2.0
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, fnd_cz,
        width + fnd_extra * 2, depth + fnd_extra * 2, fnd_t, foundation_color,
    )
    created.append(n)

    # ---- Floor ----
    floor_cz = cz + ft / 2.0
    n = _create_box(f"{prefix}_Floor", cx, cy, floor_cz, width, depth, ft, floor_color)
    created.append(n)

    # Base Z for walls (top of floor)
    base_z = cz + ft

    # ---- Back wall with window opening (3-section split) ----
    wall_cz = base_z + height / 2.0
    back_y = cy + depth / 2.0
    # Left section
    back_left_w = (width - win_w) / 2.0
    n = _create_box(
        f"{prefix}_Wall_Back_L", cx - win_w / 2.0 - back_left_w / 2.0, back_y, wall_cz,
        back_left_w, wt, height, wall_color,
    )
    created.append(n)
    # Right section
    n = _create_box(
        f"{prefix}_Wall_Back_R", cx + win_w / 2.0 + back_left_w / 2.0, back_y, wall_cz,
        back_left_w, wt, height, wall_color,
    )
    created.append(n)
    # Below window
    if win_sill > 0.1:
        below_cz = base_z + win_sill / 2.0
        n = _create_box(
            f"{prefix}_Wall_Back_Below", cx, back_y, below_cz,
            win_w, wt, win_sill, wall_color,
        )
        created.append(n)
    # Above window
    above_win_h = height - win_sill - win_h
    if above_win_h > 0.1:
        above_win_cz = base_z + win_sill + win_h + above_win_h / 2.0
        n = _create_box(
            f"{prefix}_Wall_Back_Above", cx, back_y, above_win_cz,
            win_w, wt, above_win_h, wall_color,
        )
        created.append(n)

    # ---- Left wall ----
    n = _create_box(f"{prefix}_Wall_Left", cx - width / 2.0, cy, wall_cz, wt, depth, height, wall_color)
    created.append(n)

    # ---- Right wall ----
    n = _create_box(f"{prefix}_Wall_Right", cx + width / 2.0, cy, wall_cz, wt, depth, height, wall_color)
    created.append(n)

    # ---- Front wall with door opening ----
    front_y = cy - depth / 2.0

    # Left section of front wall
    left_w = (width - dw) / 2.0
    left_cx = cx - dw / 2.0 - left_w / 2.0
    n = _create_box(f"{prefix}_Wall_Front_L", left_cx, front_y, wall_cz, left_w, wt, height, wall_color)
    created.append(n)

    # Right section of front wall
    right_cx = cx + dw / 2.0 + left_w / 2.0
    n = _create_box(f"{prefix}_Wall_Front_R", right_cx, front_y, wall_cz, left_w, wt, height, wall_color)
    created.append(n)

    # Above-door section
    above_h = height - dh
    if above_h > 0:
        above_cz = base_z + dh + above_h / 2.0
        n = _create_box(f"{prefix}_Wall_Front_Top", cx, front_y, above_cz, dw, wt, above_h, wall_color)
        created.append(n)

    # ---- Roof ----
    roof_base_z = base_z + height

    if roof_style == "gable":
        # Gable roof: two tilted slabs built from a triangular spline + extrude
        # For reliability, use two boxes angled via Python-computed positions
        # Actually — use an extruded triangle spline for the gable ends,
        # and a flat ridge box for the roof surface.
        #
        # Simpler approach: prism shapes for gable ends + a flat roof slab
        # that extends to the peak.
        #
        # Simplest reliable approach: just raise the roof slab centre and
        # add triangular gable infill walls on left/right.
        #
        # Best: two roof planes as boxes, tilted — but plan says no rotations.
        # So: build stepped approximation or use a single flat slab with
        # gable end walls (triangular prisms).

        gable_peak = options.get("gable_peak", height * 0.4)

        # Flat roof slab at peak (ridge board)
        ridge_z = roof_base_z + gable_peak + rt / 2.0
        n = _create_box(
            f"{prefix}_Roof_Ridge", cx, cy, ridge_z,
            width + ov * 2, rt, rt, roof_color,
        )
        created.append(n)

        # Front and back roof slopes — approximate with a box per side
        # covering from eave to ridge. Height = hypotenuse, but since
        # we can't rotate, we use a wedge / prism for each side.
        # Actually the best no-rotation approach: two flat slabs — the
        # left-slope and right-slope — each spanning half the depth.
        # They are axis-aligned boxes sitting at different Z levels.

        # Left slope (negative-X half of roof)
        slope_w = (width + ov * 2) / 2.0
        slope_d = depth + ov * 2
        slope_cz = roof_base_z + gable_peak / 2.0 + rt / 2.0
        n = _create_box(
            f"{prefix}_Roof_Slope_L",
            cx - slope_w / 2.0, cy, slope_cz,
            slope_w, slope_d, gable_peak + rt, roof_color,
        )
        created.append(n)

        n = _create_box(
            f"{prefix}_Roof_Slope_R",
            cx + slope_w / 2.0, cy, slope_cz,
            slope_w, slope_d, gable_peak + rt, roof_color,
        )
        created.append(n)

    else:
        # Flat roof slab
        roof_cz = roof_base_z + rt / 2.0
        n = _create_box(
            f"{prefix}_Roof", cx, cy, roof_cz,
            width + ov * 2, depth + ov * 2, rt, roof_color,
        )
        created.append(n)

    # ---- Organize under Dummy ----
    total_h = ft + height + (rt if roof_style == "flat" else options.get("gable_peak", height * 0.4) + rt)
    dummy_cz = cz + total_h / 2.0
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, dummy_cz],
        [width + ov * 2, depth + ov * 2, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_wall_with_openings(
    cx: float, cy: float, cz: float,
    width: float, height: float, thickness: float,
    openings: list[dict[str, Any]],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a single wall (aligned to XZ plane) with rectangular openings.

    Each opening is ``{"type": "door"|"window", "offset_x": float}`` where
    *offset_x* is the centre of the opening relative to *cx*.
    Doors sit on the floor; windows use ``WINDOW_SILL_HEIGHT``.

    Returns dict with ``objects`` list.
    """
    prefix = options.get("name_prefix", "Wall")
    color = options.get("color", (180, 170, 150))
    created: list[str] = []

    # Sort openings left to right
    openings = sorted(openings, key=lambda o: o.get("offset_x", 0))

    # Build list of horizontal segments and above-opening fills
    # Start from left edge of wall
    left_edge = cx - width / 2.0
    wall_cz = cz + height / 2.0
    seg_idx = 0

    cursor_x = left_edge  # tracks right edge of last segment

    for opening in openings:
        ow = opening.get("width", DOOR_WIDTH if opening["type"] == "door" else WINDOW_WIDTH)
        oh = opening.get("height", DOOR_HEIGHT if opening["type"] == "door" else WINDOW_HEIGHT)
        ox = cx + opening.get("offset_x", 0)  # centre of opening in world X

        opening_left = ox - ow / 2.0
        opening_right = ox + ow / 2.0

        # Segment to the left of this opening
        seg_w = opening_left - cursor_x
        if seg_w > 0.1:
            seg_cx = cursor_x + seg_w / 2.0
            n = _create_box(
                f"{prefix}_Seg{seg_idx}", seg_cx, cy, wall_cz,
                seg_w, thickness, height, color,
            )
            created.append(n)
            seg_idx += 1

        # Above the opening
        if opening["type"] == "door":
            above_h = height - oh
            if above_h > 0.1:
                above_cz = cz + oh + above_h / 2.0
                n = _create_box(
                    f"{prefix}_Above{seg_idx}", ox, cy, above_cz,
                    ow, thickness, above_h, color,
                )
                created.append(n)
        else:
            # Window: fill below sill + above top
            sill_h = opening.get("sill_height", WINDOW_SILL_HEIGHT)
            if sill_h > 0.1:
                below_cz = cz + sill_h / 2.0
                n = _create_box(
                    f"{prefix}_Below{seg_idx}", ox, cy, below_cz,
                    ow, thickness, sill_h, color,
                )
                created.append(n)
            above_h = height - sill_h - oh
            if above_h > 0.1:
                above_cz = cz + sill_h + oh + above_h / 2.0
                n = _create_box(
                    f"{prefix}_Above{seg_idx}", ox, cy, above_cz,
                    ow, thickness, above_h, color,
                )
                created.append(n)

        cursor_x = opening_right
        seg_idx += 1

    # Rightmost segment
    right_edge = cx + width / 2.0
    seg_w = right_edge - cursor_x
    if seg_w > 0.1:
        seg_cx = cursor_x + seg_w / 2.0
        n = _create_box(
            f"{prefix}_Seg{seg_idx}", seg_cx, cy, wall_cz,
            seg_w, thickness, height, color,
        )
        created.append(n)

    return {"objects": created}


def _build_stairs(
    cx: float, cy: float, cz: float,
    width: float, num_steps: int,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a staircase from stacked boxes (no rotations).

    Stairs go in +Y direction (each step offset in Y).
    """
    step_h = options.get("step_height", STEP_HEIGHT)
    step_d = options.get("step_depth", STEP_DEPTH)
    step_w = options.get("step_width", width if width else STEP_WIDTH)
    prefix = options.get("name_prefix", "Stairs")
    color = options.get("color", (160, 150, 140))

    created: list[str] = []

    for i in range(num_steps):
        step_cz = cz + i * step_h + step_h / 2.0
        step_cy = cy + i * step_d + step_d / 2.0
        n = _create_box(
            f"{prefix}_Step{i}", cx, step_cy, step_cz,
            step_w, step_d, step_h, color,
        )
        created.append(n)

    # Organize under Dummy
    total_h = num_steps * step_h
    total_d = num_steps * step_d
    dummy_cz = cz + total_h / 2.0
    dummy_cy = cy + total_d / 2.0
    dummy_name = _create_dummy(
        prefix,
        [cx, dummy_cy, dummy_cz],
        [step_w, total_d, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_tower(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a tower — tall box-shaped structure with walls on four sides."""
    wt = options.get("wall_thickness", WALL_THICKNESS)
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    rt = options.get("roof_thickness", ROOF_THICKNESS)
    ov = options.get("roof_overhang", ROOF_OVERHANG)
    prefix = options.get("name_prefix", "Tower")
    num_floors = options.get("num_floors", 1)
    floor_height = height / max(num_floors, 1)

    wall_color = (160, 155, 145)
    floor_color = (120, 100, 80)
    roof_color = (100, 100, 110)

    created: list[str] = []

    # Ground floor slab
    floor_cz = cz + ft / 2.0
    n = _create_box(f"{prefix}_Floor0", cx, cy, floor_cz, width, depth, ft, floor_color)
    created.append(n)

    base_z = cz + ft

    # Walls for the full height
    wall_cz = base_z + height / 2.0
    n = _create_box(f"{prefix}_Wall_N", cx, cy + depth / 2.0, wall_cz, width, wt, height, wall_color)
    created.append(n)
    n = _create_box(f"{prefix}_Wall_S", cx, cy - depth / 2.0, wall_cz, width, wt, height, wall_color)
    created.append(n)
    n = _create_box(f"{prefix}_Wall_W", cx - width / 2.0, cy, wall_cz, wt, depth, height, wall_color)
    created.append(n)
    n = _create_box(f"{prefix}_Wall_E", cx + width / 2.0, cy, wall_cz, wt, depth, height, wall_color)
    created.append(n)

    # Intermediate floor slabs
    for fi in range(1, num_floors):
        slab_z = base_z + fi * floor_height + ft / 2.0
        n = _create_box(f"{prefix}_Floor{fi}", cx, cy, slab_z, width, depth, ft, floor_color)
        created.append(n)

    # Roof slab
    roof_z = base_z + height + rt / 2.0
    n = _create_box(f"{prefix}_Roof", cx, cy, roof_z, width + ov * 2, depth + ov * 2, rt, roof_color)
    created.append(n)

    # Organize
    total_h = ft + height + rt
    dummy_name = _create_dummy(prefix, [cx, cy, cz + total_h / 2.0], [width + ov * 2, depth + ov * 2, total_h])
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_fence(
    cx: float, cy: float, cz: float,
    length: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a fence along the X axis centred at (cx, cy, cz)."""
    post_thickness = options.get("post_thickness", 4.0)
    post_spacing = options.get("post_spacing", 40.0)
    rail_height_ratio = options.get("rail_height_ratio", 0.5)
    rail_thickness = options.get("rail_thickness", 3.0)
    prefix = options.get("name_prefix", "Fence")
    post_color = (100, 80, 60)
    rail_color = (120, 95, 70)
    rng, var = _init_variation(options)

    created: list[str] = []
    num_posts = max(int(length / post_spacing) + 1, 2)
    actual_spacing = length / (num_posts - 1)

    # Posts
    for i in range(num_posts):
        px = cx - length / 2.0 + i * actual_spacing
        ph = _jitter_value(height, rng, var, factor=0.08)
        post_cz = cz + ph / 2.0
        n = _create_box(
            f"{prefix}_Post{i}", px, cy, post_cz,
            post_thickness, post_thickness, ph, _jitter_color(post_color, rng, var),
        )
        created.append(n)

    # Rails (top and mid)
    rail_cz_top = cz + height - rail_thickness / 2.0
    rail_cz_mid = cz + height * rail_height_ratio
    n = _create_box(
        f"{prefix}_Rail_Top", cx, cy, rail_cz_top,
        length, rail_thickness, rail_thickness, rail_color,
    )
    created.append(n)
    n = _create_box(
        f"{prefix}_Rail_Mid", cx, cy, rail_cz_mid,
        length, rail_thickness, rail_thickness, rail_color,
    )
    created.append(n)

    # Organize
    dummy_name = _create_dummy(prefix, [cx, cy, cz + height / 2.0], [length, post_thickness, height])
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_skyscraper(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a tapered skyscraper with setback balconies per section.

    Divides the total height into sections that get progressively narrower,
    matching the Unreal MCP tapering pattern.
    """
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    rt = options.get("roof_thickness", ROOF_THICKNESS)
    prefix = options.get("name_prefix", "Skyscraper")
    num_sections = options.get("num_sections", 4)
    taper = options.get("taper_factor", 0.1)  # width shrinks by this per section

    body_color = (160, 165, 175)
    balcony_color = (140, 145, 155)
    roof_color = (100, 100, 110)
    foundation_color = (100, 90, 75)

    created: list[str] = []

    # Foundation
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, cz - fnd_t / 2.0,
        width + 20, depth + 20, fnd_t, foundation_color,
    )
    created.append(n)

    section_h = height / max(num_sections, 1)
    current_z = cz

    for s in range(num_sections):
        scale = max(0.6, 1.0 - s * taper)
        sec_w = width * scale
        sec_d = depth * scale
        sec_cz = current_z + section_h / 2.0

        n = _create_box(
            f"{prefix}_Section{s}", cx, cy, sec_cz,
            sec_w, sec_d, section_h, body_color,
        )
        created.append(n)

        # Setback balcony between sections (except last)
        if s < num_sections - 1:
            balc_cz = current_z + section_h
            n = _create_box(
                f"{prefix}_Balcony{s}", cx, cy, balc_cz,
                sec_w + 10, sec_d + 10, ft, balcony_color,
            )
            created.append(n)

        current_z += section_h

    # Roof slab
    n = _create_box(
        f"{prefix}_Roof", cx, cy, current_z + rt / 2.0,
        width * max(0.6, 1.0 - (num_sections - 1) * taper) + 10,
        depth * max(0.6, 1.0 - (num_sections - 1) * taper) + 10,
        rt, roof_color,
    )
    created.append(n)

    total_h = height + rt + fnd_t
    dummy_name = _create_dummy(prefix, [cx, cy, cz + height / 2.0], [width + 20, depth + 20, total_h])
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_parking_garage(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a multi-level parking garage with floor slabs + pillar grid.

    Matches the Unreal MCP pattern: nested X/Y pillar loop per level.
    """
    num_levels = options.get("num_levels", max(int(height / FLOOR_HEIGHT), 2))
    level_h = height / max(num_levels, 1)
    pillar_t = options.get("pillar_thickness", PILLAR_THICKNESS)
    pillar_cols = options.get("pillar_cols", 3)
    pillar_rows = options.get("pillar_rows", 3)
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    prefix = options.get("name_prefix", "ParkingGarage")

    slab_color = (150, 150, 150)
    pillar_color = (130, 130, 135)
    foundation_color = (100, 90, 75)

    created: list[str] = []

    # Foundation
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, cz - fnd_t / 2.0,
        width + 10, depth + 10, fnd_t, foundation_color,
    )
    created.append(n)

    for lv in range(num_levels):
        level_z = cz + lv * level_h

        # Floor slab
        n = _create_box(
            f"{prefix}_Floor{lv}", cx, cy, level_z + ft / 2.0,
            width, depth, ft, slab_color,
        )
        created.append(n)

        # Pillar grid (evenly spaced within footprint)
        for px in range(pillar_cols):
            for py in range(pillar_rows):
                p_cx = cx + (px - (pillar_cols - 1) / 2.0) * (width / max(pillar_cols, 2))
                p_cy = cy + (py - (pillar_rows - 1) / 2.0) * (depth / max(pillar_rows, 2))
                p_cz = level_z + ft + (level_h - ft) / 2.0
                n = _create_box(
                    f"{prefix}_Pillar{lv}_{px}_{py}", p_cx, p_cy, p_cz,
                    pillar_t, pillar_t, level_h - ft, pillar_color,
                )
                created.append(n)

    # Top slab (roof)
    roof_z = cz + num_levels * level_h
    n = _create_box(
        f"{prefix}_Roof", cx, cy, roof_z + ft / 2.0,
        width, depth, ft, slab_color,
    )
    created.append(n)

    total_h = num_levels * level_h + ft + fnd_t
    dummy_name = _create_dummy(prefix, [cx, cy, cz + total_h / 2.0], [width + 10, depth + 10, total_h])
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_bridge(
    cx: float, cy: float, cz: float,
    span: float, deck_width: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a suspension bridge with parabolic cables.

    ``span`` is the X-axis distance between towers.
    ``height`` is the tower height above the deck.
    """
    import math

    sag_ratio = options.get("sag_ratio", 0.15)
    module_size = options.get("module_size", 20.0)
    prefix = options.get("name_prefix", "Bridge")

    tower_color = (130, 130, 140)
    deck_color = (110, 100, 90)
    cable_color = (80, 80, 85)

    created: list[str] = []
    sag = span * sag_ratio

    # ---- Towers (left and right) ----
    for side, sx in [("L", cx - span / 2.0), ("R", cx + span / 2.0)]:
        # Tower base
        n = _create_box(f"{prefix}_TowerBase_{side}", sx, cy, cz + 10, 20, 20, 20, tower_color)
        created.append(n)
        # Tower shaft
        n = _create_box(f"{prefix}_Tower_{side}", sx, cy, cz + height / 2.0, 12, 12, height, tower_color)
        created.append(n)
        # Tower cap
        n = _create_box(f"{prefix}_TowerCap_{side}", sx, cy, cz + height + 3, 14, 14, 6, tower_color)
        created.append(n)

    # ---- Deck segments ----
    num_deck = max(1, int(span / module_size))
    for i in range(num_deck):
        dx = cx - span / 2.0 + (i + 0.5) * (span / num_deck)
        n = _create_box(
            f"{prefix}_Deck{i}", dx, cy, cz,
            span / num_deck, deck_width, 3, deck_color,
        )
        created.append(n)

    # ---- Parabolic cables (one per side of deck) ----
    cable_offsets = [-deck_width / 2.0, deck_width / 2.0]
    num_cable_seg = max(4, int(span / module_size))
    for ci, c_off in enumerate(cable_offsets):
        for i in range(num_cable_seg):
            x0 = -span / 2.0 + i * (span / num_cable_seg)
            x1 = -span / 2.0 + (i + 1) * (span / num_cable_seg)
            z0 = height + parabolic_z(x0, span, sag)
            z1 = height + parabolic_z(x1, span, sag)
            mid_x = cx + (x0 + x1) / 2.0
            mid_z = cz + (z0 + z1) / 2.0
            seg_h = abs(z1 - z0) + 1.5  # thin box
            n = _create_box(
                f"{prefix}_Cable{ci}_{i}", mid_x, cy + c_off, mid_z,
                span / num_cable_seg, 1.5, seg_h, cable_color,
            )
            created.append(n)

    # ---- Vertical suspenders ----
    suspender_spacing = max(module_size * 2, span / 12)
    num_susp = max(1, int(span / suspender_spacing))
    for i in range(num_susp):
        sx = -span / 2.0 + (i + 0.5) * (span / num_susp)
        cable_z = cz + height + parabolic_z(sx, span, sag)
        deck_z = cz
        susp_h = cable_z - deck_z
        if susp_h < 2:
            continue
        for c_off in cable_offsets:
            n = _create_box(
                f"{prefix}_Susp{i}_{int(c_off)}", cx + sx, cy + c_off,
                deck_z + susp_h / 2.0, 1.0, 1.0, susp_h, cable_color,
            )
            created.append(n)

    # Organize
    total_h = height + 10
    dummy_name = _create_dummy(prefix, [cx, cy, cz + total_h / 2.0], [span + 30, deck_width + 10, total_h])
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_office_tower(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build an office tower: foundation + tall lobby + tower body + window bands + rooftop equipment.

    Options:
        name_prefix, num_floors (int), lobby_height_mult (float, default 1.5),
        window_band_interval (int, floors between bands, default 3).
    """
    prefix = options.get("name_prefix", "OfficeTower")
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    fh = options.get("floor_height", FLOOR_HEIGHT)
    num_floors = options.get("num_floors", max(int(height / fh), 3))
    lobby_mult = options.get("lobby_height_mult", 1.5)
    band_interval = options.get("window_band_interval", 3)

    body_color = (170, 180, 195)
    lobby_color = (150, 160, 175)
    window_color = (100, 140, 180)
    roof_color = (110, 110, 120)
    equip_color = (90, 90, 95)
    foundation_color = (100, 90, 75)

    created: list[str] = []

    # ---- Foundation ----
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, cz - fnd_t / 2.0,
        width + FOUNDATION_EXTRA * 2, depth + FOUNDATION_EXTRA * 2, fnd_t,
        foundation_color,
    )
    created.append(n)

    # ---- Lobby (1.5x floor height) ----
    lobby_h = fh * lobby_mult
    lobby_cz = cz + lobby_h / 2.0
    n = _create_box(
        f"{prefix}_Lobby", cx, cy, lobby_cz,
        width, depth, lobby_h, lobby_color,
    )
    created.append(n)

    # ---- Tower body (remaining height above lobby) ----
    tower_h = height - lobby_h
    if tower_h < fh:
        tower_h = fh  # ensure at least one floor
    tower_cz = cz + lobby_h + tower_h / 2.0
    n = _create_box(
        f"{prefix}_TowerBody", cx, cy, tower_cz,
        width, depth, tower_h, body_color,
    )
    created.append(n)

    # ---- Window bands every N floors ----
    band_h = ft  # thin decorative strip
    tower_floor_h = tower_h / max(num_floors - 1, 1)
    for fi in range(band_interval, num_floors, band_interval):
        band_z = cz + lobby_h + fi * tower_floor_h
        # Front band
        n = _create_box(
            f"{prefix}_WinBand_F{fi}", cx, cy - depth / 2.0, band_z,
            width + 2, 1.5, band_h, window_color,
        )
        created.append(n)
        # Back band
        n = _create_box(
            f"{prefix}_WinBand_B{fi}", cx, cy + depth / 2.0, band_z,
            width + 2, 1.5, band_h, window_color,
        )
        created.append(n)
        # Left band
        n = _create_box(
            f"{prefix}_WinBand_L{fi}", cx - width / 2.0, cy, band_z,
            1.5, depth + 2, band_h, window_color,
        )
        created.append(n)
        # Right band
        n = _create_box(
            f"{prefix}_WinBand_R{fi}", cx + width / 2.0, cy, band_z,
            1.5, depth + 2, band_h, window_color,
        )
        created.append(n)

    # ---- Rooftop slab ----
    roof_top_z = cz + lobby_h + tower_h
    n = _create_box(
        f"{prefix}_Roof", cx, cy, roof_top_z + ft / 2.0,
        width + 10, depth + 10, ft, roof_color,
    )
    created.append(n)

    # ---- Rooftop equipment (two boxes + cylinder) ----
    equip_base_z = roof_top_z + ft
    n = _create_box(
        f"{prefix}_HVAC1", cx - width * 0.2, cy, equip_base_z + 12,
        width * 0.2, depth * 0.25, 24, equip_color,
    )
    created.append(n)
    n = _create_box(
        f"{prefix}_HVAC2", cx + width * 0.2, cy, equip_base_z + 8,
        width * 0.15, depth * 0.2, 16, equip_color,
    )
    created.append(n)
    n = _create_cylinder(
        f"{prefix}_Antenna", cx, cy + depth * 0.15, equip_base_z + 20,
        3, 40, equip_color,
    )
    created.append(n)

    # ---- Organize under Dummy ----
    total_h = fnd_t + lobby_h + tower_h + ft + 40  # include equipment
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + (lobby_h + tower_h) / 2.0],
        [width + FOUNDATION_EXTRA * 2, depth + FOUNDATION_EXTRA * 2, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_apartment_complex(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build an apartment complex: foundation + main building + front/back balconies per floor + rooftop slab.

    Options:
        name_prefix, num_floors (int), balcony_depth (float), balcony_thickness (float).
    """
    prefix = options.get("name_prefix", "ApartmentComplex")
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    fh = options.get("floor_height", FLOOR_HEIGHT)
    num_floors = options.get("num_floors", max(int(height / fh), 2))
    balcony_depth = options.get("balcony_depth", 20.0)
    balcony_t = options.get("balcony_thickness", ft)

    body_color = (185, 175, 160)
    foundation_color = (100, 90, 75)
    balcony_color = (160, 155, 145)
    roof_color = (120, 115, 110)

    created: list[str] = []

    # Floor height derived from total height
    floor_h = height / max(num_floors, 1)

    # ---- Foundation ----
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, cz - fnd_t / 2.0,
        width + FOUNDATION_EXTRA * 2, depth + FOUNDATION_EXTRA * 2, fnd_t,
        foundation_color,
    )
    created.append(n)

    # ---- Main building body ----
    body_cz = cz + height / 2.0
    n = _create_box(
        f"{prefix}_Body", cx, cy, body_cz,
        width, depth, height, body_color,
    )
    created.append(n)

    # ---- Balconies per floor (front and back) ----
    for fi in range(num_floors):
        balc_z = cz + fi * floor_h + balcony_t / 2.0
        # Front balcony (negative Y face)
        front_balc_cy = cy - depth / 2.0 - balcony_depth / 2.0
        n = _create_box(
            f"{prefix}_Balcony_F{fi}", cx, front_balc_cy, balc_z,
            width * 0.8, balcony_depth, balcony_t, balcony_color,
        )
        created.append(n)
        # Back balcony (positive Y face)
        back_balc_cy = cy + depth / 2.0 + balcony_depth / 2.0
        n = _create_box(
            f"{prefix}_Balcony_B{fi}", cx, back_balc_cy, balc_z,
            width * 0.8, balcony_depth, balcony_t, balcony_color,
        )
        created.append(n)

    # ---- Rooftop slab ----
    roof_z = cz + height + ft / 2.0
    n = _create_box(
        f"{prefix}_Roof", cx, cy, roof_z,
        width + 8, depth + 8, ft, roof_color,
    )
    created.append(n)

    # ---- Organize under Dummy ----
    total_h = fnd_t + height + ft
    total_d = depth + balcony_depth * 2
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + height / 2.0],
        [width + FOUNDATION_EXTRA * 2, total_d, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_shopping_mall(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a shopping mall: extra-wide foundation + main body + entrance canopy + 3 pillars + rooftop parking slab.

    Options:
        name_prefix, canopy_depth (float), canopy_height (float),
        pillar_radius (float), parking_slab_height (float).
    """
    prefix = options.get("name_prefix", "ShoppingMall")
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    canopy_depth = options.get("canopy_depth", 30.0)
    canopy_h = options.get("canopy_height", height * 0.7)
    pillar_radius = options.get("pillar_radius", 5.0)
    parking_h = options.get("parking_slab_height", ft * 2)

    body_color = (190, 185, 175)
    foundation_color = (100, 90, 75)
    canopy_color = (160, 160, 170)
    pillar_color = (140, 140, 150)
    roof_color = (130, 130, 135)

    created: list[str] = []

    # ---- Foundation (extra wide) ----
    fnd_extra = FOUNDATION_EXTRA * 2  # wider than usual for mall
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, cz - fnd_t / 2.0,
        width + fnd_extra * 2, depth + fnd_extra * 2, fnd_t,
        foundation_color,
    )
    created.append(n)

    # ---- Main body ----
    body_cz = cz + height / 2.0
    n = _create_box(
        f"{prefix}_Body", cx, cy, body_cz,
        width, depth, height, body_color,
    )
    created.append(n)

    # ---- Entrance canopy (extending from front, negative Y) ----
    canopy_cz = cz + canopy_h + CANOPY_THICKNESS / 2.0
    canopy_cy = cy - depth / 2.0 - canopy_depth / 2.0
    n = _create_box(
        f"{prefix}_Canopy", cx, canopy_cy, canopy_cz,
        width * 0.6, canopy_depth, CANOPY_THICKNESS, canopy_color,
    )
    created.append(n)

    # ---- 3 entrance pillars under canopy ----
    pillar_h = canopy_h
    pillar_cy = canopy_cy
    pillar_cz = cz + pillar_h / 2.0
    pillar_spacing = width * 0.6 / 4.0  # evenly space 3 pillars across canopy width
    for pi in range(3):
        pillar_cx = cx + (pi - 1) * pillar_spacing
        n = _create_cylinder(
            f"{prefix}_Pillar{pi}", pillar_cx, pillar_cy, pillar_cz,
            pillar_radius, pillar_h, pillar_color,
        )
        created.append(n)

    # ---- Rooftop parking slab ----
    parking_z = cz + height + parking_h / 2.0
    n = _create_box(
        f"{prefix}_ParkingRoof", cx, cy, parking_z,
        width * 0.9, depth * 0.9, parking_h, roof_color,
    )
    created.append(n)

    # ---- Organize under Dummy ----
    total_h = fnd_t + height + parking_h
    total_d = depth + canopy_depth
    dummy_name = _create_dummy(
        prefix,
        [cx, cy - canopy_depth / 2.0, cz + height / 2.0],
        [width + fnd_extra * 2, total_d + fnd_extra, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_hotel(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a hotel: grand foundation + extra-tall lobby (2x) + narrower tower + penthouse + rooftop pool + entrance canopy.

    Options:
        name_prefix, num_floors (int), lobby_height_mult (float, default 2.0),
        tower_width_factor (float, default 0.9), pool_depth (float),
        canopy_depth (float).
    """
    prefix = options.get("name_prefix", "Hotel")
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    fh = options.get("floor_height", FLOOR_HEIGHT)
    lobby_mult = options.get("lobby_height_mult", 2.0)
    tower_w_factor = options.get("tower_width_factor", 0.9)
    pool_depth_size = options.get("pool_depth", 15.0)
    canopy_depth = options.get("canopy_depth", 25.0)

    body_color = (195, 190, 180)
    lobby_color = (175, 165, 150)
    tower_color = (180, 185, 195)
    penthouse_color = (200, 195, 185)
    pool_color = (80, 150, 200)
    canopy_color = (160, 160, 170)
    roof_color = (120, 115, 110)
    foundation_color = (110, 100, 85)

    created: list[str] = []

    # ---- Grand foundation (extra thick and wide) ----
    grand_fnd_t = fnd_t * 1.5
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, cz - grand_fnd_t / 2.0,
        width + FOUNDATION_EXTRA * 3, depth + FOUNDATION_EXTRA * 3, grand_fnd_t,
        foundation_color,
    )
    created.append(n)

    # ---- Extra-tall lobby (2x floor height) ----
    lobby_h = fh * lobby_mult
    lobby_cz = cz + lobby_h / 2.0
    n = _create_box(
        f"{prefix}_Lobby", cx, cy, lobby_cz,
        width, depth, lobby_h, lobby_color,
    )
    created.append(n)

    # ---- Tower (0.9x width/depth, fills remaining height minus one floor for penthouse) ----
    penthouse_h = fh
    tower_h = height - lobby_h - penthouse_h
    if tower_h < fh:
        tower_h = fh
    tower_w = width * tower_w_factor
    tower_d = depth * tower_w_factor
    tower_cz = cz + lobby_h + tower_h / 2.0
    n = _create_box(
        f"{prefix}_Tower", cx, cy, tower_cz,
        tower_w, tower_d, tower_h, tower_color,
    )
    created.append(n)

    # ---- Penthouse (full width, top floor) ----
    penthouse_base_z = cz + lobby_h + tower_h
    penthouse_cz = penthouse_base_z + penthouse_h / 2.0
    n = _create_box(
        f"{prefix}_Penthouse", cx, cy, penthouse_cz,
        width, depth, penthouse_h, penthouse_color,
    )
    created.append(n)

    # ---- Rooftop pool slab ----
    roof_top_z = penthouse_base_z + penthouse_h
    pool_w = width * 0.5
    pool_d = depth * 0.3
    n = _create_box(
        f"{prefix}_Pool", cx, cy + depth * 0.15, roof_top_z + pool_depth_size / 2.0,
        pool_w, pool_d, pool_depth_size, pool_color,
    )
    created.append(n)

    # ---- Rooftop slab ----
    n = _create_box(
        f"{prefix}_Roof", cx, cy, roof_top_z + ft / 2.0,
        width + 8, depth + 8, ft, roof_color,
    )
    created.append(n)

    # ---- Entrance canopy (front face, negative Y) ----
    canopy_h = lobby_h * 0.8
    canopy_cy = cy - depth / 2.0 - canopy_depth / 2.0
    canopy_cz = cz + canopy_h + CANOPY_THICKNESS / 2.0
    n = _create_box(
        f"{prefix}_Canopy", cx, canopy_cy, canopy_cz,
        width * 0.5, canopy_depth, CANOPY_THICKNESS, canopy_color,
    )
    created.append(n)

    # ---- Organize under Dummy ----
    total_h = grand_fnd_t + height + ft + pool_depth_size
    total_d = depth + canopy_depth
    dummy_name = _create_dummy(
        prefix,
        [cx, cy - canopy_depth / 2.0, cz + height / 2.0],
        [width + FOUNDATION_EXTRA * 3, total_d, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_restaurant(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a restaurant: foundation + main building (single floor) + outdoor patio slab + awning above patio.

    Options:
        name_prefix, patio_depth (float), patio_width_factor (float),
        awning_height (float).
    """
    prefix = options.get("name_prefix", "Restaurant")
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    fh = options.get("floor_height", FLOOR_HEIGHT)
    patio_depth = options.get("patio_depth", depth * 0.4)
    patio_w_factor = options.get("patio_width_factor", 0.8)
    awning_h = options.get("awning_height", height * 0.75)

    body_color = (195, 180, 155)
    foundation_color = (100, 90, 75)
    patio_color = (165, 145, 120)
    awning_color = (180, 60, 50)

    created: list[str] = []

    # Use single floor height — restaurant is small
    building_h = min(height, fh)

    # ---- Foundation ----
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, cz - fnd_t / 2.0,
        width + FOUNDATION_EXTRA * 2, depth + FOUNDATION_EXTRA * 2, fnd_t,
        foundation_color,
    )
    created.append(n)

    # ---- Main building (single floor) ----
    body_cz = cz + building_h / 2.0
    n = _create_box(
        f"{prefix}_Body", cx, cy, body_cz,
        width, depth, building_h, body_color,
    )
    created.append(n)

    # ---- Outdoor patio slab (extending from front, negative Y) ----
    patio_w = width * patio_w_factor
    patio_cy = cy - depth / 2.0 - patio_depth / 2.0
    patio_cz = cz + ft / 2.0  # slightly raised platform
    n = _create_box(
        f"{prefix}_Patio", cx, patio_cy, patio_cz,
        patio_w, patio_depth, ft, patio_color,
    )
    created.append(n)

    # ---- Awning slab above patio ----
    awning_cz = cz + awning_h + CANOPY_THICKNESS / 2.0
    n = _create_box(
        f"{prefix}_Awning", cx, patio_cy, awning_cz,
        patio_w + 6, patio_depth + 6, CANOPY_THICKNESS, awning_color,
    )
    created.append(n)

    # ---- Organize under Dummy ----
    total_h = fnd_t + building_h
    total_d = depth + patio_depth
    dummy_name = _create_dummy(
        prefix,
        [cx, cy - patio_depth / 2.0, cz + building_h / 2.0],
        [width + FOUNDATION_EXTRA * 2, total_d, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_store(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a store: foundation + main building (single floor) + storefront sign above front wall.

    Options:
        name_prefix, sign_height (float), sign_thickness (float),
        sign_overhang (float).
    """
    prefix = options.get("name_prefix", "Store")
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    fnd_t = options.get("foundation_thickness", FOUNDATION_THICKNESS)
    fh = options.get("floor_height", FLOOR_HEIGHT)
    sign_h = options.get("sign_height", 18.0)
    sign_t = options.get("sign_thickness", 3.0)
    sign_overhang = options.get("sign_overhang", 4.0)

    body_color = (185, 180, 170)
    foundation_color = (100, 90, 75)
    sign_color = (200, 70, 55)

    created: list[str] = []

    # Use single floor height — store is small
    building_h = min(height, fh)

    # ---- Foundation ----
    n = _create_box(
        f"{prefix}_Foundation", cx, cy, cz - fnd_t / 2.0,
        width + FOUNDATION_EXTRA * 2, depth + FOUNDATION_EXTRA * 2, fnd_t,
        foundation_color,
    )
    created.append(n)

    # ---- Main building (single floor) ----
    body_cz = cz + building_h / 2.0
    n = _create_box(
        f"{prefix}_Body", cx, cy, body_cz,
        width, depth, building_h, body_color,
    )
    created.append(n)

    # ---- Storefront sign (thin box above front wall) ----
    sign_cz = cz + building_h + sign_h / 2.0
    sign_cy = cy - depth / 2.0  # flush with front face
    n = _create_box(
        f"{prefix}_Sign", cx, sign_cy, sign_cz,
        width + sign_overhang * 2, sign_t, sign_h, sign_color,
    )
    created.append(n)

    # ---- Organize under Dummy ----
    total_h = fnd_t + building_h + sign_h
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + (building_h + sign_h) / 2.0],
        [width + FOUNDATION_EXTRA * 2, depth + FOUNDATION_EXTRA * 2, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_spiral_tower(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a spiral tower made of individual blocks arranged in a helix.

    Returns dict with ``objects`` list and ``dummy`` name.
    """
    prefix = options.get("name_prefix", "SpiralTower")
    rng, var = _init_variation(options)
    block_size = options.get("block_size", 15.0)
    num_levels = max(int(height / block_size), 5)
    base_radius = min(width, depth) / 2.0
    palette_name = options.get("palette", "rainbow")

    palettes = {
        "rainbow": [(255, 0, 0), (255, 128, 0), (255, 255, 0), (0, 255, 0), (0, 0, 255), (128, 0, 255)],
        "fire": [(255, 0, 0), (255, 77, 0), (255, 153, 0), (255, 204, 0), (255, 255, 51)],
        "ocean": [(0, 51, 102), (0, 102, 153), (0, 153, 204), (0, 204, 255), (51, 255, 255)],
        "sunset": [(255, 102, 153), (255, 153, 102), (255, 204, 51), (255, 230, 153), (230, 179, 230)],
    }
    palette = palettes.get(palette_name, palettes["rainbow"])

    created: list[str] = []

    for level in range(num_levels):
        t = level / max(num_levels - 1, 1)  # 0..1 ratio
        twist_angle = t * 4.0 * math.pi  # 4 full rotations
        current_radius = base_radius * (1.0 - t * 0.4)  # shrinks to 60%
        num_blocks = max(6, int(base_radius * 0.5 * (1.0 - t * 0.5)))

        # Pick color from palette by gradient
        palette_idx = t * (len(palette) - 1)
        ci = min(int(palette_idx), len(palette) - 2)
        frac = palette_idx - ci
        c0 = palette[ci]
        c1 = palette[ci + 1]
        color = (
            int(c0[0] + (c1[0] - c0[0]) * frac),
            int(c0[1] + (c1[1] - c0[1]) * frac),
            int(c0[2] + (c1[2] - c0[2]) * frac),
        )

        for i in range(num_blocks):
            angle = 2.0 * math.pi * i / num_blocks + twist_angle
            bx = cx + math.cos(angle) * current_radius
            by = cy + math.sin(angle) * current_radius
            bz = cz + level * block_size + block_size / 2.0

            bs = _jitter_value(block_size, rng, var, factor=0.1)
            n = _create_box(
                f"{prefix}_L{level}_B{i}", bx, by, bz,
                bs, bs, bs, _jitter_color(color, rng, var, amount=10),
            )
            created.append(n)

    # Spire on top: 3 shrinking cone segments
    spire_base_z = cz + num_levels * block_size
    spire_radius = base_radius * 0.3
    spire_colors = [
        palette[-1],
        palette[len(palette) // 2],
        palette[0],
    ]
    for si in range(3):
        seg_radius = spire_radius * (1.0 - si * 0.3)
        seg_height = block_size * (2.0 - si * 0.5)
        seg_cz = spire_base_z + seg_height / 2.0
        n = _create_cone(
            f"{prefix}_Spire{si}", cx, cy, seg_cz,
            seg_radius, seg_height, spire_colors[si],
        )
        created.append(n)
        spire_base_z += seg_height

    # Organize under Dummy
    total_h = num_levels * block_size + block_size * (2.0 + 1.5 + 1.0)
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + total_h / 2.0],
        [width, depth, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_twisted_tower(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a tower with a square cross-section that twists as it goes up.

    Returns dict with ``objects`` list and ``dummy`` name.
    """
    prefix = options.get("name_prefix", "TwistedTower")
    rng, var = _init_variation(options)
    block_size = options.get("block_size", 15.0)
    num_levels = max(int(height / block_size), 5)
    base_size = min(width, depth) / block_size

    # Gradient colors from bottom (dark stone) to top (light)
    color_bottom = (100, 95, 85)
    color_top = (200, 195, 180)

    created: list[str] = []

    for level in range(num_levels):
        t = level / max(num_levels - 1, 1)
        twist_angle = t * 2.0 * math.pi  # 2 full rotations
        current_size = base_size * max(0.6, 1.0 - t * 0.4)

        # Gradient color
        color = (
            int(color_bottom[0] + (color_top[0] - color_bottom[0]) * t),
            int(color_bottom[1] + (color_top[1] - color_bottom[1]) * t),
            int(color_bottom[2] + (color_top[2] - color_bottom[2]) * t),
        )

        half = current_size / 2.0
        num_per_edge = max(int(current_size), 2)

        # Build blocks along the 4 edges of a square perimeter
        perimeter_positions: list[tuple[float, float]] = []
        for i in range(num_per_edge):
            frac = (i / num_per_edge) - 0.5  # -0.5 .. ~0.5
            # Top edge: y = +half, x varies
            perimeter_positions.append((frac * current_size, half))
            # Bottom edge: y = -half, x varies
            perimeter_positions.append((frac * current_size, -half))
            # Left edge: x = -half, y varies
            perimeter_positions.append((-half, frac * current_size))
            # Right edge: x = +half, y varies
            perimeter_positions.append((half, frac * current_size))

        cos_a = math.cos(twist_angle)
        sin_a = math.sin(twist_angle)
        bz = cz + level * block_size + block_size / 2.0

        for bi, (lx, ly) in enumerate(perimeter_positions):
            rotated_x = lx * cos_a - ly * sin_a
            rotated_y = lx * sin_a + ly * cos_a
            bx = cx + rotated_x * block_size
            by = cy + rotated_y * block_size

            tbs = _jitter_value(block_size, rng, var, factor=0.1)
            n = _create_box(
                f"{prefix}_L{level}_B{bi}", bx, by, bz,
                tbs, tbs, tbs, _jitter_color(color, rng, var, amount=10),
            )
            created.append(n)

    # Organize under Dummy
    total_h = num_levels * block_size
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + total_h / 2.0],
        [width, depth, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_aqueduct(
    cx: float, cy: float, cz: float,
    span: float, deck_width: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a multi-tier Roman aqueduct with arches, piers, deck, and side walls.

    Note: ``width`` = span length, ``depth`` = deck_width, ``height`` = total height.

    Returns dict with ``objects`` list and ``dummy`` name.
    """
    prefix = options.get("name_prefix", "Aqueduct")
    rng, var = _init_variation(options)
    num_arches = options.get("num_arches", 5)
    num_tiers = options.get("num_tiers", 2)
    pier_width = options.get("pier_width", 15.0)

    # Computed values
    arch_radius = (span / num_arches - pier_width) / 2.0
    tier_height = 2.0 * arch_radius + pier_width
    arch_spacing = 2.0 * arch_radius + pier_width

    # Colors
    pier_color = (160, 155, 140)
    arch_color = (150, 145, 135)
    deck_color = (140, 135, 125)
    wall_color = (130, 125, 115)

    created: list[str] = []

    # Start x at left edge of the aqueduct
    start_x = cx - span / 2.0

    for tier in range(num_tiers):
        tier_base_z = cz + tier * tier_height

        # ---- Piers ----
        for pi in range(num_arches + 1):
            pier_x = start_x + pi * arch_spacing
            pier_cz = tier_base_z + tier_height / 2.0
            n = _create_box(
                f"{prefix}_Pier_T{tier}_P{pi}",
                pier_x, cy, pier_cz,
                pier_width, pier_width, tier_height, _jitter_color(pier_color, rng, var),
            )
            created.append(n)

        # ---- Arches (8 segments per arch) ----
        num_arch_segments = 8
        for ai in range(num_arches):
            arch_center_x = start_x + ai * arch_spacing + arch_spacing / 2.0
            arch_base_z = tier_base_z

            for si in range(num_arch_segments):
                angle0 = math.pi * si / num_arch_segments
                angle1 = math.pi * (si + 1) / num_arch_segments

                x0 = arch_x(angle0, arch_radius)
                z0 = arch_z(angle0, arch_radius)
                x1 = arch_x(angle1, arch_radius)
                z1 = arch_z(angle1, arch_radius)

                mid_x = arch_center_x + (x0 + x1) / 2.0
                mid_z = arch_base_z + (z0 + z1) / 2.0
                seg_w = abs(x1 - x0) + pier_width * 0.3
                seg_h = abs(z1 - z0) + pier_width * 0.3

                n = _create_box(
                    f"{prefix}_Arch_T{tier}_A{ai}_S{si}",
                    mid_x, cy, mid_z,
                    seg_w, pier_width, seg_h, _jitter_color(arch_color, rng, var),
                )
                created.append(n)

    # ---- Deck on top tier ----
    deck_z = cz + num_tiers * tier_height + 2.5  # 5.0 thick, centred
    deck_seg_w = span / num_arches
    for di in range(num_arches):
        deck_x = start_x + (di + 0.5) * arch_spacing
        n = _create_box(
            f"{prefix}_Deck_{di}",
            deck_x, cy, deck_z,
            deck_seg_w, deck_width, 5.0, deck_color,
        )
        created.append(n)

    # ---- Side walls along the deck ----
    wall_h = 10.0
    wall_z = cz + num_tiers * tier_height + 5.0 + wall_h / 2.0
    wall_thickness = 3.0
    for side, y_off in [("L", -deck_width / 2.0), ("R", deck_width / 2.0)]:
        n = _create_box(
            f"{prefix}_Wall_{side}",
            cx, cy + y_off, wall_z,
            span, wall_thickness, wall_h, wall_color,
        )
        created.append(n)

    # ---- Organize under Dummy ----
    total_h = num_tiers * tier_height + 5.0 + wall_h
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + total_h / 2.0],
        [span + pier_width, deck_width, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_castle(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a complete medieval castle with walls, towers, keep, and village.

    Parameters:
        cx, cy, cz: Centre of the castle.
        width, depth: Outer bailey dimensions (X and Y axes).
        height: Outer wall height.
        options: Overrides — ``name_prefix``, ``castle_size``.

    Returns dict with ``dummy`` name and ``objects`` list.
    """
    prefix = options.get("name_prefix", "Castle")
    castle_size = options.get("castle_size", "large")
    rng, var = _init_variation(options)

    # -- Derived dimensions --------------------------------------------------
    wt = height * 0.08                    # wall thickness
    tower_r = height * 0.2                # corner tower radius
    tower_h = height * 1.5                # corner tower height
    roof_h = tower_r                      # cone roof height = radius
    inner_w = width * 0.5                 # inner bailey width
    inner_d = depth * 0.5                 # inner bailey depth
    inner_h = height * 1.3                # inner wall height
    inner_tower_h = height * 1.8          # inner tower height
    keep_w = inner_w * 0.4               # keep width
    keep_d = inner_d * 0.4               # keep depth
    keep_h = height * 2.0                # keep height
    gate_gap = width * 0.12              # gate opening width

    # -- Colours -------------------------------------------------------------
    wall_clr = (160, 155, 140)
    tower_clr = (150, 145, 135)
    roof_clr = (120, 50, 40)
    keep_clr = (140, 135, 125)
    court_clr = (170, 160, 145)
    village_clr = (180, 170, 150)
    moat_clr = (40, 80, 120)
    bridge_clr = (100, 70, 40)
    flag_clr = (180, 30, 30)

    created: list[str] = []

    # ========================================================================
    # 1. Outer walls (4 sides, west wall split for gate)
    # ========================================================================
    wall_cz = cz + height / 2.0

    # North wall — full width along +Y edge
    n = _create_box(
        f"{prefix}_Wall_N", cx, cy + depth / 2.0, wall_cz,
        width, wt, height, wall_clr,
    )
    created.append(n)

    # South wall — full width along -Y edge
    n = _create_box(
        f"{prefix}_Wall_S", cx, cy - depth / 2.0, wall_cz,
        width, wt, height, wall_clr,
    )
    created.append(n)

    # East wall — full depth along +X edge
    n = _create_box(
        f"{prefix}_Wall_E", cx + width / 2.0, cy, wall_cz,
        wt, depth, height, wall_clr,
    )
    created.append(n)

    # West wall — split into two segments with gate gap centred on cy
    west_x = cx - width / 2.0
    seg_len = (depth - gate_gap) / 2.0

    # West wall south segment
    seg_s_cy = cy - depth / 2.0 + seg_len / 2.0
    n = _create_box(
        f"{prefix}_Wall_W_S", west_x, seg_s_cy, wall_cz,
        wt, seg_len, height, wall_clr,
    )
    created.append(n)

    # West wall north segment
    seg_n_cy = cy + depth / 2.0 - seg_len / 2.0
    n = _create_box(
        f"{prefix}_Wall_W_N", west_x, seg_n_cy, wall_cz,
        wt, seg_len, height, wall_clr,
    )
    created.append(n)

    # ========================================================================
    # 2. Battlements on outer walls
    # ========================================================================
    batt_h = 15.0   # battlement merlon height
    batt_sp = 20.0   # spacing between merlons (centre-to-centre)
    batt_w = batt_sp * 0.45  # merlon width (slightly less than half spacing)
    batt_top_z = cz + height + batt_h / 2.0

    # --- North wall battlements ---
    n_count = max(1, int(width / batt_sp))
    for i in range(n_count):
        bx = cx - width / 2.0 + batt_sp / 2.0 + i * batt_sp
        if bx > cx + width / 2.0 - batt_w / 2.0:
            break
        nm = _create_box(
            f"{prefix}_Batt_N{i}", bx, cy + depth / 2.0, batt_top_z,
            batt_w, wt, batt_h, _jitter_color(wall_clr, rng, var),
        )
        created.append(nm)

    # --- South wall battlements ---
    for i in range(n_count):
        bx = cx - width / 2.0 + batt_sp / 2.0 + i * batt_sp
        if bx > cx + width / 2.0 - batt_w / 2.0:
            break
        nm = _create_box(
            f"{prefix}_Batt_S{i}", bx, cy - depth / 2.0, batt_top_z,
            batt_w, wt, batt_h, _jitter_color(wall_clr, rng, var),
        )
        created.append(nm)

    # --- East wall battlements ---
    e_count = max(1, int(depth / batt_sp))
    for i in range(e_count):
        by = cy - depth / 2.0 + batt_sp / 2.0 + i * batt_sp
        if by > cy + depth / 2.0 - batt_w / 2.0:
            break
        nm = _create_box(
            f"{prefix}_Batt_E{i}", cx + width / 2.0, by, batt_top_z,
            wt, batt_w, batt_h, _jitter_color(wall_clr, rng, var),
        )
        created.append(nm)

    # --- West wall battlements (skip gate gap area) ---
    gate_min_y = cy - gate_gap / 2.0
    gate_max_y = cy + gate_gap / 2.0
    for i in range(e_count):
        by = cy - depth / 2.0 + batt_sp / 2.0 + i * batt_sp
        if by > cy + depth / 2.0 - batt_w / 2.0:
            break
        # Skip if merlon overlaps the gate gap
        if (by + batt_w / 2.0) > gate_min_y and (by - batt_w / 2.0) < gate_max_y:
            continue
        nm = _create_box(
            f"{prefix}_Batt_W{i}", west_x, by, batt_top_z,
            wt, batt_w, batt_h, _jitter_color(wall_clr, rng, var),
        )
        created.append(nm)

    # ========================================================================
    # 3. Four corner towers (cylinders + cone roofs)
    # ========================================================================
    corners = [
        ("NE", cx + width / 2.0, cy + depth / 2.0),
        ("NW", cx - width / 2.0, cy + depth / 2.0),
        ("SE", cx + width / 2.0, cy - depth / 2.0),
        ("SW", cx - width / 2.0, cy - depth / 2.0),
    ]
    tower_cz = cz + tower_h / 2.0
    roof_base_z = cz + tower_h + roof_h / 2.0

    for tag, tx, ty in corners:
        nm = _create_cylinder(
            f"{prefix}_Tower_{tag}", tx, ty, tower_cz,
            tower_r, tower_h, tower_clr,
        )
        created.append(nm)

        nm = _create_cone(
            f"{prefix}_TowerRoof_{tag}", tx, ty, roof_base_z,
            tower_r, roof_h, roof_clr,
        )
        created.append(nm)

    # ========================================================================
    # 4. Gate complex
    # ========================================================================
    gate_tower_r = tower_r * 0.7
    gate_tower_h = tower_h * 0.9
    gate_tower_cz = cz + gate_tower_h / 2.0

    # Two gate flanking towers
    for tag, gy in [("GateN", cy + gate_gap / 2.0), ("GateS", cy - gate_gap / 2.0)]:
        nm = _create_cylinder(
            f"{prefix}_{tag}", west_x, gy, gate_tower_cz,
            gate_tower_r, gate_tower_h, tower_clr,
        )
        created.append(nm)

        nm = _create_cone(
            f"{prefix}_{tag}_Roof", west_x, gy,
            cz + gate_tower_h + gate_tower_r * 0.5,
            gate_tower_r, gate_tower_r, roof_clr,
        )
        created.append(nm)

    # Portcullis — thin box filling the gate opening
    portcullis_h = height * 0.8
    nm = _create_box(
        f"{prefix}_Portcullis", west_x, cy, cz + portcullis_h / 2.0,
        wt * 0.3, gate_gap, portcullis_h, (80, 80, 90),
    )
    created.append(nm)

    # Barbican — extends outward (-X) from the gate
    barbican_w = gate_gap * 1.5
    barbican_d = gate_gap * 1.2
    barbican_h = height * 0.9
    nm = _create_box(
        f"{prefix}_Barbican",
        west_x - barbican_w / 2.0, cy, cz + barbican_h / 2.0,
        barbican_w, barbican_d, barbican_h, wall_clr,
    )
    created.append(nm)

    # ========================================================================
    # 5. Inner walls (smaller rectangle)
    # ========================================================================
    inner_wall_cz = cz + inner_h / 2.0
    inner_wt = wt * 1.2  # slightly thicker inner walls

    # Inner North
    nm = _create_box(
        f"{prefix}_InnerWall_N", cx, cy + inner_d / 2.0, inner_wall_cz,
        inner_w, inner_wt, inner_h, wall_clr,
    )
    created.append(nm)

    # Inner South
    nm = _create_box(
        f"{prefix}_InnerWall_S", cx, cy - inner_d / 2.0, inner_wall_cz,
        inner_w, inner_wt, inner_h, wall_clr,
    )
    created.append(nm)

    # Inner East
    nm = _create_box(
        f"{prefix}_InnerWall_E", cx + inner_w / 2.0, cy, inner_wall_cz,
        inner_wt, inner_d, inner_h, wall_clr,
    )
    created.append(nm)

    # Inner West
    nm = _create_box(
        f"{prefix}_InnerWall_W", cx - inner_w / 2.0, cy, inner_wall_cz,
        inner_wt, inner_d, inner_h, wall_clr,
    )
    created.append(nm)

    # ========================================================================
    # 6. Four inner corner towers
    # ========================================================================
    inner_tower_r = tower_r * 0.8
    inner_tower_cz = cz + inner_tower_h / 2.0
    inner_corners = [
        ("INE", cx + inner_w / 2.0, cy + inner_d / 2.0),
        ("INW", cx - inner_w / 2.0, cy + inner_d / 2.0),
        ("ISE", cx + inner_w / 2.0, cy - inner_d / 2.0),
        ("ISW", cx - inner_w / 2.0, cy - inner_d / 2.0),
    ]
    for tag, tx, ty in inner_corners:
        nm = _create_cylinder(
            f"{prefix}_InnerTower_{tag}", tx, ty, inner_tower_cz,
            inner_tower_r, inner_tower_h, tower_clr,
        )
        created.append(nm)

        nm = _create_cone(
            f"{prefix}_InnerTowerRoof_{tag}", tx, ty,
            cz + inner_tower_h + inner_tower_r * 0.5,
            inner_tower_r, inner_tower_r, roof_clr,
        )
        created.append(nm)

    # ========================================================================
    # 7. Central keep
    # ========================================================================
    keep_cz = cz + keep_h / 2.0
    nm = _create_box(
        f"{prefix}_Keep", cx, cy, keep_cz,
        keep_w, keep_d, keep_h, keep_clr,
    )
    created.append(nm)

    # Spire on top of keep
    spire_r = min(keep_w, keep_d) * 0.3
    spire_h = keep_h * 0.3
    nm = _create_cylinder(
        f"{prefix}_KeepSpire", cx, cy, cz + keep_h + spire_h / 2.0,
        spire_r, spire_h, tower_clr,
    )
    created.append(nm)

    nm = _create_cone(
        f"{prefix}_KeepSpireRoof", cx, cy,
        cz + keep_h + spire_h + spire_r * 0.5,
        spire_r, spire_r, roof_clr,
    )
    created.append(nm)

    # ========================================================================
    # 8. Courtyard buildings (between inner walls)
    # ========================================================================
    # Place buildings at offsets from the centre, inside the inner bailey
    court_margin = inner_wt + 5.0  # stay clear of inner walls
    avail_w = inner_w - court_margin * 2
    avail_d = inner_d - court_margin * 2
    bldg_w = avail_w * 0.18   # building width
    bldg_d = avail_d * 0.18   # building depth
    bldg_h = height * 0.35    # building height

    # Named buildings with (offset_x_ratio, offset_y_ratio) from centre
    courtyard_buildings = [
        ("Stables",     -0.35,  0.35),
        ("Barracks",     0.35,  0.35),
        ("Blacksmith",  -0.35, -0.35),
        ("Armory",       0.35, -0.35),
        ("Chapel",       0.35,  0.0),
        ("Kitchen",     -0.35,  0.0),
        ("Treasury",     0.0,   0.35),
        ("Granary",      0.0,  -0.35),
        ("GuardHouse",  -0.15,  0.15),
    ]

    for bldg_name, ox_r, oy_r in courtyard_buildings:
        bx = cx + ox_r * avail_w
        by = cy + oy_r * avail_d
        bx, by = _jitter_pos(bx, by, rng, var, amount=bldg_w * 0.3)
        bw = _jitter_value(bldg_w, rng, var)
        bd = _jitter_value(bldg_d, rng, var)
        bh = _jitter_value(bldg_h, rng, var)
        nm = _create_box(
            f"{prefix}_{bldg_name}", bx, by, cz + bh / 2.0,
            bw, bd, bh, _jitter_color(court_clr, rng, var),
        )
        created.append(nm)

    # Well — cylinder instead of box
    well_r = bldg_w * 0.3
    well_h = bldg_h * 0.4
    nm = _create_cylinder(
        f"{prefix}_Well",
        cx + 0.15 * avail_w, cy - 0.15 * avail_d, cz + well_h / 2.0,
        well_r, well_h, (100, 100, 105),
    )
    created.append(nm)

    # ========================================================================
    # 9. Village settlement (two rings of houses around the castle)
    # ========================================================================
    outer_radius = max(width, depth) / 2.0 + tower_r + 30.0
    house_w = width * 0.04
    house_d = depth * 0.04
    house_h = height * 0.25
    house_roof_h = house_h * 0.5

    # Inner ring: 16 houses at 65% of outer_radius
    ring1_r = outer_radius * 0.65
    ring1_count = 16 if castle_size == "large" else 8
    for i in range(ring1_count):
        angle = 2.0 * math.pi * i / ring1_count
        hx, hy = circular_position(cx, cy, ring1_r, angle)
        hx, hy = _jitter_pos(hx, hy, rng, var, amount=house_w * 1.5)
        hw = _jitter_value(house_w, rng, var, factor=0.25)
        hd = _jitter_value(house_d, rng, var, factor=0.25)
        hh = _jitter_value(house_h, rng, var, factor=0.25)
        hrh = _jitter_value(house_roof_h, rng, var, factor=0.2)
        nm = _create_box(
            f"{prefix}_Village1_House{i}", hx, hy, cz + hh / 2.0,
            hw, hd, hh, _jitter_color(village_clr, rng, var),
        )
        created.append(nm)
        # Cone roof on each house
        nm = _create_cone(
            f"{prefix}_Village1_Roof{i}", hx, hy,
            cz + hh + hrh / 2.0,
            max(hw, hd) * 0.7, hrh, _jitter_color(roof_clr, rng, var),
        )
        created.append(nm)

    # Outer ring: 8 houses at 80% of outer_radius
    ring2_r = outer_radius * 0.8
    ring2_count = 8 if castle_size == "large" else 4
    for i in range(ring2_count):
        angle = (2.0 * math.pi * i / ring2_count
                 + math.pi / ring2_count)  # offset from inner ring
        hx, hy = circular_position(cx, cy, ring2_r, angle)
        hx, hy = _jitter_pos(hx, hy, rng, var, amount=house_w * 1.5)
        hw = _jitter_value(house_w, rng, var, factor=0.25)
        hd = _jitter_value(house_d, rng, var, factor=0.25)
        hh = _jitter_value(house_h, rng, var, factor=0.25)
        hrh = _jitter_value(house_roof_h, rng, var, factor=0.2)
        nm = _create_box(
            f"{prefix}_Village2_House{i}", hx, hy, cz + hh / 2.0,
            hw, hd, hh, _jitter_color(village_clr, rng, var),
        )
        created.append(nm)
        nm = _create_cone(
            f"{prefix}_Village2_Roof{i}", hx, hy,
            cz + hh + hrh / 2.0,
            max(hw, hd) * 0.7, hrh, _jitter_color(roof_clr, rng, var),
        )
        created.append(nm)

    # ========================================================================
    # 10. Moat — ring of flat cylinder segments around the outer walls
    # ========================================================================
    moat_inner_r = max(width, depth) / 2.0 + tower_r + 5.0
    moat_outer_r = moat_inner_r + MOAT_WIDTH
    moat_mid_r = (moat_inner_r + moat_outer_r) / 2.0
    moat_ring_w = moat_outer_r - moat_inner_r
    moat_h = 3.0   # very flat
    moat_segments = 24

    for i in range(moat_segments):
        angle = 2.0 * math.pi * i / moat_segments
        mx, my = circular_position(cx, cy, moat_mid_r, angle)
        nm = _create_cylinder(
            f"{prefix}_Moat{i}", mx, my, cz - moat_h / 2.0,
            moat_ring_w * 0.55, moat_h, _jitter_color(moat_clr, rng, var, amount=10),
        )
        created.append(nm)

    # ========================================================================
    # 11. Drawbridge — flat box extending outward from the gate
    # ========================================================================
    drawbridge_len = moat_ring_w + 20.0
    drawbridge_w = gate_gap * 0.9
    drawbridge_h = 3.0
    nm = _create_box(
        f"{prefix}_Drawbridge",
        west_x - barbican_w - drawbridge_len / 2.0, cy,
        cz + drawbridge_h / 2.0,
        drawbridge_len, drawbridge_w, drawbridge_h, bridge_clr,
    )
    created.append(nm)

    # ========================================================================
    # 12. Flags on corner towers
    # ========================================================================
    pole_r = tower_r * 0.04
    pole_h = height * 0.4
    flag_w = pole_h * 0.6
    flag_h_dim = pole_h * 0.3

    for tag, tx, ty in corners:
        pole_base_z = cz + tower_h + roof_h
        # Flag pole — thin cylinder
        nm = _create_cylinder(
            f"{prefix}_FlagPole_{tag}", tx, ty,
            pole_base_z + pole_h / 2.0,
            pole_r, pole_h, (60, 60, 60),
        )
        created.append(nm)
        # Flag — thin box attached near top of pole
        nm = _create_box(
            f"{prefix}_Flag_{tag}",
            tx + flag_w / 2.0, ty,
            pole_base_z + pole_h - flag_h_dim / 2.0,
            flag_w, pole_r * 3, flag_h_dim, flag_clr,
        )
        created.append(nm)

    # ========================================================================
    # Organize everything under a Dummy
    # ========================================================================
    total_extent = max(width, depth) + moat_ring_w * 2 + 40.0
    tallest = keep_h + spire_h + spire_r  # tallest point
    dummy_cz = cz + tallest / 2.0

    dummy_name = _create_dummy(
        prefix,
        [cx, cy, dummy_cz],
        [total_extent, total_extent, tallest],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_mansion(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a luxury mansion with wings, staircase, rooftop bar, and exterior.

    Options:
        name_prefix, num_floors (int), wing_width_factor (float),
        wing_depth_factor (float), has_rooftop_bar (bool),
        has_gardens (bool), has_garage (bool), has_fountains (bool).
    """
    prefix = options.get("name_prefix", "Mansion")
    rng, var = _init_variation(options)
    ft = options.get("floor_thickness", FLOOR_THICKNESS)
    fh = options.get("floor_height", FLOOR_HEIGHT)
    num_floors = options.get("num_floors", max(int(height / fh), 2))
    wing_w_factor = options.get("wing_width_factor", 0.4)
    wing_d_factor = options.get("wing_depth_factor", 0.6)
    has_rooftop = options.get("has_rooftop_bar", True)
    has_gardens = options.get("has_gardens", True)
    has_garage = options.get("has_garage", True)
    has_fountains = options.get("has_fountains", True)

    body_color = (210, 200, 185)
    wall_color = (195, 185, 170)
    floor_color = (160, 140, 115)
    roof_color = (130, 120, 110)
    window_color = (120, 160, 200)
    wing_color = (200, 190, 175)
    stair_color = (180, 170, 155)
    railing_color = (100, 95, 90)
    bar_color = (140, 130, 120)
    garden_color = (60, 140, 50)
    fountain_color = (70, 130, 180)
    garage_color = (170, 165, 155)
    gate_color = (80, 75, 70)
    driveway_color = (130, 125, 120)

    created: list[str] = []
    floor_h = height / max(num_floors, 1)
    hw = width / 2.0
    hd = depth / 2.0
    wt = WALL_THICKNESS

    # ---- Main body: floor slabs + perimeter walls per floor ----
    for fi in range(num_floors):
        fz = cz + fi * floor_h

        # Floor slab
        n = _create_box(f"{prefix}_Floor{fi}", cx, cy, fz + ft / 2.0,
                        width, depth, ft, floor_color)
        created.append(n)

        wall_base = fz + ft
        wh = floor_h - ft
        wcz = wall_base + wh / 2.0

        # Perimeter walls (4 sides)
        n = _create_box(f"{prefix}_WN{fi}", cx, cy + hd, wcz, width, wt, wh, wall_color)
        created.append(n)
        n = _create_box(f"{prefix}_WS{fi}", cx, cy - hd, wcz, width, wt, wh, wall_color)
        created.append(n)
        n = _create_box(f"{prefix}_WW{fi}", cx - hw, cy, wcz, wt, depth, wh, wall_color)
        created.append(n)
        n = _create_box(f"{prefix}_WE{fi}", cx + hw, cy, wcz, wt, depth, wh, wall_color)
        created.append(n)

        # Windows on front and back (3 per side per floor)
        for wi in range(3):
            wx = cx + (wi - 1) * (width * 0.3)
            win_cz = wall_base + WINDOW_SILL_HEIGHT + WINDOW_HEIGHT / 2.0
            n = _create_box(f"{prefix}_WinF{fi}_{wi}", wx, cy - hd - 0.5, win_cz,
                            WINDOW_WIDTH * 0.8, 1.0, WINDOW_HEIGHT, window_color)
            created.append(n)
            n = _create_box(f"{prefix}_WinB{fi}_{wi}", wx, cy + hd + 0.5, win_cz,
                            WINDOW_WIDTH * 0.8, 1.0, WINDOW_HEIGHT, window_color)
            created.append(n)

    # ---- Wings (left and right) ----
    wing_w = width * wing_w_factor
    wing_d = depth * wing_d_factor
    wing_h = height * 0.8
    wing_cz = cz + wing_h / 2.0

    # Left wing
    n = _create_box(f"{prefix}_WingL", cx - hw - wing_w / 2.0, cy, wing_cz,
                    wing_w, wing_d, wing_h, wing_color)
    created.append(n)
    n = _create_box(f"{prefix}_WingL_Roof", cx - hw - wing_w / 2.0, cy,
                    cz + wing_h + ROOF_THICKNESS / 2.0,
                    wing_w + ROOF_OVERHANG * 2, wing_d + ROOF_OVERHANG * 2,
                    ROOF_THICKNESS, roof_color)
    created.append(n)

    # Right wing
    n = _create_box(f"{prefix}_WingR", cx + hw + wing_w / 2.0, cy, wing_cz,
                    wing_w, wing_d, wing_h, wing_color)
    created.append(n)
    n = _create_box(f"{prefix}_WingR_Roof", cx + hw + wing_w / 2.0, cy,
                    cz + wing_h + ROOF_THICKNESS / 2.0,
                    wing_w + ROOF_OVERHANG * 2, wing_d + ROOF_OVERHANG * 2,
                    ROOF_THICKNESS, roof_color)
    created.append(n)

    # ---- Main roof ----
    n = _create_box(f"{prefix}_Roof", cx, cy, cz + height + ROOF_THICKNESS / 2.0,
                    width + ROOF_OVERHANG * 2, depth + ROOF_OVERHANG * 2,
                    ROOF_THICKNESS, roof_color)
    created.append(n)

    # ---- Grand staircase (front entrance) ----
    num_steps = 8
    step_w = width * 0.3
    for si in range(num_steps):
        step_z = cz + si * STEP_HEIGHT + STEP_HEIGHT / 2.0
        step_y = cy - hd - (num_steps - si) * STEP_DEPTH - STEP_DEPTH / 2.0
        n = _create_box(f"{prefix}_Step{si}", cx, step_y, step_z,
                        step_w - si * 2, STEP_DEPTH, STEP_HEIGHT, stair_color)
        created.append(n)

    # ---- Rooftop bar / deck ----
    if has_rooftop:
        deck_z = cz + height + ROOF_THICKNESS
        deck_w = width * 0.6
        deck_d = depth * 0.4

        n = _create_box(f"{prefix}_Deck", cx, cy, deck_z + ft / 2.0,
                        deck_w, deck_d, ft, bar_color)
        created.append(n)

        # 8 stilts
        stilt_h = ROOF_THICKNESS + ft
        for sti in range(8):
            angle = 2.0 * math.pi * sti / 8
            sx, sy = circular_position(cx, cy, min(deck_w, deck_d) * 0.4, angle)
            n = _create_cylinder(f"{prefix}_Stilt{sti}", sx, sy,
                                 deck_z - stilt_h / 2.0 + ft, 2.0, stilt_h, railing_color)
            created.append(n)

        # Railing (4 sides)
        rail_z = deck_z + ft + 45.0
        rail_t = 2.0
        n = _create_box(f"{prefix}_RailN", cx, cy + deck_d / 2.0, rail_z,
                        deck_w, rail_t, 90.0, railing_color)
        created.append(n)
        n = _create_box(f"{prefix}_RailS", cx, cy - deck_d / 2.0, rail_z,
                        deck_w, rail_t, 90.0, railing_color)
        created.append(n)
        n = _create_box(f"{prefix}_RailW", cx - deck_w / 2.0, cy, rail_z,
                        rail_t, deck_d, 90.0, railing_color)
        created.append(n)
        n = _create_box(f"{prefix}_RailE", cx + deck_w / 2.0, cy, rail_z,
                        rail_t, deck_d, 90.0, railing_color)
        created.append(n)

        # Bar counter
        n = _create_box(f"{prefix}_BarCounter", cx, cy + deck_d * 0.3,
                        deck_z + ft + 50.0, deck_w * 0.4, 8.0, 50.0, bar_color)
        created.append(n)

    # ---- Driveway ----
    driveway_len = depth * 0.8
    n = _create_box(f"{prefix}_Driveway", cx,
                    cy - hd - num_steps * STEP_DEPTH - driveway_len / 2.0,
                    cz + 1.0, width * 0.2, driveway_len, 2.0, driveway_color)
    created.append(n)

    # ---- Front gates ----
    gate_y = cy - hd - num_steps * STEP_DEPTH - driveway_len
    gate_h = 40.0
    for side, gx in [("L", cx - width * 0.12), ("R", cx + width * 0.12)]:
        n = _create_box(f"{prefix}_Gate_{side}", gx, gate_y, cz + gate_h / 2.0,
                        4.0, 4.0, gate_h, gate_color)
        created.append(n)
    n = _create_box(f"{prefix}_GateBar", cx, gate_y, cz + gate_h,
                    width * 0.24, 3.0, 3.0, gate_color)
    created.append(n)

    # ---- Gardens (circular hedge + flower beds) ----
    garden_r = min(width, depth) * 0.6
    if has_gardens:
        garden_cy = cy + hd + garden_r + 20.0
        for gi in range(24):
            angle = 2.0 * math.pi * gi / 24
            gx, gy = circular_position(cx, garden_cy, garden_r * 0.8, angle)
            gx, gy = _jitter_pos(gx, gy, rng, var, amount=3.0)
            gh = _jitter_value(20.0, rng, var, factor=0.2)
            n = _create_box(f"{prefix}_Hedge{gi}", gx, gy, cz + gh / 2.0,
                            _jitter_value(8.0, rng, var), _jitter_value(8.0, rng, var),
                            gh, _jitter_color(garden_color, rng, var))
            created.append(n)
        for fbi, (fx, fy) in enumerate([
            (cx, garden_cy + garden_r * 0.4),
            (cx, garden_cy - garden_r * 0.4),
            (cx - garden_r * 0.4, garden_cy),
            (cx + garden_r * 0.4, garden_cy),
        ]):
            n = _create_box(f"{prefix}_FlowerBed{fbi}", fx, fy, cz + 3.0,
                            _jitter_value(20.0, rng, var), _jitter_value(20.0, rng, var),
                            6.0, _jitter_color((180, 50, 80), rng, var, amount=20))
            created.append(n)

    # ---- Fountains ----
    if has_fountains:
        ftn_y = cy - hd - num_steps * STEP_DEPTH - driveway_len * 0.5
        n = _create_cylinder(f"{prefix}_FtnBase", cx, ftn_y, cz + 5.0,
                             20.0, 10.0, fountain_color)
        created.append(n)
        n = _create_cylinder(f"{prefix}_FtnBasin", cx, ftn_y, cz + 15.0,
                             12.0, 10.0, fountain_color)
        created.append(n)
        n = _create_cylinder(f"{prefix}_FtnSpout", cx, ftn_y, cz + 30.0,
                             3.0, 20.0, (200, 195, 185))
        created.append(n)

    # ---- Garage ----
    if has_garage:
        garage_x = cx + hw + wing_w + 30.0
        gar_w = 60.0
        gar_d = 40.0
        gar_h = 35.0
        n = _create_box(f"{prefix}_Garage", garage_x, cy, cz + gar_h / 2.0,
                        gar_w, gar_d, gar_h, garage_color)
        created.append(n)
        n = _create_box(f"{prefix}_GarageRoof", garage_x, cy,
                        cz + gar_h + ROOF_THICKNESS / 2.0,
                        gar_w + 8, gar_d + 8, ROOF_THICKNESS, roof_color)
        created.append(n)
        for di in range(3):
            door_x = garage_x + (di - 1) * (gar_w * 0.3)
            n = _create_box(f"{prefix}_GarageDoor{di}", door_x,
                            cy - gar_d / 2.0 - 0.5, cz + 15.0,
                            16.0, 1.0, 30.0, gate_color)
            created.append(n)

    # ---- Organize under Dummy ----
    total_w = width + wing_w * 2 + (100.0 if has_garage else 0.0)
    total_d = depth + num_steps * STEP_DEPTH + driveway_len + (garden_r * 2 + 40.0 if has_gardens else 0.0)
    total_h = height + ROOF_THICKNESS + (100.0 if has_rooftop else 0.0)
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + height / 2.0],
        [total_w, total_d, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_street_grid(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a street grid with horizontal + vertical streets, lights, and sidewalks.

    Options:
        name_prefix, num_streets_x (int), num_streets_y (int),
        street_width (float), sidewalk_width (float), light_height (float).
    """
    prefix = options.get("name_prefix", "StreetGrid")
    rng, var = _init_variation(options)
    num_sx = options.get("num_streets_x", 3)
    num_sy = options.get("num_streets_y", 3)
    street_w = options.get("street_width", 30.0)
    sidewalk_w = options.get("sidewalk_width", 8.0)
    light_h = options.get("light_height", 35.0)

    street_color = (80, 80, 85)
    sidewalk_color = (160, 155, 150)
    light_pole_color = (90, 90, 95)
    light_bulb_color = (255, 240, 200)
    crosswalk_color = (220, 220, 215)

    created: list[str] = []

    # ---- Horizontal streets (along X axis) ----
    for si in range(num_sy):
        sy = cy + (si - (num_sy - 1) / 2.0) * (depth / max(num_sy, 1))
        n = _create_box(f"{prefix}_StreetH{si}", cx, sy, cz + 1.0,
                        width, street_w, 2.0, street_color)
        created.append(n)
        n = _create_box(f"{prefix}_SWalkHN{si}", cx,
                        sy + street_w / 2.0 + sidewalk_w / 2.0,
                        cz + 2.0, width, sidewalk_w, 4.0, sidewalk_color)
        created.append(n)
        n = _create_box(f"{prefix}_SWalkHS{si}", cx,
                        sy - street_w / 2.0 - sidewalk_w / 2.0,
                        cz + 2.0, width, sidewalk_w, 4.0, sidewalk_color)
        created.append(n)

    # ---- Vertical streets (along Y axis) ----
    for si in range(num_sx):
        sx = cx + (si - (num_sx - 1) / 2.0) * (width / max(num_sx, 1))
        n = _create_box(f"{prefix}_StreetV{si}", sx, cy, cz + 1.0,
                        street_w, depth, 2.0, street_color)
        created.append(n)
        n = _create_box(f"{prefix}_SWalkVW{si}",
                        sx - street_w / 2.0 - sidewalk_w / 2.0, cy,
                        cz + 2.0, sidewalk_w, depth, 4.0, sidewalk_color)
        created.append(n)
        n = _create_box(f"{prefix}_SWalkVE{si}",
                        sx + street_w / 2.0 + sidewalk_w / 2.0, cy,
                        cz + 2.0, sidewalk_w, depth, 4.0, sidewalk_color)
        created.append(n)

    # ---- Street lights at intersection corners ----
    for xi in range(num_sx):
        for yi in range(num_sy):
            ix = cx + (xi - (num_sx - 1) / 2.0) * (width / max(num_sx, 1))
            iy = cy + (yi - (num_sy - 1) / 2.0) * (depth / max(num_sy, 1))
            for ci, (dx, dy) in enumerate([
                (street_w / 2.0 + 3, street_w / 2.0 + 3),
                (-street_w / 2.0 - 3, street_w / 2.0 + 3),
                (street_w / 2.0 + 3, -street_w / 2.0 - 3),
                (-street_w / 2.0 - 3, -street_w / 2.0 - 3),
            ]):
                lh = _jitter_value(light_h, rng, var, factor=0.06)
                n = _create_cylinder(f"{prefix}_LPole_{xi}_{yi}_{ci}",
                                     ix + dx, iy + dy, cz + lh / 2.0,
                                     1.5, lh, _jitter_color(light_pole_color, rng, var))
                created.append(n)
                n = _create_sphere(f"{prefix}_LBulb_{xi}_{yi}_{ci}",
                                   ix + dx, iy + dy, cz + lh + 2.0,
                                   3.0, _jitter_color(light_bulb_color, rng, var, amount=8))
                created.append(n)

    # ---- Crosswalks at intersections (5 stripes per crossing) ----
    for xi in range(num_sx):
        for yi in range(num_sy):
            ix = cx + (xi - (num_sx - 1) / 2.0) * (width / max(num_sx, 1))
            iy = cy + (yi - (num_sy - 1) / 2.0) * (depth / max(num_sy, 1))
            for si in range(5):
                stripe_y = iy - street_w * 0.4 + si * (street_w * 0.8 / 5)
                n = _create_box(f"{prefix}_CW_{xi}_{yi}_{si}",
                                ix, stripe_y, cz + 2.1,
                                2.0, street_w / 6, 0.2, crosswalk_color)
                created.append(n)

    # ---- Organize under Dummy ----
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + light_h / 2.0],
        [width, depth, light_h + 5],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


def _build_central_plaza(
    cx: float, cy: float, cz: float,
    width: float, depth: float, height: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build a town central plaza with fountain, monument, benches, and lights.

    Options:
        name_prefix, fountain_tiers (int), monument_height (float),
        num_benches (int), num_lights (int).
    """
    prefix = options.get("name_prefix", "Plaza")
    rng, var = _init_variation(options)
    fountain_tiers = options.get("fountain_tiers", 3)
    monument_h = options.get("monument_height", height * 0.8)
    num_benches = options.get("num_benches", 8)
    num_lights = options.get("num_lights", 12)

    plaza_color = (170, 165, 155)
    fountain_color = (80, 140, 190)
    monument_color = (190, 185, 175)
    bench_color = (120, 90, 60)
    light_pole_color = (90, 90, 95)
    light_bulb_color = (255, 240, 200)

    created: list[str] = []
    radius = min(width, depth) / 2.0

    # ---- Plaza floor ----
    n = _create_box(f"{prefix}_Floor", cx, cy, cz + 2.0,
                    width, depth, 4.0, plaza_color)
    created.append(n)

    # ---- Multi-tier fountain (centre) ----
    ftn_z = cz + 4.0
    for ti in range(fountain_tiers):
        tier_r = (radius * 0.25) * (1.0 - ti * 0.25)
        tier_h = 10.0 + ti * 5.0
        tier_cz = ftn_z + tier_h / 2.0
        n = _create_cylinder(f"{prefix}_Fountain_T{ti}", cx, cy, tier_cz,
                             tier_r, tier_h, fountain_color)
        created.append(n)
        ftn_z += tier_h

    # Water spout on top
    n = _create_cylinder(f"{prefix}_Fountain_Spout", cx, cy, ftn_z + 5.0,
                         2.0, 10.0, (100, 180, 230))
    created.append(n)

    # ---- Monument (offset from centre) ----
    mon_x = cx + radius * 0.5
    n = _create_box(f"{prefix}_MonBase", mon_x, cy, cz + 4.0 + 8.0,
                    20.0, 20.0, 16.0, monument_color)
    created.append(n)
    n = _create_box(f"{prefix}_MonPillar", mon_x, cy,
                    cz + 4.0 + 16.0 + monument_h / 2.0,
                    10.0, 10.0, monument_h, monument_color)
    created.append(n)
    n = _create_sphere(f"{prefix}_MonTop", mon_x, cy,
                       cz + 4.0 + 16.0 + monument_h + 5.0,
                       6.0, monument_color)
    created.append(n)

    # ---- Benches in a circle ----
    bench_r = radius * 0.6
    for bi in range(num_benches):
        angle = 2.0 * math.pi * bi / num_benches
        bx, by = circular_position(cx, cy, bench_r, angle)
        bx, by = _jitter_pos(bx, by, rng, var, amount=3.0)
        bclr = _jitter_color(bench_color, rng, var)
        n = _create_box(f"{prefix}_Bench{bi}", bx, by, cz + 4.0 + 12.5,
                        20.0, 8.0, 5.0, bclr)
        created.append(n)
        for li, ldx in enumerate([-7.0, 7.0]):
            n = _create_box(f"{prefix}_BLeg{bi}_{li}", bx + ldx, by, cz + 4.0 + 5.0,
                            2.0, 6.0, 10.0, bclr)
            created.append(n)

    # ---- Lights in a circle ----
    light_r = radius * 0.85
    light_h = 35.0
    for li in range(num_lights):
        angle = 2.0 * math.pi * li / num_lights
        lx, ly = circular_position(cx, cy, light_r, angle)
        n = _create_cylinder(f"{prefix}_LPole{li}", lx, ly, cz + light_h / 2.0,
                             1.5, light_h, _jitter_color(light_pole_color, rng, var))
        created.append(n)
        n = _create_sphere(f"{prefix}_LBulb{li}", lx, ly, cz + light_h + 2.0,
                           3.0, _jitter_color(light_bulb_color, rng, var, amount=8))
        created.append(n)

    # ---- Organize under Dummy ----
    total_h = max(light_h + 5, monument_h + 25)
    dummy_name = _create_dummy(
        prefix,
        [cx, cy, cz + total_h / 2.0],
        [width, depth, total_h],
    )
    _parent_objects(created, dummy_name)

    return {"dummy": dummy_name, "objects": created}


# ---------------------------------------------------------------------------
# Public MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def build_structure(
    type: str,
    location: list[float] = [0, 0, 0],
    width: float = 200.0,
    depth: float = 150.0,
    height: float = 120.0,
    options: dict[str, Any] | None = None,
) -> str:
    """Build a complete structure using grid-based math — no guesswork.

    Every component is positioned with exact Python-side math from the centre
    point.  No rotations for rectangular geometry.

    Args:
        type: Structure type — "house", "tower", "stairs", "fence",
              "wall_with_openings", "skyscraper", "parking_garage", "bridge",
              "office_tower", "apartment_complex", "shopping_mall", "hotel",
              "restaurant", "store", "spiral_tower", "twisted_tower",
              "aqueduct", "castle", "mansion", "street_grid", "central_plaza".
        location: Centre point [x, y, z].  Z=0 means ground level.
        width: X-axis dimension (cm).
        depth: Y-axis dimension (cm).
        height: Wall / structure height (cm) — does not include roof.
        options: Type-specific overrides.  Common keys:
            name_prefix (str), wall_thickness, floor_thickness,
            door_width, door_height, roof_style ("flat"|"gable"),
            roof_overhang, roof_thickness,
            variation (float 0-1, default 0 = deterministic; >0 adds
              randomised color/size/position jitter to decorative elements),
            seed (int, optional — makes randomisation reproducible).
          For "stairs": num_steps (int), step_height, step_depth.
          For "fence": post_spacing, post_thickness, rail_thickness.
          For "wall_with_openings": thickness (float),
            openings (list of {type, offset_x, width?, height?, sill_height?}).
          For "skyscraper": num_sections (int), taper_factor (float).
          For "parking_garage": num_levels (int), pillar_cols, pillar_rows.
          For "bridge": sag_ratio (float), module_size (float).
            *width* = span length, *depth* = deck width.
          For "spiral_tower": block_size (float), palette (str:
            "rainbow"|"fire"|"ocean"|"sunset").
          For "twisted_tower": block_size (float).
          For "aqueduct": num_arches (int), num_tiers (int), pier_width (float).
            *width* = span length, *depth* = deck width.
          For "castle": castle_size ("large"|"small").
          For "mansion": num_floors (int), wing_width_factor (float),
            has_rooftop_bar (bool), has_gardens (bool), has_garage (bool),
            has_fountains (bool).
          For "street_grid": num_streets_x (int), num_streets_y (int),
            street_width (float), sidewalk_width (float), light_height (float).
          For "central_plaza": fountain_tiers (int), monument_height (float),
            num_benches (int), num_lights (int).

    Returns:
        JSON with created objects and organiser dummy name.
    """
    opts: dict[str, Any] = options or {}
    cx, cy, cz = location if len(location) >= 3 else (0.0, 0.0, 0.0)

    builders = {
        "house": lambda: _build_house(cx, cy, cz, width, depth, height, opts),
        "tower": lambda: _build_tower(cx, cy, cz, width, depth, height, opts),
        "stairs": lambda: _build_stairs(
            cx, cy, cz, width,
            opts.get("num_steps", max(int(height / STEP_HEIGHT), 1)),
            opts,
        ),
        "fence": lambda: _build_fence(cx, cy, cz, width, height, opts),
        "wall_with_openings": lambda: _build_wall_with_openings(
            cx, cy, cz, width, height,
            opts.get("thickness", WALL_THICKNESS),
            opts.get("openings", []),
            opts,
        ),
        "skyscraper": lambda: _build_skyscraper(cx, cy, cz, width, depth, height, opts),
        "parking_garage": lambda: _build_parking_garage(cx, cy, cz, width, depth, height, opts),
        "bridge": lambda: _build_bridge(cx, cy, cz, width, depth, height, opts),
        "office_tower": lambda: _build_office_tower(cx, cy, cz, width, depth, height, opts),
        "apartment_complex": lambda: _build_apartment_complex(cx, cy, cz, width, depth, height, opts),
        "shopping_mall": lambda: _build_shopping_mall(cx, cy, cz, width, depth, height, opts),
        "hotel": lambda: _build_hotel(cx, cy, cz, width, depth, height, opts),
        "restaurant": lambda: _build_restaurant(cx, cy, cz, width, depth, height, opts),
        "store": lambda: _build_store(cx, cy, cz, width, depth, height, opts),
        "spiral_tower": lambda: _build_spiral_tower(cx, cy, cz, width, depth, height, opts),
        "twisted_tower": lambda: _build_twisted_tower(cx, cy, cz, width, depth, height, opts),
        "aqueduct": lambda: _build_aqueduct(cx, cy, cz, width, depth, height, opts),
        "castle": lambda: _build_castle(cx, cy, cz, width, depth, height, opts),
        "mansion": lambda: _build_mansion(cx, cy, cz, width, depth, height, opts),
        "street_grid": lambda: _build_street_grid(cx, cy, cz, width, depth, height, opts),
        "central_plaza": lambda: _build_central_plaza(cx, cy, cz, width, depth, height, opts),
    }

    builder = builders.get(type)
    if builder is None:
        available = ", ".join(sorted(builders.keys()))
        return json.dumps({"error": f"Unknown structure type '{type}'. Available: {available}"})

    result = builder()
    return json.dumps(result)
