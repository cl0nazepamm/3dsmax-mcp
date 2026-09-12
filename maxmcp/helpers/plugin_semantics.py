"""Explicit provider annotations; SDK ranges are never treated as enum lists."""
VRAY_LIGHT = (1012233633, 1607860959)
VRAY_SHAPES = {"rectangle": 0, "environment": 1, "sphere": 2, "disk": 4}
VRAY_UNITS = {"renderer": 0, "lm": 1, "cd/m2": 2}
OCTANE_LIGHT = (592523983, 1640440069)
OCTANE_SHAPES = {"rectangle": 0, "disk": 1, "sphere": 3, "cylinder": 4}
VRAY_IMAGE = (1734939723, 46203261)
VRAY_IMAGE_MAPPING = {"angular": 0, "cubic": 1, "spherical": 2, "mirrored_ball": 3, "max_standard": 4}

# Corona Renderer (Chaos). Class IDs read from live PB2 descriptors on Corona 15 / 3ds Max 2027.
# Enum meanings come from Chaos's own coronaConverter.ms shipped with Corona
# (<Corona>\Scripts\coronaConverter.ms): units at line 3354, colorMode at 3379,
# shape at 3410-3546, CoronaBitmap enviroMapping at 45-46, CoronaSun colorMode at 3744-3746.
# Shape geometry (width = X extent or radius, height = Y extent or cylinder length) was
# confirmed from live bounding boxes of a CoronaLight per shape value.
CORONA_RENDERER = (1655201228, 1379677700)
CORONA_LIGHT = (1110459877, 692869241)
CORONA_SUN = (2084191360, 164583279)
CORONA_MOON = (1510691909, 443094578)   # colour filter only, no Kelvin/realistic modes
CORONA_SKY = (1498904930, 1286306497)
CORONA_BITMAP = (2881116036, 1699234372)
CORONA_SHAPES = {"sphere": 0, "rectangle": 1, "disk": 2, "cylinder": 3}
CORONA_UNITS = {"renderer": 0, "lm": 1, "cd": 2}          # 3 = lux, not offered by LightOutput
CORONA_COLOR_MODES = {"rendering_rgb": 0, "kelvin": 1, "texmap": 2}
CORONA_SUN_COLOR_MODES = {"rendering_rgb": 0, "kelvin": 1, "realistic": 2}
CORONA_BITMAP_MAPPING = {"spherical": 0, "screen": 1}
_CORONA_EVIDENCE = "Chaos coronaConverter.ms shipped with Corona 15 (units L3354, colorMode L3379, shape L3410-3546)"
_CORONA_LIGHT_DOMAINS = {
    (0, 121, "shape"): CORONA_SHAPES,
    (0, 132, "colorMode"): CORONA_COLOR_MODES,
    (0, 136, "intensityUnits"): {**CORONA_UNITS, "lx": 3},
}
_CORONA_SUN_DOMAINS = {(0, 107, "colorMode"): CORONA_SUN_COLOR_MODES}
_CORONA_BITMAP_DOMAINS = {(0, 102, "enviroMapping"): CORONA_BITMAP_MAPPING}
_VRAY_IMAGE_DOMAINS = {
    (0, 2, "mapType"): VRAY_IMAGE_MAPPING,
    (0, 25, "color_space"): {"none": 0, "inverse_gamma": 1, "srgb": 2, "from_max": 3, "auto": 4},
    (3, 41, "rgbColorSpace"): {"default": 0, "srgb": 1, "acescg": 2, "raw": 3},
}

# Independent source: Chaos's shipped scripts/VRay-VRayLightLister.mcr.
# Its Type dropdown uses (type + 1): Plane, Dome, Sphere, Mesh, Disc.
# Its Units dropdown similarly uses normalizeColor + 1.
_VRAY_DOMAINS = {
    (0, 1, "type"): {"rectangle": 0, "dome": 1, "sphere": 2, "mesh": 3, "disk": 4},
    (0, 12, "normalizeColor"): {"renderer": 0, "lm": 1, "cd/m2": 2, "W": 3, "W/m2/sr": 4},
    (0, 39, "color_mode"): {"rendering_rgb": 0, "kelvin": 1},
}


def annotate(data: dict) -> dict:
    identity = data.get("identity", {})
    superclass = identity.get("superclass_id")
    ids=tuple(identity.get("class_id", []))
    if superclass==48 and ids==VRAY_LIGHT:
        domains,evidence=_VRAY_DOMAINS,"Chaos VRay-VRayLightLister.mcr"
    elif superclass==48 and ids==OCTANE_LIGHT:
        domains,evidence={(0,14768,"analyticLightType"):OCTANE_SHAPES},"Octane 2026.3 Type combo item data (Quad=0, Disc=1, Sphere=3, Tube=4)"
    elif superclass==3088 and ids==VRAY_IMAGE:
        domains,evidence=_VRAY_IMAGE_DOMAINS,"V-Ray 7 update 3 VRayBitmap Qt combo item data (mapping, transfer function and RGB primaries)"
    elif superclass==48 and ids==CORONA_LIGHT:
        domains,evidence=_CORONA_LIGHT_DOMAINS,_CORONA_EVIDENCE
    elif superclass==48 and ids==CORONA_SUN:
        domains,evidence=_CORONA_SUN_DOMAINS,"Chaos coronaConverter.ms shipped with Corona 15 (L3744-3746: colorMode 0 = direct colour; PB2 default 2 = realistic)"
    elif superclass==3088 and ids==CORONA_BITMAP:
        domains,evidence=_CORONA_BITMAP_DOMAINS,"Chaos coronaConverter.ms shipped with Corona 15 (L45-46: CORONA_BITMAP_MAPPING_SPHERICAL=0, SCREEN=1)"
    else:
        return data
    for property in data.get("properties", []):
        ref = property.get("property_ref", {})
        choices = domains.get((ref.get("block_id"), ref.get("param_id"), property.get("name")))
        if choices and property.get("type") == "int":
            property["domain"] = {"kind": "enum", "choices": [{"name": k, "value": v} for k,v in choices.items()],
                "complete": True, "source": "provider_reference", "evidence": evidence}
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
