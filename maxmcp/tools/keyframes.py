"""Native keyframing tools with compact intent-shaped JSON envelopes."""

from typing import Optional
import json as _json

from ..server import mcp, client
from ..coerce import FloatList, StrList


@mcp.tool()
def keyframe_tracks(
    action: str = "set",
    target: str = "selection",
    names: Optional[StrList] = None,
    tracks: str = "all",
    track_paths: Optional[StrList] = None,
    time: Optional[float] = None,
    times: Optional[FloatList] = None,
    value: Optional[FloatList] = None,
    move: Optional[FloatList] = None,
    time_unit: str = "frames",
    match: str = "first_to_last",
    from_time: Optional[float] = None,
    to_time: Optional[float] = None,
    key_type: Optional[str] = None,
    in_type: Optional[str] = None,
    out_type: Optional[str] = None,
    tcb: Optional[dict] = None,
    out_of_range: Optional[dict] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    ort_enabled: Optional[bool] = None,
    order: str = "flat",
    budget: Optional[dict] = None,
    max_tracks: int = 1000,
    max_keys: int = 50000,
    max_results: int = 50,
    include_samples: bool = False,
) -> str:
    """Native keyframing: list inspects tracks read-only; set keys at time/times; value or move writes keyed poses without transform_object; match copies between times; loop closes f1->f100 on parented rigs (order=hierarchy); style sets tangents; ort sets out-of-range."""
    if not client.native_available:
        return "Native bridge is required for keyframe_tracks."

    payload: dict = {
        "action": action,
        "target": target,
        "time_unit": time_unit,
        "match": match,
        "order": order,
        "budget": {
            "max_tracks": max_tracks,
            "max_keys": max_keys,
            "max_results": max_results,
            "include_samples": include_samples,
        },
    }
    if names:
        payload["names"] = list(names)
    if track_paths:
        payload["track_paths"] = list(track_paths)
    if not track_paths or tracks != "all":
        payload["tracks"] = tracks
    if time is not None:
        payload["time"] = time
    if times:
        payload["times"] = list(times)
    if value:
        payload["value"] = list(value)
    if move:
        payload["move"] = list(move)
    if from_time is not None:
        payload["from_time"] = from_time
    if to_time is not None:
        payload["to_time"] = to_time
    if key_type:
        payload["key_type"] = key_type
    if in_type:
        payload["in_type"] = in_type
    if out_type:
        payload["out_type"] = out_type
    if tcb:
        payload["tcb"] = tcb
    if out_of_range:
        payload["out_of_range"] = out_of_range
    if before:
        payload["before"] = before
    if after:
        payload["after"] = after
    if ort_enabled is not None:
        payload["ort_enabled"] = ort_enabled
    if budget:
        payload["budget"].update(budget)

    response = client.send_command(
        _json.dumps(payload),
        cmd_type="native:keyframe_tracks",
        timeout=30.0,
    )
    return response.get("result", "")
