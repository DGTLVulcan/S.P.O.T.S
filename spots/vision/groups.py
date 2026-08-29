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
    # Indexed double loop rather than max(... for combinations(points, 2)):
    # same value, but without materializing a tuple pair per comparison --
    # this is O(n^2) and sits on the shot-add path, so the constant matters.
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


# Only units we know a fixed conversion for -- an arbitrary custom
# unit_name (anything else the user typed in Settings) can't be converted
# to meters, so MOA is simply omitted rather than guessed at.
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


def best_subgroup(points: list[tuple[float, float]], n: int, unit_name: str) -> GroupStats | None:
    """Tightest N-shot subset by extreme spread, out of however many shots
    exist -- the standard "best N-shot group" precision-shooting metric.

    Exact (it still finds the true optimum), but searched via depth-first
    branch and bound over a precomputed distance matrix rather than scoring
    every n-choose-k subset. The naive version built full GroupStats for
    each of C(30,5)=142,506 subsets on every dashboard poll, which measured
    at ~3.6 s per call on a dev box (far worse on a Pi) and pegged the
    server. Pruning any partial subset that already spans at least the best
    diameter found so far collapses that to a few milliseconds, since most
    branches blow the bound within their first two or three points.

    Callers should still cap the input size (see
    TargetConfig.best_subgroup_max_shots): the worst case is combinatorial,
    the pruning just makes real shot groups cheap.

    Exact ties are common (the diameter is set by one widest pair, which
    many different subsets share), and which of several equally-tight
    subsets gets reported may differ from the exhaustive version's -- both
    are optimal, and the reported extreme spread is identical either way.
    Only the tied subgroup's secondary figures (its own center/mean radius/
    std dev) can vary between two such answers.
    """
    count = len(points)
    if n < 1 or count < n:
        return None
    if n == 1:
        # Every 1-shot "subgroup" has zero spread, so the exhaustive version
        # kept the first one (a tie never beat the incumbent). Match that.
        return compute_group_stats([points[0]], unit_name)

    # Spatially adjacent points get adjacent indices, so the first branches
    # explored are already tight ones. That drives the bound down early,
    # which is what makes the pruning below bite.
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
