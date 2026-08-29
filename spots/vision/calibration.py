"""Pixel <-> real-world coordinate conversion.

MVP calibration is a manual two-point measurement: the user marks two points
on the target whose real-world distance they know (e.g. the target's printed
width) via the dashboard, and everything downstream converts pixel offsets
to real-world units using that scale. This is robust to outdoor lighting
since it's a one-time manual step, unlike auto-detecting fiducial markers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Calibration:
    units_per_px: float
    unit_name: str
    origin_px: tuple[float, float]

    @classmethod
    def from_two_points(
        cls,
        p1_px: tuple[float, float],
        p2_px: tuple[float, float],
        real_distance_units: float,
        unit_name: str,
        origin_px: tuple[float, float],
    ) -> "Calibration":
        pixel_distance = math.hypot(p2_px[0] - p1_px[0], p2_px[1] - p1_px[1])
        if pixel_distance <= 0:
            raise ValueError("Calibration points must be distinct")
        return cls(
            units_per_px=real_distance_units / pixel_distance,
            unit_name=unit_name,
            origin_px=origin_px,
        )

    def to_units(self, point_px: tuple[float, float]) -> tuple[float, float]:
        dx_px = point_px[0] - self.origin_px[0]
        dy_px = point_px[1] - self.origin_px[1]
        # Flip y so "up" is positive, matching how shooters read a target.
        return dx_px * self.units_per_px, -dy_px * self.units_per_px

    def px_per_unit(self) -> float:
        return 1.0 / self.units_per_px
