"""Shot-group statistics from a list of real-world shot coordinates."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from itertools import combinations


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
    extreme_spread = (
        max(math.hypot(x1 - x2, y1 - y2) for (x1, y1), (x2, y2) in combinations(points, 2))
        if len(points) > 1
        else 0.0
    )

    return GroupStats(
        shot_count=len(points),
        center=(cx, cy),
        extreme_spread=extreme_spread,
        mean_radius=mean_radius,
        std_dev=std_dev,
        unit_name=unit_name,
    )


def best_subgroup(points: list[tuple[float, float]], n: int, unit_name: str) -> GroupStats | None:
    """Tightest N-shot subset by extreme spread, out of however many shots
    exist -- the standard "best N-shot group" precision-shooting metric.

    Exhaustive (n-choose-k) by construction; callers should cap the input
    size (see TargetConfig.best_subgroup_max_shots) since this is not meant
    to scale to large shot counts.
    """
    if len(points) < n:
        return None
    best: GroupStats | None = None
    for combo in combinations(points, n):
        stats = compute_group_stats(list(combo), unit_name)
        if best is None or stats.extreme_spread < best.extreme_spread:
            best = stats
    return best
