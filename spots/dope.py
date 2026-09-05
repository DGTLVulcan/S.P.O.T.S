"""Where the ballistics solver meets the rest of the app.

Turns the rifle, scope and ammo you already have selected into a solver
input, works out what is still missing, and builds come-up cards from
either the solution or strings you have actually shot.
"""
from __future__ import annotations

import math
import re
import time

from spots import ballistics
from spots.equipment_specs import CONDITION_FIELDS

# The range card runs to here because that is what Eagle Park is certified
# to; the step keeps it to a card you can read on a phone.
DEFAULT_MAX_DISTANCE_M = 500
DEFAULT_STEP_M = 50

# Wind directions the conditions form offers, as clock positions. The
# half-value entries are the 1:30 / 4:30 diagonals shooters actually call.
_WIND_CLOCK = {
    "head": 12.0, "tail": 6.0,
    "right": 3.0, "left": 9.0,
    "half_right": 1.5, "half_left": 10.5,
}


def default_distances(maximum: int = DEFAULT_MAX_DISTANCE_M,
                      step: int = DEFAULT_STEP_M) -> list[int]:
    step = max(5, int(step))
    maximum = max(step, int(maximum))
    return list(range(step, maximum + 1, step))


def _number(source: dict, key: str):
    """A spec value as a float, or None -- specs are free text, so most of
    these are strings and plenty are blank."""
    if not isinstance(source, dict):
        return None
    raw = source.get(key)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_twist(raw) -> float | None:
    """Twist is written "1:10", "1 in 10", or just "10"."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    match = re.search(r"1\s*(?::|in)\s*([\d.]+)", text)
    if match:
        text = match.group(1)
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def parse_magnification(raw) -> tuple[float | None, float | None]:
    """The low and high power out of a scope's description.

    Written "4-16x40", "5-25x56", or "10x42" for a fixed scope, and often
    with the objective left off entirely. Returns (low, high), equal when
    the scope does not zoom, and (None, None) when it cannot be read --
    which is not an error, just a scope nobody has described yet.
    """
    text = str(raw or "").strip().lower().replace("×", "x")
    if not text:
        return None, None
    match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*x", text)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        if 0 < low <= high:
            return low, high
        return None, None
    match = re.search(r"(\d+(?:\.\d+)?)\s*x", text)
    if match:
        fixed = float(match.group(1))
        return (fixed, fixed) if fixed > 0 else (None, None)
    return None, None


def shot_from_equipment(equipment: dict, conditions: dict | None = None,
                        overrides: dict | None = None):
    """Build a solver input from the selected kit.

    Returns (shot, missing, used) -- `missing` lists the labels of anything
    required that nobody has recorded, so the page can ask for it rather
    than quietly substituting a default and producing a confident wrong
    answer.
    """
    equipment = equipment or {}
    conditions = conditions or {}
    overrides = overrides or {}

    rifle = (equipment.get("rifle") or {}).get("specs") or {}
    ammo = (equipment.get("ammo") or {}).get("specs") or {}
    scope_item = equipment.get("scope") or {}
    scope = scope_item.get("specs") or {}

    def pick(name, *sources, cast=float):
        if name in overrides and overrides[name] not in (None, ""):
            try:
                return cast(overrides[name])
            except (TypeError, ValueError):
                return None
        for source, key in sources:
            value = _number(source, key)
            if value is not None:
                return value
        return None

    velocity = pick("muzzle_velocity_fps", (ammo, "muzzle_velocity_fps"))
    bc = pick("ballistic_coefficient", (ammo, "ballistic_coefficient"))
    sight_height = pick("sight_height_mm", (rifle, "sight_height_mm"))
    zero = pick("zero_distance_m", (scope, "zero_distance_m"))

    missing = []
    if velocity is None:
        missing.append("Muzzle velocity (on the ammo)")
    if bc is None:
        missing.append("Ballistic coefficient (on the ammo)")
    if sight_height is None:
        missing.append("Sight height over bore (on the rifle)")
    if zero is None:
        missing.append("Zero distance (on the scope)")

    drag_model = overrides.get("drag_model") or ammo.get("drag_model") or "g7"
    if drag_model not in ballistics.DRAG_MODELS:
        drag_model = "g7"

    wind_speed = pick("wind_speed_kph", (conditions, "wind_speed"))
    wind_clock = overrides.get("wind_clock")
    if wind_clock in (None, ""):
        wind_clock = _WIND_CLOCK.get(conditions.get("wind_direction"), 3.0)

    atmosphere = ballistics.Atmosphere(
        temperature_c=pick("temperature_c", (conditions, "temperature_c"))
        if pick("temperature_c", (conditions, "temperature_c")) is not None
        else ballistics.STANDARD_TEMP_C,
        pressure_hpa=pick("pressure_hpa", (conditions, "pressure_hpa"))
        if pick("pressure_hpa", (conditions, "pressure_hpa")) is not None
        else ballistics.STANDARD_PRESSURE_HPA,
        humidity_pct=pick("humidity_pct", (conditions, "humidity_pct")) or 0.0,
    )

    shot = ballistics.Shot(
        muzzle_velocity_fps=velocity or 0.0,
        ballistic_coefficient=bc or 0.0,
        drag_model=drag_model,
        bullet_grains=pick("bullet_grains", (ammo, "bullet_grains")) or 0.0,
        bullet_diameter_mm=pick("bullet_diameter_mm", (ammo, "bullet_diameter_mm")) or 0.0,
        bullet_length_mm=pick("bullet_length_mm", (ammo, "bullet_length_mm")) or 0.0,
        sight_height_mm=sight_height if sight_height is not None else 40.0,
        zero_distance_m=zero if zero is not None else 100.0,
        twist_rate_in=parse_twist(overrides.get("twist_rate") or rifle.get("twist_rate")) or 0.0,
        wind_speed_kph=wind_speed or 0.0,
        wind_clock=float(wind_clock),
        look_angle_deg=float(overrides.get("look_angle_deg") or 0.0),
        atmosphere=atmosphere,
    )

    used = {
        "rifle": (equipment.get("rifle") or {}).get("name"),
        "scope": scope_item.get("name"),
        "ammo": (equipment.get("ammo") or {}).get("name"),
        "unit": scope_item.get("click_unit") or "mrad",
        "click_value": scope_item.get("click_value") or 0.0,
        # Nothing here reaches the solver. It is for drawing the scope
        # picture, which needs to know what you are looking through: the
        # marks, and whether their spacing holds at every magnification.
        "reticle": (scope.get("reticle") or "").strip(),
        "focal_plane": (scope.get("focal_plane") or "").strip(),
        "magnification": (scope.get("magnification") or "").strip(),
        # On a second focal plane scope the marks only subtend what they
        # claim at one power, so the picture needs to know which one. Blank
        # is left blank rather than guessed: the page says what it assumed.
        "reticle_calibration_x": _number(scope, "reticle_calibration_x"),
    }
    used["magnification_min"], used["magnification_max"] = parse_magnification(
        used["magnification"])
    return shot, missing, used


def unit_for(equipment: dict, override: str | None = None) -> str:
    """MOA or mrad -- the scope's own turret unit unless told otherwise."""
    if override in ("moa", "mrad"):
        return override
    unit = ((equipment or {}).get("scope") or {}).get("click_unit")
    return unit if unit in ("moa", "mrad") else "mrad"


def observations_from_sessions(sessions, unit: str) -> list[dict]:
    """Come-ups that were actually measured, pulled out of history.

    A session can be trued against only if it says three things: how far
    away the target was, what was dialled, and where the group landed. The
    first two are recorded per session; the third is the group centre the
    detector already worked out. Anything missing one of them is returned
    with a reason rather than silently skipped.
    """
    rows = []
    for session in sessions or []:
        distance = session.get("distance_m") or 0
        conditions = session.get("conditions") or {}
        dialled = _number(conditions, "dialled_elevation")
        centre = session.get("group_center")
        name = session.get("name") or f"Session #{session.get('id')}"

        if distance <= 0:
            rows.append({"session_id": session.get("id"), "name": name,
                         "usable": False, "why": "no distance recorded"})
            continue
        if centre is None:
            rows.append({"session_id": session.get("id"), "name": name,
                         "distance_m": distance, "usable": False,
                         "why": "no group centre (needs a marked point of aim)"})
            continue
        if dialled is None:
            rows.append({"session_id": session.get("id"), "name": name,
                         "distance_m": distance, "usable": False,
                         "why": "elevation dialled wasn't recorded"})
            continue

        # The group landed `centre` from the point of aim. What the rifle
        # actually needed is what was on the turret plus whatever would
        # have moved the group onto the aim point.
        unit_name = session.get("unit_name") or ""
        offset_m = _to_metres(centre[1], unit_name)
        if offset_m is None:
            rows.append({"session_id": session.get("id"), "name": name,
                         "distance_m": distance, "usable": False,
                         "why": f"can't convert {unit_name!r} to metres"})
            continue
        correction = ballistics.to_angle(-offset_m, distance, unit)
        rows.append({
            "session_id": session.get("id"),
            "name": name,
            "distance_m": round(distance, 1),
            "dialled": round(dialled, 2),
            "group_offset": round(correction, 2),
            "measured": round(dialled + correction, 2),
            "shots": session.get("shot_count"),
            "usable": True,
        })
    return rows


_UNIT_TO_METRES = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}


def _to_metres(value, unit_name: str):
    factor = _UNIT_TO_METRES.get((unit_name or "").strip().lower())
    if factor is None or value is None:
        return None
    return float(value) * factor


def blank_card(distances=None, unit: str = "mrad") -> dict:
    """An empty card to fill in by hand."""
    return {
        "unit": unit,
        "rows": [{"distance_m": d, "elevation": None, "windage": None, "note": ""}
                 for d in (distances or default_distances())],
        "source": "manual",
        "updated_at": time.time(),
    }


def card_key(equipment: dict) -> str:
    """Cards belong to a rifle and a load, not to the app as a whole."""
    rifle = (equipment.get("rifle") or {}).get("id") or 0
    ammo = (equipment.get("ammo") or {}).get("id") or 0
    return f"{int(rifle)}:{int(ammo)}"


def clean_card(raw) -> dict:
    """Whatever was stored, made safe to render."""
    if not isinstance(raw, dict):
        return blank_card()
    unit = raw.get("unit")
    rows = []
    for entry in (raw.get("rows") or [])[:60]:
        if not isinstance(entry, dict):
            continue
        distance = _number(entry, "distance_m")
        if distance is None or distance <= 0:
            continue
        rows.append({
            "distance_m": round(distance, 1),
            "elevation": _number(entry, "elevation"),
            "windage": _number(entry, "windage"),
            "note": str(entry.get("note") or "")[:80],
        })
    rows.sort(key=lambda r: r["distance_m"])
    return {
        "unit": unit if unit in ("moa", "mrad") else "mrad",
        "rows": rows,
        "source": raw.get("source") if raw.get("source") in ("manual", "solved", "trued")
        else "manual",
        "note": str(raw.get("note") or "")[:200],
        "updated_at": float(raw.get("updated_at") or time.time()),
    }
