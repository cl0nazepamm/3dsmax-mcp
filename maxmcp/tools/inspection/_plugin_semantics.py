"""Named values of integer plugin parameters. SDK ranges are never treated as enum lists.

The meanings live in the renderer presets (maxmcp/assets/renderers), each with the
evidence it came from; this module only attaches them to an inspected schema.
"""
from ..rendering import _presets as presets


def annotate(data: dict) -> dict:
    identity = data.get("identity", {})
    superclass = identity.get("superclass_id")
    class_id = identity.get("class_id") or []
    if superclass is None or len(class_id) != 2:
        return data
    domains = presets.enum_domains(superclass, class_id)
    if not domains:
        return data
    for property in data.get("properties", []):
        if property.get("type") != "int":
            continue
        ref = property.get("property_ref", {})
        for entry in domains:
            if entry["param"] != property.get("name"):
                continue
            # A preset may pin the exact parameter block and ID as well as the name.
            if "block" in entry and (entry["block"], entry.get("id")) != (ref.get("block_id"), ref.get("param_id")):
                continue
            property["domain"] = {
                "kind": "enum", "choices": [{"name": name, "value": value} for name, value in entry["values"].items()],
                "complete": entry.get("complete", True), "source": "provider_reference", "evidence": entry["evidence"]}
    return data


def resolve_enum(property: dict, value):
    if not isinstance(value, dict) or set(value) != {"enum"}:
        return value
    domain = property.get("domain", {})
    choices = domain.get("choices") or []
    found = [x["value"] for x in choices if x["name"] == value["enum"]]
    if domain.get("kind") != "enum" or len(found) != 1:
        raise ValueError("UNKNOWN_ENUM: choose an exact named value published by the inspector; numeric ranges do not identify meanings.")
    return found[0]
