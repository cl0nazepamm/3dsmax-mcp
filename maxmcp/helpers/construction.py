"""Shared constants and math helpers for grid-based construction."""

# ---------------------------------------------------------------------------
# Architectural constants (centimetres)
# ---------------------------------------------------------------------------
WALL_THICKNESS = 8.0
FLOOR_THICKNESS = 5.0
DOOR_WIDTH = 50.0
DOOR_HEIGHT = 100.0
WINDOW_WIDTH = 50.0
WINDOW_HEIGHT = 45.0
WINDOW_SILL_HEIGHT = 70.0  # bottom of window from floor
ROOF_OVERHANG = 15.0
ROOF_THICKNESS = 5.0
STEP_HEIGHT = 18.0
STEP_DEPTH = 28.0
STEP_WIDTH = 100.0
RAILING_HEIGHT = 90.0
RAILING_THICKNESS = 4.0
FOUNDATION_EXTRA = 10.0  # how much wider than footprint on each side
FOUNDATION_THICKNESS = 8.0
FLOOR_HEIGHT = 120.0  # standard storey height for multi-floor buildings
PILLAR_THICKNESS = 6.0
CABLE_THICKNESS = 2.0
CANOPY_THICKNESS = 3.0
BATTLEMENT_HEIGHT = 15.0
BATTLEMENT_SPACING = 20.0
TOWER_RADIUS = 30.0
MOAT_WIDTH = 60.0
HEDGE_RADIUS_RATIO = 0.8  # garden hedge circle as ratio of garden_size
DOOR_OPENING_WIDTH = 90.0  # default door gap width for floor plans
LABEL_SIZE = 20.0           # default text label size for floor plan rooms

# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def grid_position(origin: float, index: float, cell_size: float) -> float:
    """Return world coordinate for a grid cell.

    ``origin + index * cell_size``
    *index* can be fractional for sub-grid precision.
    """
    return origin + index * cell_size


def center_offset(dimension: float) -> float:
    """Half of *dimension* — shorthand used everywhere."""
    return dimension / 2.0


def stack_z(base_z: float, layer: int, layer_height: float) -> float:
    """Return Z for stacked layers (e.g. floor → wall → roof)."""
    return base_z + layer * layer_height


def parabolic_z(x: float, span: float, sag: float) -> float:
    """Return Z offset along a parabolic curve (for bridge cables).

    At x=0 (centre of span): returns ``-sag`` (lowest point).
    At x=±span/2 (towers): returns 0.
    """
    a = 4.0 * sag / (span * span)
    return a * x * x - sag


def circular_position(
    cx: float, cy: float, radius: float, angle_rad: float,
) -> tuple[float, float]:
    """Return (x, y) on a circle centred at (cx, cy)."""
    import math
    return (cx + radius * math.cos(angle_rad), cy + radius * math.sin(angle_rad))


def arch_z(angle_rad: float, radius: float) -> float:
    """Return Z offset for a semicircular arch at the given angle (0..pi)."""
    import math
    return radius * math.sin(angle_rad)


def arch_x(angle_rad: float, radius: float) -> float:
    """Return X offset for a semicircular arch at the given angle (0..pi)."""
    import math
    return radius * math.cos(angle_rad)
