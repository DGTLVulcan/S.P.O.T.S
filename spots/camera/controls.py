"""The subset of the Z CAM's ~150 /ctrl/set keys worth having on a fixed
range camera: exposure, colour and image, not the cinematography ones.
Keys confirmed against imaginevision/Z-Camera-Doc's api.js, not guessed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CameraControl:
    key: str
    label: str


CAMERA_CONTROLS: list[CameraControl] = [
    CameraControl("iso", "ISO"),
    CameraControl("ev", "Exposure (EV)"),
    CameraControl("wb", "White balance"),
    CameraControl("brightness", "Brightness"),
    CameraControl("contrast", "Contrast"),
    CameraControl("sharpness", "Sharpness"),
    CameraControl("saturation", "Saturation"),
]

CAMERA_CONTROL_KEYS = frozenset(c.key for c in CAMERA_CONTROLS)
