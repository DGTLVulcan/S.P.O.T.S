"""Settings loading for S.P.O.T.S.

Reads config.yaml (falling back to config.example.yaml if the user hasn't
made a local copy yet) into a tree of small dataclasses.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

import yaml

_DEFAULT_CONFIG_PATH = "config.yaml"
_EXAMPLE_CONFIG_PATH = "config.example.yaml"


@dataclass
class CameraConfig:
    source: str = "synthetic"  # "zcam" or "synthetic"
    # Empty string = auto-discover on the Ethernet link (see
    # spots/camera/discovery.py) -- the normal field setup, since the Pi
    # hands the camera its DHCP lease and its address isn't fixed. Set this
    # explicitly only if you want to skip discovery and pin a known IP.
    ip: str = ""
    stream_width: int = 1920
    stream_height: int = 1080
    stream_bitrate: int = 8_000_000
    # Software crop+resize zoom, for when the lens can't get physically
    # close enough to fill the frame with the target. 1.0 = no zoom.
    # center_x/center_y are fractional pan position (0-1) within the frame.
    digital_zoom: float = 1.0
    zoom_center_x: float = 0.5
    zoom_center_y: float = 0.5


@dataclass
class TargetConfig:
    width_units: float = 59.0
    unit_name: str = "cm"
    # Distance to target in meters, for converting group size to MOA. 0
    # means "not set" -- MOA is simply omitted from stats until this is.
    # Captured per-session at New Target time (like unit_name), so changing
    # it later doesn't retroactively alter a past session's MOA figures.
    distance_m: float = 0.0
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
    # A hole on a dark printed ring can differ from that ring by far less
    # than on a light one (e.g. hole=10 vs a dark ring=40 is a diff of just
    # 30, vs 200+ against a light ring) -- kept low enough to catch that,
    # relying on min/max hole area + circularity + the 2-frame debounce
    # below to reject noise rather than a high threshold. Raise this if
    # outdoor lighting flicker causes false positives; lower it if holes on
    # dark rings still aren't registering.
    diff_threshold: int = 20
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
    # MJPEG dashboard stream. Every streamed frame costs an overlay draw plus
    # a full JPEG encode, per connected viewer -- on a Pi that's the single
    # biggest steady CPU cost, and it's independent of the detection sample
    # rate (shots are still detected at detection.sample_fps no matter what
    # this is set to). 10 fps still looks live on a phone; raise it for a
    # smoother picture if you have CPU headroom.
    stream_fps: float = 10.0
    stream_quality: int = 80
    # Downscale the streamed picture to at most this many pixels wide (0 =
    # send it at native resolution). Detection is NOT affected -- it always
    # runs on the full-resolution frame; this only changes what gets pushed
    # to the browser. A full 1080p stream measured ~18 Mbit/s, which a Pi
    # running its own 2.4GHz access point cannot sustain: the stream backs
    # up in TCP and the picture arrives seconds late (looking like new shots
    # "don't appear until you refresh"). 960px is plenty on a phone and cuts
    # that by roughly 4x.
    stream_max_width: int = 960


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

    def save(self, path: str | None = None) -> None:
        """Persists to config.yaml (never config.example.yaml, regardless of
        which file was loaded from) so settings changes survive a restart.
        """
        chosen = path or _DEFAULT_CONFIG_PATH
        with open(chosen, "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(self), f, sort_keys=False)
