"""Renders a shot group as a shareable PNG.

Drawn server-side with OpenCV rather than screenshotting the browser: the
same picture comes out whichever device asked for it, it works for a past
session nobody has open, and OpenCV is already a dependency.

Deliberately plain -- a target diagram, the numbers, and what it was shot
with. It is meant to be legible on a phone and printable, not decorative.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

_WIDTH = 900
_HEIGHT = 1000
_MARGIN = 48

# Dark card, matching the dashboard's own palette so the two look related.
_BG = (17, 17, 17)
_PANEL = (26, 26, 25)
_INK = (255, 255, 255)
_INK_DIM = (160, 160, 155)
_GRID = (60, 60, 58)
_SHOT = (214, 120, 42)      # BGR of #2a78d6
_CENTER = (72, 73, 227)     # BGR of #e34948
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img, value, origin, scale=0.5, colour=_INK, thickness=1):
    cv2.putText(img, str(value), origin, _FONT, scale, colour, thickness, cv2.LINE_AA)


def _text_width(value, scale, thickness=1):
    return cv2.getTextSize(str(value), _FONT, scale, thickness)[0][0]


def _draw_diagram(img, top, height, shots, center, rings, unit_name):
    """Group diagram: scoring rings if the target defines them, otherwise
    plain reference rings at a third, two thirds and full scale."""
    cx, cy = _WIDTH // 2, top + height // 2
    plot_radius = min(_WIDTH - 2 * _MARGIN, height) // 2 - 30

    plotted = [s for s in shots if s.get("x_units") is not None]
    max_abs = 0.5
    for shot in plotted:
        max_abs = max(max_abs, abs(shot["x_units"]), abs(shot["y_units"]))
    if center:
        max_abs = max(max_abs, abs(center[0]), abs(center[1]))
    # Rings only constrain the scale when they'd otherwise be cropped away.
    if rings:
        max_abs = max(max_abs, max(r["diameter"] / 2.0 for r in rings))
    scale = plot_radius / (max_abs * 1.15)

    def to_px(x, y):
        return int(cx + x * scale), int(cy - y * scale)

    if rings:
        for ring in sorted(rings, key=lambda r: -r["diameter"]):
            radius = int(ring["diameter"] / 2.0 * scale)
            if radius < 4:
                continue
            cv2.circle(img, (cx, cy), radius, _GRID, 1, cv2.LINE_AA)
            _text(img, f"{ring['value']:g}", (cx + 4, cy - radius + 14), 0.4, _INK_DIM)
    else:
        for fraction in (0.33, 0.66, 1.0):
            cv2.circle(img, (cx, cy), int(plot_radius * fraction), _GRID, 1, cv2.LINE_AA)

    # Span the drawn content, not the whole panel: a single wide flyer pulls
    # the scale in, and a crosshair drawn to the full radius then stretches
    # far beyond anything plotted and reads as an error.
    extent = int(max_abs * scale) + 18
    cv2.line(img, (cx - extent, cy), (cx + extent, cy), _GRID, 1, cv2.LINE_AA)
    cv2.line(img, (cx, cy - extent), (cx, cy + extent), _GRID, 1, cv2.LINE_AA)

    for shot in plotted:
        px, py = to_px(shot["x_units"], shot["y_units"])
        if shot.get("excluded"):
            # Hollow, like the dashboard: present but not counted.
            cv2.circle(img, (px, py), 7, _INK_DIM, 1, cv2.LINE_AA)
        else:
            cv2.circle(img, (px, py), 7, _SHOT, -1, cv2.LINE_AA)
            cv2.circle(img, (px, py), 7, _BG, 1, cv2.LINE_AA)

    if center:
        px, py = to_px(center[0], center[1])
        cv2.drawMarker(img, (px, py), _CENTER, cv2.MARKER_CROSS, 22, 2)

    _text(img, f"scale in {unit_name}", (cx - extent, cy + extent + 22), 0.42, _INK_DIM)


def render_group_card(
    title: str,
    subtitle: str,
    stats: dict,
    shots: list,
    center,
    unit_name: str,
    rings=None,
    equipment: dict | None = None,
    conditions_summary: str = "",
    score: dict | None = None,
) -> bytes:
    """Returns the PNG bytes for one session's group."""
    img = np.full((_HEIGHT, _WIDTH, 3), _BG, np.uint8)

    _text(img, title, (_MARGIN, 62), 1.0, _INK, 2)
    if subtitle:
        _text(img, subtitle, (_MARGIN, 92), 0.5, _INK_DIM)
    cv2.line(img, (_MARGIN, 112), (_WIDTH - _MARGIN, 112), _GRID, 1)

    # Headline figures across the top, so they read first.
    tiles = [
        ("SHOTS", str(stats.get("shot_count", 0)) if stats else "0", ""),
        ("EXTREME SPREAD",
         f"{stats['extreme_spread']:.2f}" if stats else "-",
         f"{stats['extreme_spread_moa']:.2f} MOA"
         if stats and stats.get("extreme_spread_moa") is not None else unit_name),
        ("MEAN RADIUS", f"{stats['mean_radius']:.2f}" if stats else "-", unit_name),
        ("STD DEV", f"{stats['std_dev']:.2f}" if stats else "-", unit_name),
    ]
    if score:
        tiles.append(("SCORE", f"{score['total']:g}", f"of {score['possible']:g}"))

    tile_width = (_WIDTH - 2 * _MARGIN) // len(tiles)
    for index, (label, value, note) in enumerate(tiles):
        x = _MARGIN + index * tile_width
        cv2.rectangle(img, (x + 4, 132), (x + tile_width - 8, 214), _PANEL, -1)
        _text(img, label, (x + 16, 156), 0.38, _INK_DIM)
        _text(img, value, (x + 16, 190), 0.85, _INK, 2)
        if note:
            _text(img, note, (x + 16, 206), 0.36, _INK_DIM)

    _draw_diagram(img, 232, 470, shots, center, rings, unit_name)

    # What it was shot with, and in what.
    y = 754
    cv2.line(img, (_MARGIN, y - 24), (_WIDTH - _MARGIN, y - 24), _GRID, 1)
    for kind in ("rifle", "scope", "ammo", "target"):
        kit = (equipment or {}).get(kind)
        if not kit:
            continue
        _text(img, kind.upper(), (_MARGIN, y), 0.38, _INK_DIM)
        _text(img, kit.get("name", ""), (_MARGIN + 90, y), 0.5, _INK)
        specs = kit.get("specs") or {}
        if specs:
            detail = " / ".join(f"{v:g}" if isinstance(v, float) else str(v)
                                for v in list(specs.values())[:4])
            _text(img, detail, (_MARGIN + 90, y + 20), 0.4, _INK_DIM)
            y += 20
        y += 38

    if conditions_summary:
        _text(img, "CONDITIONS", (_MARGIN, y), 0.38, _INK_DIM)
        _text(img, conditions_summary, (_MARGIN + 90, y), 0.45, _INK)

    footer = "S.P.O.T.S"
    _text(img, footer, (_WIDTH - _MARGIN - _text_width(footer, 0.45), _HEIGHT - 24), 0.45, _INK_DIM)

    ok, buffer = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("Could not encode the group image")
    return buffer.tobytes()
