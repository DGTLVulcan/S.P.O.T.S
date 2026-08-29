"""Settings loading for S.P.O.T.S.

Reads config.yaml (falling back to config.example.yaml if the user hasn't
made a local copy yet) into a tree of small dataclasses.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

_DEFAULT_CONFIG_PATH = "config.yaml"
_EXAMPLE_CONFIG_PATH = "config.example.yaml"


@dataclass
class CameraConfig:
    source: str = "synthetic"  # "zcam" or "synthetic"
    ip: str = "192.168.1.188"
    stream_width: int = 1920
    stream_height: int = 1080
    stream_bitrate: int = 8_000_000


@dataclass
class TargetConfig:
    width_units: float = 59.0
    unit_name: str = "cm"
    # "Best N-shot subgroup" sizes to report (tightest N shots by extreme
    # spread, out of however many have been fired). Skipped for a given N
    # until at least N shots exist.
    best_subgroup_sizes: list[int] = field(default_factory=lambda: [3, 5])
    # Exhaustive subgroup search is combinatorial (n choose k); above this
    # many shots in the session it's skipped rather than eating Pi CPU.
    best_subgroup_max_shots: int = 30


@dataclass
class DetectionConfig:
    sample_fps: float = 3.0
    diff_threshold: int = 35
    min_hole_area_px: int = 20
    max_hole_area_px: int = 400
    min_circularity: float = 0.5
    min_shot_spacing_px: int = 12
    debounce_frames: int = 2
    # Re-align each incoming frame onto the reference's coordinates via ORB
    # feature matching + homography before diffing, so wind/shockwave sway
    # of the physical target doesn't get misread as shots or blur real ones.
    realignment_enabled: bool = True
    realignment_method: str = "orb"  # "orb" or "sift"
    realignment_min_matches: int = 15


@dataclass
class StorageConfig:
    db_path: str = "spots.db"
    snapshot_dir: str = "snapshots"


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class Settings:
    camera: CameraConfig = field(default_factory=CameraConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    web: WebConfig = field(default_factory=WebConfig)

    @classmethod
    def load(cls, path: str | None = None) -> "Settings":
        chosen = path or (
            _DEFAULT_CONFIG_PATH if os.path.exists(_DEFAULT_CONFIG_PATH) else _EXAMPLE_CONFIG_PATH
        )
        with open(chosen, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls(
            camera=CameraConfig(**raw.get("camera", {})),
            target=TargetConfig(**raw.get("target", {})),
            detection=DetectionConfig(**raw.get("detection", {})),
            storage=StorageConfig(**raw.get("storage", {})),
            web=WebConfig(**raw.get("web", {})),
        )
