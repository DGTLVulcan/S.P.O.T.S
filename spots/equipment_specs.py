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

import math
import re
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
    "target": (
        SpecField("face", "Target face", placeholder="e.g. NRA B-8, 25 yd"),
        SpecField("distance_m", "Intended distance", "number", unit="m", step="1",
                  placeholder="100"),
    ),
    "ammo": (
        SpecField("calibre", "Calibre", placeholder="e.g. .308 Winchester"),
        SpecField("bullet_grains", "Bullet weight", "number", unit="gr", step="0.1",
                  placeholder="168"),
        SpecField("bullet_diameter_mm", "Bullet diameter", "number", unit="mm", step="0.01",
                  placeholder="7.82"),
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

# Conditions a string was shot in. Every one is optional -- you often don't
# know the temperature, and a half-filled record is still worth more than
# none when comparing two groups weeks apart.
CONDITION_FIELDS: tuple[SpecField, ...] = (
    SpecField("wind_speed", "Wind speed", "number", unit="kph", step="0.5", placeholder="12"),
    SpecField("wind_direction", "Wind direction", "select", options=(
        ("", "Unspecified"), ("head", "Headwind"), ("tail", "Tailwind"),
        ("left", "Full value, left"), ("right", "Full value, right"),
        ("half_left", "Half value, left"), ("half_right", "Half value, right"),
        ("switching", "Switching"),
    )),
    SpecField("temperature_c", "Temperature", "number", unit="C", step="0.5", placeholder="18"),
    SpecField("humidity_pct", "Humidity", "number", unit="%", step="1", placeholder="60"),
    SpecField("pressure_hpa", "Pressure", "number", unit="hPa", step="1", placeholder="1013"),
    SpecField("light", "Light", "select", options=(
        ("", "Unspecified"), ("bright", "Bright sun"), ("overcast", "Overcast"),
        ("mixed", "Mixed"), ("low", "Low light"), ("mirage", "Heavy mirage"),
    )),
    SpecField("position", "Position", "select", options=(
        ("", "Unspecified"), ("bench", "Bench"), ("bipod", "Prone, bipod"),
        ("sling", "Prone, sling"), ("sitting", "Sitting"), ("standing", "Standing"),
        ("field", "Field improvised"),
    )),
    SpecField("notes", "Notes", placeholder="anything else about the string"),
)


def condition_fields() -> tuple[SpecField, ...]:
    return CONDITION_FIELDS


def conditions_schema() -> list[dict]:
    return [
        {
            "key": f.key, "label": f.label, "type": f.kind, "placeholder": f.placeholder,
            "unit": f.unit, "step": f.step,
            "options": [{"value": v, "label": l} for v, l in f.options],
        }
        for f in CONDITION_FIELDS
    ]


def clean_conditions(raw: dict) -> tuple[dict, list[str]]:
    """Same validation as equipment specs, against the conditions schema."""
    cleaned: dict = {}
    errors: list[str] = []
    if not isinstance(raw, dict):
        return cleaned, ["Conditions must be an object"]
    for spec in CONDITION_FIELDS:
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
        if spec.kind == "select" and text not in {v for v, _ in spec.options}:
            errors.append(f"{spec.label} is not one of the available options")
            continue
        cleaned[spec.key] = text
    return cleaned, errors


def describe_conditions(conditions: dict | None) -> str:
    """One line for lists and comparisons, e.g. "12 kph left, 18 C, overcast"."""
    conditions = conditions or {}
    labels = {f.key: dict(f.options) for f in CONDITION_FIELDS if f.kind == "select"}
    parts: list[str] = []
    wind = conditions.get("wind_speed")
    if wind not in (None, ""):
        chosen = conditions.get("wind_direction")
        direction = labels["wind_direction"].get(chosen, "") if chosen else ""
        parts.append(f"{float(wind):g} kph{' ' + direction.lower() if direction else ''}")
    if conditions.get("temperature_c") not in (None, ""):
        parts.append(f"{float(conditions['temperature_c']):g} C")
    for key in ("light", "position"):
        # Skip blanks: the schema's empty option is labelled "Unspecified",
        # which must not leak into the description as a value.
        chosen = conditions.get(key)
        if chosen:
            parts.append(labels[key].get(chosen, chosen).lower())
    return ", ".join(parts)


EQUIPMENT_TITLES = {"rifle": "Rifles", "scope": "Scopes", "ammo": "Ammo",
                    "target": "Targets"}
EQUIPMENT_SINGULAR = {"rifle": "Rifle", "scope": "Scope", "ammo": "Ammo",
                      "target": "Target"}
EQUIPMENT_NAME_PLACEHOLDER = {
    "rifle": "e.g. Tikka T3x CTR",
    "scope": "e.g. Vortex Viper PST Gen II",
    "ammo": "e.g. Federal GMM 168gr",
    "target": "e.g. NRA B-8 at 25 yd",
}


def clean_rings(raw) -> tuple[list, list[str]]:
    """Validates a target's scoring rings.

    Each is {"value": points, "diameter": across}, the diameter in the same
    unit as everything else on the target. Sorted smallest first so scoring
    can take the first ring a shot falls inside.
    """
    rings: list = []
    errors: list[str] = []
    if raw in (None, ""):
        return rings, errors
    if not isinstance(raw, list):
        return rings, ["Rings must be a list"]
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            errors.append(f"Ring {index} is malformed")
            continue
        value, diameter = entry.get("value"), entry.get("diameter")
        if value in (None, "") and diameter in (None, ""):
            continue  # a blank row in the editor
        try:
            value = float(value)
            diameter = float(diameter)
        except (TypeError, ValueError):
            errors.append(f"Ring {index} needs a number for both score and diameter")
            continue
        if diameter <= 0:
            errors.append(f"Ring {index} diameter must be greater than zero")
            continue
        rings.append({"value": value, "diameter": diameter})
    diameters = [r["diameter"] for r in rings]
    if len(set(diameters)) != len(diameters):
        errors.append("Two rings share a diameter")
    return sorted(rings, key=lambda r: r["diameter"]), errors


def score_shot(distance_from_centre: float, rings: list | None) -> float | None:
    """Points for a shot that landed `distance_from_centre` from the middle.

    A shot counts for the first ring whose radius it falls within, so the
    smallest (highest-scoring) ring wins. Outside every ring scores zero;
    with no rings defined there is nothing to score against and the result
    is None rather than 0, so "unscored" is distinguishable from "a miss".
    """
    if not rings:
        return None
    for ring in sorted(rings, key=lambda r: r["diameter"]):
        if distance_from_centre <= ring["diameter"] / 2.0:
            return ring["value"]
    return 0.0


def score_group(points: list[tuple[float, float]], rings: list | None) -> dict | None:
    """Totals a string against a target face. `points` are offsets from the
    marked centre, so their distance is the radius used for scoring.
    """
    if not rings:
        return None
    scores = [score_shot(math.hypot(x, y), rings) for x, y in points]
    best = max((r["value"] for r in rings), default=0)
    return {
        "total": sum(scores),
        "possible": best * len(scores),
        "shot_count": len(scores),
        "best_ring": best,
        "scores": scores,
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


def normalise_calibre(text: str | None) -> str:
    """Reduces a chambering to something comparable.

    People write the same cartridge half a dozen ways -- ".308 Winchester",
    ".308 Win", "308win" -- so punctuation, spacing and case are stripped
    before comparing. Returns "" when nothing is recorded.
    """
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def calibres_match(left: str | None, right: str | None) -> bool:
    """Whether two chamberings can be used together.

    An unrecorded calibre matches anything: the app can't prove a mismatch
    it has no data for, and refusing to pair kit just because a field is
    blank would be worse than letting it through. Otherwise one being a
    prefix of the other counts, so ".308 Win" and ".308 Winchester" agree
    while ".308" and ".300 Win Mag" don't.
    """
    a, b = normalise_calibre(left), normalise_calibre(right)
    if not a or not b:
        return True
    return a == b or a.startswith(b) or b.startswith(a)


def calibre_of(item: dict | None) -> str | None:
    if not item:
        return None
    return (item.get("specs") or {}).get("calibre")


def _with_unit(value, suffix: str) -> str | None:
    if value in (None, ""):
        return None
    number = f"{float(value):g}"
    return f"{number}{suffix}"
