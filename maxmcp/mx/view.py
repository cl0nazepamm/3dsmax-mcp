"""mx view helpers: captures, the AGENT VIEWPORT and set_viewport."""

from __future__ import annotations

from typing import Any, Sequence

from ._node import Node
from ._runtime import call, node_name

__all__ = [
    "capture",
    "capture_viewport",
    "open_viewport",
    "frame",
    "orbit",
    "zoom",
    "pan",
    "viewport_capture",
    "pick",
    "viewport_status",
    "release_viewport",
    "look",
]


def _plain(value: Any) -> Any:
    # Tuples (nested too) become lists; a bare string or number passes through for the tool to coerce.
    return [_plain(v) for v in value] if isinstance(value, (list, tuple)) else value


def _names(targets: Node | str | Sequence[Node | str] | None) -> list[str] | None:
    if targets is None:
        return None
    if isinstance(targets, str) or hasattr(targets, "handle"):
        targets = [targets]
    return [node_name(target) for target in targets]


def capture(
    views: Sequence[str | Sequence[float]] | None = None,
    shading: str | None = None,
    edges: bool | None = None,
    frame_root: Node | str = "",
    max_width: int | None = None,
    max_height: int | None = None,
    source: str | None = None,
    capture_method: str | None = None,
    frames: Sequence[int] | None = None,
    frame_count: int | None = None,
    wait_seconds: int | None = None,
    job: str | None = None,
) -> Any:
    """capture_multi_view: stitched multi-angle (or frame strip) capture; result["file"] is the image path.

    views: names (front, top, three_quarter, current, ...) or [x, y, z] eye directions.
    """
    return call(
        "capture_multi_view",
        views=_plain(views),
        shading=shading,
        edges=edges,
        frame_root=node_name(frame_root) if frame_root else None,
        max_width=max_width,
        max_height=max_height,
        source=source,
        capture_method=capture_method,
        frames=_plain(frames),
        frame_count=frame_count,
        wait_seconds=wait_seconds,
        job=job,
    )


def capture_viewport(
    max_width: int | None = None,
    max_height: int | None = None,
    source: str | None = None,
    capture_method: str | None = None,
) -> Any:
    """capture_viewport: one view (AGENT VIEWPORT when open) saved to result["file"]."""
    return call(
        "capture_viewport",
        max_width=max_width,
        max_height=max_height,
        source=source,
        capture_method=capture_method,
    )


def open_viewport(start_minimized: bool = False, width: int | None = None, height: int | None = None) -> Any:
    """agent_viewport open: reserve the floating AGENT VIEWPORT and return its status."""
    return call("agent_viewport", action="open", start_minimized=start_minimized, width=width, height=height)


def frame(*targets: Node | str, padding: float | None = None) -> Any:
    """agent_viewport frame: fit these nodes (and descendants), or visible geometry when none are given."""
    names = [node_name(target) for target in targets] or None
    return call("agent_viewport", action="frame", frame_names=names, padding=padding)


def orbit(yaw: float = 0, pitch: float = 0) -> Any:
    """agent_viewport orbit: rotate yaw/pitch degrees about the framing target."""
    return call("agent_viewport", action="orbit", yaw=yaw, pitch=pitch)


def zoom(factor: float) -> Any:
    """agent_viewport zoom: factor < 1 moves closer, > 1 farther."""
    return call("agent_viewport", action="zoom", factor=factor)


def pan(x: float, y: float) -> Any:
    """agent_viewport pan: shift the view by x/y view-plane scene units."""
    return call("agent_viewport", action="pan", x=x, y=y)


def viewport_capture(crop: Sequence[int] | None = None, capture_method: str | None = None) -> Any:
    """agent_viewport capture: the agent panel or its visible VFB; result["capture"]["file"] is the image.

    crop: [x, y, width, height] in VFB client pixels (VFB previews only).
    """
    return call(
        "agent_viewport",
        action="capture",
        crop=_plain(crop),
        capture_method=capture_method,
    )


def pick(x: float, y: float, expected_view: str) -> Any:
    """agent_viewport pick: surface hit at normalized image x/y (0..1, top-left); result["hit"] or None.

    expected_view: the view_token of the capture the coordinates came from.
    """
    return call("agent_viewport", action="pick", x=x, y=y, expected_view=expected_view)


def viewport_status() -> Any:
    """agent_viewport status: ownership, capture readiness, camera and render state."""
    return call("agent_viewport", action="status")


def release_viewport() -> Any:
    """agent_viewport release: hide the owned AGENT VIEWPORT panel."""
    return call("agent_viewport", action="release")


def look(
    eye: Sequence[float] | None = None,
    target: Sequence[float] | None = None,
    view: str | None = None,
    frame_names: Node | str | Sequence[Node | str] | None = None,
    fov: float | None = None,
    padding: float | None = None,
    shading: str | None = None,
    edges: bool | None = None,
    grid: bool | None = None,
    source: str | None = None,
) -> Any:
    """set_viewport: place the view at world `eye` looking at `target`, or set a named view (agent view when open).

    eye and target go together and need view perspective or orthographic; frame_names fits those nodes.
    """
    return call(
        "set_viewport",
        view=view,
        eye=_plain(eye),
        target=_plain(target),
        frame_names=_names(frame_names),
        fov=fov,
        padding=padding,
        shading=shading,
        edges=edges,
        grid=grid,
        source=source,
    )
