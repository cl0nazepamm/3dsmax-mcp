"""FStorm-specific light controls and bindings. No scene conversion or renderer switch."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

# Native descriptors and emitter bounds, FStorm 2.0.0Z / Max 2027.2.
FSTORM_RENDERER = (33030992, 1363098752)
FSTORM_LIGHT = (605978406, 1370574548)
FSTORM_SUN = (807800799, 784941243)
SHAPES = {"rectangle": 0, "disk": 1, "sphere": 2}


class SolarPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    hour: float | None = Field(default=None, ge=0, le=24)
    month: float | None = Field(default=None, ge=1, le=12)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    north_direction: float | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("Supply at least one solar position setting.")
        return self


class FStormControls(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    visible: StrictBool | None = None
    gi_visible: StrictBool | None = None
    double_sided: StrictBool | None = None
    affect_diffuse: StrictBool | None = None
    affect_glossy: StrictBool | None = None
    solar: SolarPosition | None = None
    sun_model: Literal["physical", "legacy"] | None = None
    sun_size: float | None = Field(default=None, gt=0)


COMMON_FIELDS = ["enabled", "power", "visible", "affect_diffuse", "affect_glossy", "targeted"]
AREA_FIELDS = COMMON_FIELDS + ["shape", "size_x", "size_y", "color_type", "color", "temperature",
                               "texture", "cast_shadows", "gi_visible", "double_sided", "ies", "ies_enabled",
                               "directional", "directional_angle"]
SUN_FIELDS = COMMON_FIELDS + ["size", "model", "sun_color", "hour", "month", "latitude", "north_direction"]


def apply_controls(controls: FStormControls, *, sun: bool, assign, targeted=False):
    values = controls.model_dump(exclude_none=True)
    if sun and ("gi_visible" in values or "double_sided" in values):
        raise ValueError("FStorm sun has no gi_visible or double_sided control.")
    if not sun and any(k in values for k in ("solar", "sun_model", "sun_size")):
        raise ValueError("Solar position, sun_model and sun_size apply only to FStorm suns.")
    if "solar" in values and targeted:
        raise ValueError("Solar settings require an untargeted FStorm sun; target ownership is preserved.")
    for key, value in values.items():
        if key == "solar":
            for prop, v in value.items():
                assign(prop, v)
        elif key == "sun_model":
            assign("model", {"legacy": 0, "physical": 1}[value])
        elif key == "sun_size":
            assign("size", value)
        else:
            assign(key, value)


def set_color(color, *, sun, assign, model=None):
    if sun:
        if color.kelvin is not None:
            raise ValueError("FStorm sun has no Kelvin mode; use physical sun_model or a legacy RGB color.")
        if model == "physical":
            raise ValueError("Direct sun RGB requires sun_model=legacy.")
        assign("sun_color", list(color.rgb))
    else:
        if color.kelvin is not None and color.kelvin > 24000:
            raise ValueError("FStorm light temperature is limited to 24000 K.")
        assign("color_type", 1 if color.kelvin is not None else 0)
        assign("temperature" if color.kelvin is not None else "color",
               color.kelvin if color.kelvin is not None else list(color.rgb))


def set_size(shape, dims, assign):
    if shape not in SHAPES:
        raise ValueError("FStorm area lights support rectangle, disk and sphere.")
    if shape == "rectangle":
        assign("size_x", dims["width"] / 2)
        assign("size_y", dims["height"] / 2)
    else:
        assign("size_x", dims["radius"])


def validate_output(output, *, sun):
    if output.unit != "renderer":
        raise ValueError("FStorm output uses native renderer power, not a photometric conversion.")
    if output.value <= 0:
        raise ValueError("FStorm power must be positive; use enabled=false to turn a light off.")
    if sun and not .001 <= output.value <= 100000:
        raise ValueError("FStorm sun power must be between 0.001 and 100000.")
