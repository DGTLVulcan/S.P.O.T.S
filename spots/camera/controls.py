"""Curated subset of the Z CAM's ~150 /ctrl/set keys that are actually
useful for a fixed, unattended range camera -- exposure/color/image, not the
PTZ, audio, genlock, tally, or recording-format keys meant for cinematography.

Keys and query syntax confirmed against imaginevision/Z-Camera-Doc's
E2/protocol/http/api.js (the reference client implementation), not guessed.
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
