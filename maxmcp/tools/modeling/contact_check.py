"""Read-only contact, gap and interpenetration check between mesh nodes."""
from __future__ import annotations

from typing import Any

from ._contact_check import DEFAULT_TIME_BUDGET_S, build_script, parse_report, validate_args
from ...server import client, mcp


@mcp.tool()
def contact_check(
    names: list[str] | None = None,
    against: list[str] | None = None,
    tolerance: float = 0.0,
    near_gap: float = 0.0,
    include_separate: bool = False,
    limit: int = 50,
    max_pairs: int = 200,
    max_faces: int = 200000,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
) -> dict[str, Any]:
    """Find meshes that pass through each other, touch, or float just short of contact.

    Scope: `names` checks those nodes pairwise. `names` + `against` checks each
    name only against the `against` nodes (e.g. all legs against the floor and
    seat). With neither, the current selection is used, or all visible geometry
    when nothing is selected. Only pairs whose world bounding boxes come within
    near_gap are measured.

    Named nodes that are not meshes (group heads, dummies, shapes, lights),
    empty meshes and meshes over max_faces are returned in `skipped` with a
    reason and a warning; missing or ambiguous names still fail. At least two
    meshes must remain, with one in `names` and one in `against` when `against`
    is used.

    time_budget_s (1-300) bounds the run. Pairs are measured fewest faces
    first; when time runs out the result has complete=false, pairs_measured
    below candidate_pairs and a warning. Unmeasured pairs are unchecked, not clear.

    Status per pair, most severe first:
      penetrating  - a vertex (or, failing that, a face center) lies inside the other
                     closed mesh deeper than tolerance
      intersecting - surfaces cross with no vertex inside (thin parts, open meshes)
      touching     - closest vertex within tolerance of the other surface
      near_gap     - clearance above tolerance but within near_gap (floating parts)
      separate     - hidden unless include_separate=true

    depth is a lower bound (deepest inside vertex to surface); gap is an upper
    bound (vertex to surface). Both in scene units, world space, evaluated
    meshes with modifiers. tolerance defaults to 0.1 mm and near_gap to 10 mm,
    converted to scene units. Points are world positions to aim a camera or
    inspect_mesh at. An open mesh has no inside, so a part piercing it reports
    intersecting; its own vertices can still sit inside a closed part and report
    penetrating. Read only; the scene is not changed.
    """
    names_l, against_l, tol, near, max_pairs, max_faces, budget = validate_args(
        names, against, tolerance, near_gap, max_pairs, max_faces, time_budget_s)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer from 1 to 500")
    # The budget is checked between steps, so allow for one slow mesh evaluation past it.
    response = client.send_command(build_script(names_l, against_l, tol, near, max_pairs, max_faces, budget),
                                   timeout=max(client.timeout, budget + 60.0))
    report = parse_report(str(response.get("result", "")), include_separate=include_separate, limit=limit)
    report["notes"] = [
        "depth is sampled at vertices: a coarse mesh can hide a deeper overlap between its vertices.",
        "gap is vertex to surface; edge-to-edge clearance between two coarse meshes can be smaller.",
        "Parent-child and grouped pairs are checked like any other; a leg seated into a seat by design reports penetrating.",
    ]
    return report
