"""Shot-group statistics from a list of real-world shot coordinates."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass
class GroupStats:
    shot_count: int
    center: tuple[float, float]
    extreme_spread: float
    mean_radius: float
    std_dev: float
    unit_name: str


def compute_group_stats(points: list[tuple[float, float]], unit_name: str) -> GroupStats | None:
    if not points:
        return None

    cx = statistics.fmean(p[0] for p in points)
    cy = statistics.fmean(p[1] for p in points)
    radii = [math.hypot(x - cx, y - cy) for x, y in points]
    mean_radius = statistics.fmean(radii)
    std_dev = statistics.pstdev(radii) if len(points) > 1 else 0.0
    # Indexed loop rather than combinations(): same value without a tuple
    # per comparison, and this is O(n^2) on the shot-add path.
    extreme_spread = 0.0
    for i in range(len(points)):
        xi, yi = points[i]
        for j in range(i + 1, len(points)):
            xj, yj = points[j]
            separation = math.hypot(xi - xj, yi - yj)
            if separation > extreme_spread:
                extreme_spread = separation

    return GroupStats(
        shot_count=len(points),
        center=(cx, cy),
        extreme_spread=extreme_spread,
        mean_radius=mean_radius,
        std_dev=std_dev,
        unit_name=unit_name,
    )


# Only units with a fixed conversion. A custom unit_name can't be turned
# into metres, so MOA is omitted rather than guessed.
_UNIT_TO_METERS = {
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
    "in": 0.0254,
    "ft": 0.3048,
}
_ARCMINUTES_PER_DEGREE = 60.0


def to_moa(size: float, unit_name: str, distance_m: float | None) -> float | None:
    """Converts a linear group size to MOA (minutes of angle) at a given
    distance. Returns None if there's no usable distance or the unit isn't
    one of the fixed conversions above -- callers should treat that as "MOA
    not available" rather than an error.
    """
    if not distance_m or distance_m <= 0:
        return None
    factor = _UNIT_TO_METERS.get(unit_name.strip().lower())
    if factor is None:
        return None
    size_m = size * factor
    return math.degrees(math.atan(size_m / distance_m)) * _ARCMINUTES_PER_DEGREE


def to_mrad(size: float, unit_name: str, distance_m: float | None) -> float | None:
    """Converts a linear size to milliradians at a given distance. Same
    availability rules as to_moa(): None when there's no usable distance or
    the unit isn't one we can convert to meters.
    """
    if not distance_m or distance_m <= 0:
        return None
    factor = _UNIT_TO_METERS.get(unit_name.strip().lower())
    if factor is None:
        return None
    return math.atan(size * factor / distance_m) * 1000.0


def scope_correction(
    center: tuple[float, float],
    unit_name: str,
    distance_m: float | None,
    click_value: float,
    click_unit: str,
) -> dict | None:
    """Turret adjustment that would move the group onto the point of aim.

    `center` is the group centre relative to the marked point of aim, +x
    right and +y up. The dial goes the opposite way to the error: high and
    right needs DOWN and LEFT. None if the angle can't be worked out.
    """
    if click_value <= 0:
        return None
    to_angle = to_mrad if click_unit == "mrad" else to_moa
    horizontal = to_angle(abs(center[0]), unit_name, distance_m)
    vertical = to_angle(abs(center[1]), unit_name, distance_m)
    if horizontal is None or vertical is None:
        return None
    return {
        "click_unit": click_unit,
        "click_value": click_value,
        # Direction to turn the turret, i.e. opposite the group's error.
        "horizontal_dir": "left" if center[0] > 0 else "right",
        "vertical_dir": "down" if center[1] > 0 else "up",
        "horizontal_angle": horizontal,
        "vertical_angle": vertical,
        "horizontal_clicks": round(horizontal / click_value),
        "vertical_clicks": round(vertical / click_value),
    }


def best_subgroup(points: list[tuple[float, float]], n: int, unit_name: str) -> GroupStats | None:
    """Tightest N-shot subset by extreme spread, out of however many shots
    exist -- the standard "best N-shot group" precision-shooting metric.

    Still exact, but by depth-first branch and bound over a precomputed
    distance matrix rather than scoring every subset. Building GroupStats
    for all C(30,5)=142,506 measured ~3.6 s per dashboard poll; pruning any
    partial subset that already spans the best diameter found makes it a few
    milliseconds, since most branches blow the bound in two or three points.

    Callers should still cap the input (TargetConfig.best_subgroup_max_shots)
    -- the worst case is combinatorial, pruning just makes real groups cheap.

    Ties are common, since the diameter comes from one widest pair that many
    subsets share, so which optimal subset is reported can differ from the
    exhaustive version's. The extreme spread is identical either way; only
    the tied subgroup's own centre/mean radius/std dev can vary.
    """
    count = len(points)
    if n < 1 or count < n:
        return None
    if n == 1:
        # Every 1-shot "subgroup" has zero spread, so the exhaustive version
        # kept the first one (a tie never beat the incumbent). Match that.
        return compute_group_stats([points[0]], unit_name)

    # Adjacent indices end up spatially adjacent, so the first branches
    # explored are tight ones -- that's what makes the pruning below bite.
    ordered = sorted(points)

    distance = [[0.0] * count for _ in range(count)]
    for i in range(count):
        xi, yi = ordered[i]
        row_i = distance[i]
        for j in range(i + 1, count):
            xj, yj = ordered[j]
            separation = math.hypot(xi - xj, yi - yj)
            row_i[j] = separation
            distance[j][i] = separation

    best_diameter = math.inf
    best_indices: tuple[int, ...] | None = None

    def search(start: int, chosen: list[int], chosen_diameter: float) -> None:
        nonlocal best_diameter, best_indices
        still_needed = n - len(chosen)
        if still_needed == 0:
            # Pruning below already guarantees this beats the incumbent;
            # re-checking keeps that invariant explicit rather than implied.
            if chosen_diameter < best_diameter:
                best_diameter = chosen_diameter
                best_indices = tuple(chosen)
            return
        # Stop where too few points remain to finish the subset at all.
        for i in range(start, count - still_needed + 1):
            row_i = distance[i]
            widened = chosen_diameter
            prunable = False
            for c in chosen:
                separation = row_i[c]
                if separation >= best_diameter:
                    # Adding i can't beat the incumbent, and neither can any
                    # superset of this partial choice.
                    prunable = True
                    break
                if separation > widened:
                    widened = separation
            if prunable:
                continue
            chosen.append(i)
            search(i + 1, chosen, widened)
            chosen.pop()

    search(0, [], 0.0)
    if best_indices is None:
        return None
    return compute_group_stats([ordered[i] for i in best_indices], unit_name)
