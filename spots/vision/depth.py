"""Distance to the target from how big it looks in the picture.

The camera is treated as a thin lens. The calibration says how much of the
target one pixel covers; the camera's pixel size and the telescope's focal
length say how big that pixel is on the sensor. Their ratio is the
magnification, and the lens equation gives the distance:

    m = (pixel size / digital zoom) / (target covered by one pixel)
    distance = f * (1 + 1/m)

Only the ZWO ASI camera on the telescope has a known focal length and pixel
size, so it must be the live feed and the scale calibrated through it.
"""
from __future__ import annotations

from spots.vision.groups import _UNIT_TO_METERS

FEED = "asi"

# The estimate's precision follows the calibration span, but the specs'
# focal length can be this far out, so the correction refuses anything
# further: a mistyped distance rather than real optics.
_CORRECTION_RANGE = (0.5, 2.0)

_FEED_NAMES = {"synthetic": "the simulated target", "zcam": "the Z CAM", "asi": "the ZWO ASI camera"}


def metres_per_unit(unit_name: str) -> float | None:
    return _UNIT_TO_METERS.get((unit_name or "").strip().lower())


def magnification(units_per_px: float, unit_name: str, zoom_level: float,
                  pixel_size_um: float) -> float | None:
    """Image size over object size, or None if a figure is unusable."""
    metres = metres_per_unit(unit_name)
    if not metres or units_per_px <= 0 or zoom_level <= 0 or pixel_size_um <= 0:
        return None
    on_sensor_m = pixel_size_um * 1e-6 / zoom_level
    on_target_m = units_per_px * metres
    return on_sensor_m / on_target_m


def distance_m(focal_length_mm: float, m: float) -> float:
    return focal_length_mm / 1000.0 * (1.0 + 1.0 / m)


def focal_length_for(known_distance_m: float, m: float) -> float:
    """The focal length, in mm, that puts the target at a measured distance."""
    return known_distance_m * 1000.0 * m / (1.0 + m)


def correction_allowed(focal_length_mm: float, spec_focal_length_mm: float) -> bool:
    low, high = _CORRECTION_RANGE
    return low * spec_focal_length_mm <= focal_length_mm <= high * spec_focal_length_mm


def estimate(feed: str, connected: bool, calibration, pixel_size_um: float | None,
             optics) -> dict:
    """The distance, or why there isn't one.

    `pixel_size_um` is what the camera reports, if anything; the Optics
    setting stands in otherwise.
    """
    def unavailable(reason: str, message: str) -> dict:
        return {"available": False, "reason": reason, "message": message}

    if feed != FEED:
        return unavailable("feed", "Needs the ZWO ASI camera as the live feed: the distance "
                                   "comes from the telescope's focal length and the ZWO's "
                                   "pixel size.")
    if not connected:
        return unavailable("camera", "The ZWO ASI camera isn't sending pictures.")
    if calibration is None:
        return unavailable("calibrate", "Calibrate the scale on the target to get a distance.")
    if getattr(calibration, "feed", "") != FEED:
        where = _FEED_NAMES.get(getattr(calibration, "feed", ""), "")
        return unavailable("recalibrate", "Calibrate the scale again with the ZWO camera live"
                           + (f": it was measured on {where}." if where else "."))

    pixel_from = "camera" if pixel_size_um else "settings"
    pixel = pixel_size_um or optics.pixel_size_um
    m = magnification(calibration.units_per_px, calibration.unit_name,
                      calibration.zoom_level, pixel)
    if m is None:
        return unavailable("unit", f"The target unit '{calibration.unit_name}' can't be "
                                   "converted to metres: use mm, cm, m, in or ft.")

    focal = optics.focal_length_mm()
    measured = optics.measured_focal_length_mm > 0
    return {
        "available": True,
        "distance_m": distance_m(focal, m),
        "magnification": m,
        "focal_length_mm": focal,
        "focal_from": "measured" if measured else "specs",
        "telescope_focal_length_mm": optics.telescope_focal_length_mm,
        "reducer": optics.reducer,
        "measured_at_m": optics.measured_at_m if measured else None,
        "pixel_size_um": pixel,
        "pixel_from": pixel_from,
        "zoom_level": calibration.zoom_level,
        "units_per_px": calibration.units_per_px,
        "unit_name": calibration.unit_name,
    }
