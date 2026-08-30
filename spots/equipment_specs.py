"""What's worth recording about a rifle, a scope and a batch of ammo.

The three kinds deliberately do NOT share a field list -- barrel twist means
nothing to a box of ammo, and a bullet's ballistic coefficient means nothing
to a scope. This module is the single definition of those fields: the
equipment page builds its forms from it, and the API validates against it,
so neither can drift from the other.

Everything except the scope's click value is documentation -- recorded so a
session's history says what it was actually shot with. Click value is the
exception: it feeds the turret maths in Scope Correction, so it lives in its
own column rather than the free-form specs blob.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpecField:
    key: str
    label: str
    kind: str = "text"  # "text" | "number" | "select"
    placeholder: str = ""
    unit: str = ""
    options: tuple[tuple[str, str], ...] = ()
    step: str = "any"
    # True for the two fields kept as real table columns because a
    # calculation reads them; everything else goes in the specs JSON.
    column: bool = False


_MUZZLE_DEVICES = (
    ("", "None"),
    ("brake", "Muzzle brake"),
    ("suppressor", "Suppressor"),
    ("flash_hider", "Flash hider"),
    ("compensator", "Compensator"),
    ("thread_protector", "Thread protector"),
)

_ACTIONS = (
    ("", "Unspecified"),
    ("bolt", "Bolt action"),
    ("semi_auto", "Semi-automatic"),
    ("lever", "Lever action"),
    ("pump", "Pump action"),
    ("single_shot", "Single shot"),
    ("break", "Break action"),
)

EQUIPMENT_SPECS: dict[str, tuple[SpecField, ...]] = {
    "rifle": (
        SpecField("calibre", "Calibre", placeholder="e.g. .308 Winchester"),
        SpecField("barrel_length_in", "Barrel length", "number", unit="in", step="0.1",
                  placeholder="20"),
        SpecField("twist_rate", "Twist rate", placeholder="e.g. 1:10"),
        SpecField("muzzle_device", "Muzzle device", "select", options=_MUZZLE_DEVICES),
        SpecField("action", "Action", "select", options=_ACTIONS),
        SpecField("trigger_weight_lb", "Trigger weight", "number", unit="lb", step="0.1",
                  placeholder="2.5"),
    ),
    "scope": (
        SpecField("click_value", "Click value", "number", step="0.001", placeholder="0.25",
                  column=True),
        SpecField("click_unit", "Click unit", "select", column=True,
                  options=(("moa", "MOA"), ("mrad", "mrad"))),
        SpecField("magnification", "Magnification", placeholder="e.g. 5-25x56"),
        SpecField("reticle", "Reticle", placeholder="e.g. EBR-7C"),
        SpecField("focal_plane", "Focal plane", "select",
                  options=(("", "Unspecified"), ("ffp", "First (FFP)"), ("sfp", "Second (SFP)"))),
        SpecField("tube_diameter_mm", "Tube diameter", "number", unit="mm", step="0.1",
                  placeholder="30"),
        SpecField("zero_distance_m", "Zero distance", "number", unit="m", step="1",
                  placeholder="100"),
    ),
    "ammo": (
        SpecField("calibre", "Calibre", placeholder="e.g. .308 Winchester"),
        SpecField("bullet_grains", "Bullet weight", "number", unit="gr", step="0.1",
                  placeholder="168"),
        SpecField("bullet", "Bullet", placeholder="e.g. Sierra MatchKing HPBT"),
        SpecField("manufacturer", "Manufacturer", placeholder="e.g. Federal"),
        SpecField("powder", "Powder", placeholder="e.g. Varget"),
        SpecField("charge_grains", "Charge weight", "number", unit="gr", step="0.1",
                  placeholder="42.5"),
        SpecField("muzzle_velocity_fps", "Muzzle velocity", "number", unit="fps", step="1",
                  placeholder="2650"),
        SpecField("ballistic_coefficient", "Ballistic coefficient", "number", step="0.001",
                  placeholder="0.462"),
    ),
}

EQUIPMENT_TITLES = {"rifle": "Rifles", "scope": "Scopes", "ammo": "Ammo"}
EQUIPMENT_SINGULAR = {"rifle": "Rifle", "scope": "Scope", "ammo": "Ammo"}
EQUIPMENT_NAME_PLACEHOLDER = {
    "rifle": "e.g. Tikka T3x CTR",
    "scope": "e.g. Vortex Viper PST Gen II",
    "ammo": "e.g. Federal GMM 168gr",
}


def spec_fields(kind: str) -> tuple[SpecField, ...]:
    return EQUIPMENT_SPECS.get(kind, ())


def schema_payload() -> dict:
    """The field definitions in the shape the equipment page consumes."""
    return {
        kind: {
            "title": EQUIPMENT_TITLES[kind],
            "singular": EQUIPMENT_SINGULAR[kind],
            "name_placeholder": EQUIPMENT_NAME_PLACEHOLDER[kind],
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "type": f.kind,
                    "placeholder": f.placeholder,
                    "unit": f.unit,
                    "step": f.step,
                    "options": [{"value": v, "label": l} for v, l in f.options],
                    "column": f.column,
                }
                for f in fields_for(kind)
            ],
        }
        for kind in EQUIPMENT_SPECS
    }


def fields_for(kind: str) -> tuple[SpecField, ...]:
    return EQUIPMENT_SPECS.get(kind, ())


def clean_specs(kind: str, raw: dict) -> tuple[dict, list[str]]:
    """Validates a submitted spec dict against the schema for `kind`.

    Returns (cleaned, errors). Unknown keys are dropped, blanks are omitted
    entirely rather than stored as empty strings, numbers are parsed, and a
    select must be one of its declared options.
    """
    cleaned: dict = {}
    errors: list[str] = []
    if not isinstance(raw, dict):
        return cleaned, ["Specs must be an object"]
    for spec in fields_for(kind):
        if spec.column:
            continue  # handled as a real column by the caller
        value = raw.get(spec.key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if spec.kind == "number":
            try:
                cleaned[spec.key] = float(value)
            except (TypeError, ValueError):
                errors.append(f"{spec.label} must be a number")
            continue
        text = str(value).strip()
        if spec.kind == "select":
            allowed = {v for v, _ in spec.options}
            if text not in allowed:
                errors.append(f"{spec.label} is not one of the available options")
                continue
        cleaned[spec.key] = text
    return cleaned, errors


def summarise(item: dict) -> str:
    """Short one-line description for the sidebar and the header dropdowns --
    the couple of fields that actually identify a piece of kit at a glance.

    Takes the whole record rather than just its specs because a scope is
    best identified by its turret, which lives in a column.
    """
    kind = item.get("kind")
    specs = item.get("specs") or {}
    parts: list[str] = []
    if kind == "rifle":
        parts = [specs.get("calibre"), _with_unit(specs.get("barrel_length_in"), '"')]
    elif kind == "scope":
        click = None
        if item.get("click_value"):
            click = f"{float(item['click_value']):g} {item.get('click_unit') or 'moa'}/click"
        parts = [specs.get("magnification"), click]
    elif kind == "ammo":
        parts = [specs.get("calibre"), _with_unit(specs.get("bullet_grains"), " gr")]
    return " ".join(p for p in parts if p)


def _with_unit(value, suffix: str) -> str | None:
    if value in (None, ""):
        return None
    number = f"{float(value):g}"
    return f"{number}{suffix}"
