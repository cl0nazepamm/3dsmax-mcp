"""Guarded typed plugin edits using inspector-issued references."""
from typing import Any
from ...server import mcp
from ._plugin_schema import native
from . import _plugin_schema as plugin_schema
from ._plugin_semantics import resolve_enum


@mcp.tool()
def plugin_patch(edits: list[dict[str, Any]]) -> dict:
    """Atomically edit inspected PB2 properties with native typed readback.

    Each edit: owner_ref, property_ref, expected_schema (schema_token),
    expected_state (state_token), value. Get these with
    inspect_plugin_instance(schema_version=2, fields=[...]). References are
    explicit owner_ref objects; arrays preserve their existing count. Values
    use the inspector's native units. Optional sharing="all_instances" explicitly
    permits changing a shared owner. Animated parameters are refused. No raw
    MAXScript, implicit cloning or controller replacement. Named enum values use
    value={"enum":"name"} from the inspector's published/provider choices.
    """
    if not edits or len(edits) > 512:
        raise ValueError("Supply 1..512 property edits.")
    compiled = []
    for edit in edits:
        value = edit.get("value")
        if isinstance(value, dict) and "enum" in value:
            schema = plugin_schema.inspect(owner_ref=edit["owner_ref"], fields=[edit["property_ref"]])
            if schema["schema_token"] != edit["expected_schema"] or len(schema["properties"]) != 1:
                raise plugin_schema.PluginGuardError("STALE_SCHEMA", "inspect the property again.")
            value = resolve_enum(schema["properties"][0], value)
        compiled.append({**edit, "value": value})
    return native("native:plugin_patch", {"version": 1, "edits": compiled})
