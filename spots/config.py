"""Settings loading for S.P.O.T.S.

Reads config.yaml (falling back to config.example.yaml if the user hasn't
made a local copy yet) into a tree of small dataclasses.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields

import yaml

_DEFAULT_CONFIG_PATH = "config.yaml"
_EXAMPLE_CONFIG_PATH = "config.example.yaml"
_DEFAULT_ENV_PATH = ".env"

# Overrides applied on top of the YAML, so a laptop can run the synthetic
# camera without editing the config.yaml the field install depends on.
#   env var -> (section, field, parser)
_ENV_OVERRIDES: dict[str, tuple[str, str, type]] = {
    "SPOTS_CAMERA_SOURCE": ("camera", "source", str),
    "SPOTS_CAMERA_IP": ("camera", "ip", str),
    "SPOTS_WEB_HOST": ("web", "host", str),
    "SPOTS_WEB_PORT": ("web", "port", int),
    "SPOTS_WEB_STREAM_FPS": ("web", "stream_fps", float),
    "SPOTS_WEB_STREAM_QUALITY": ("web", "stream_quality", int),
    "SPOTS_WEB_STREAM_MAX_WIDTH": ("web", "stream_max_width", int),
    "SPOTS_DB_PATH": ("storage", "db_path", str),
    "SPOTS_SNAPSHOT_DIR": ("storage", "snapshot_dir", str),
    "SPOTS_DETECTION_SAMPLE_FPS": ("detection", "sample_fps", float),
}


def load_dotenv(path: str = _DEFAULT_ENV_PATH) -> dict[str, str]:
    """Reads KEY=VALUE lines from a .env file into os.environ.

    Hand-rolled rather than python-dotenv, to keep one more package off the
    Pi. The real environment always wins over the file. Returns what it
    applied; a missing file is not an error.
    """
    applied: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return applied
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


@dataclass
class CameraConfig:
    source: str = "synthetic"  # "zcam" or "synthetic"
    # Blank = discover it on the Ethernet link (camera/discovery.py), which
    # is the field setup: the Pi hands out the lease, so the IP isn't fixed.
    ip: str = ""
    stream_width: int = 1920
    stream_height: int = 1080
    stream_bitrate: int = 8_000_000
    # Crop+resize zoom for when the lens can't reach. 1.0 = off; the
    # centres are a fractional pan position (0-1) within the frame.
    # Which fabricated target the synthetic source draws. "realistic" is
    # a paper sheet in front of a berm that moves in the wind, so holes show
    # ground through torn paper; "simple" is the flat one with black discs.
    synthetic_mode: str = "realistic"
    digital_zoom: float = 1.0
    zoom_center_x: float = 0.5
    zoom_center_y: float = 0.5


@dataclass
class TargetConfig:
    width_units: float = 59.0
    unit_name: str = "cm"
    # Metres, for MOA. 0 = unset, and MOA is omitted until it isn't.
    # Captured per session, so changing it can't rewrite past figures.
    distance_m: float = 0.0
    # Which "best N-shot subgroup" sizes to report, once N shots exist.
    best_subgroup_sizes: list[int] = field(default_factory=lambda: [3, 5])
    # Exhaustive subgroup search is combinatorial (n choose k); above this
    # many shots in the session it's skipped rather than eating Pi CPU.
    best_subgroup_max_shots: int = 30


@dataclass
class DetectionConfig:
    sample_fps: float = 3.0
    # Low, because a hole on a dark ring differs from it by far less than
    # one on white paper. Noise is rejected by area, circularity and the
    # debounce below rather than by a high threshold here.
    diff_threshold: int = 20
    # Size holes from bullet diameter + calibrated scale; the fixed pixel
    # figures below only ever suit one framing, and are the fallback.
    auto_hole_area: bool = True
    min_hole_area_px: int = 20
    max_hole_area_px: int = 400
    min_circularity: float = 0.5
    min_shot_spacing_px: int = 12
    # How far past a counted hole the reference keeps being refreshed, to
    # swallow whatever drift survives re-alignment. Raise it if one hole is
    # still counted more than once; too high and a shot landing right beside
    # an earlier one is absorbed instead of counted -- measured here, 5 px is
    # the most that still separates holes 14 px apart.
    burn_in_margin_px: int = 3
    debounce_frames: int = 2
    # Warp each frame onto the reference before diffing, so a target
    # swaying in the wind isn't read as a wall of new holes.
    realignment_enabled: bool = True
    realignment_method: str = "orb"  # "orb" or "sift"
    realignment_min_matches: int = 15


@dataclass
class EquipmentConfig:
    """Which rifle/scope/ammo is currently selected in the dashboard header.

    Only ids live here; the equipment itself is in the database. The
    selected scope supplies the turret click value used for corrections.
    """
    rifle_id: int | None = None
    scope_id: int | None = None
    ammo_id: int | None = None


@dataclass
class StorageConfig:
    db_path: str = "spots.db"
    snapshot_dir: str = "snapshots"


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    # Dashboard stream. Each frame costs an overlay draw plus a JPEG encode
    # per viewer -- the biggest steady CPU cost on a Pi. Detection is
    # unaffected; it runs at detection.sample_fps regardless.
    stream_fps: float = 10.0
    stream_quality: int = 80
    # Cap the streamed picture's width (0 = native). Detection still uses
    # the full-resolution frame. Native 1080p measured ~18 Mbit/s, which the
    # Pi's own 2.4GHz AP can't carry -- the stream then backs up in TCP and
    # arrives seconds late.
    stream_max_width: int = 960
    # The range-hot / cease-fire banner. A range indicator that is wrong is
    # worse than none, so it can be switched off outright rather than left
    # showing a state nobody is keeping up to date.
    range_status_enabled: bool = True
    # Space as a shortcut for the same toggle. Off is a legitimate choice:
    # it is a big lever on a small key.
    range_status_spacebar: bool = True


def _build(config_class, raw: dict):
    """Builds a config dataclass from YAML, ignoring keys it doesn't declare.

    Without this, removing a setting would break every existing install:
    the old key is still in their config.yaml, and the constructor would
    raise TypeError on it. Dropped keys disappear at the next save.
    """
    if not isinstance(raw, dict):
        return config_class()
    known = {f.name for f in fields(config_class)}
    return config_class(**{k: v for k, v in raw.items() if k in known})


@dataclass
class Settings:
    camera: CameraConfig = field(default_factory=CameraConfig)
    target: TargetConfig = field(default_factory=TargetConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    equipment: EquipmentConfig = field(default_factory=EquipmentConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    web: WebConfig = field(default_factory=WebConfig)

    @classmethod
    def load(cls, path: str | None = None, env_path: str | None = _DEFAULT_ENV_PATH) -> "Settings":
        """Loads config.yaml, then layers any SPOTS_* environment overrides
        on top (see _ENV_OVERRIDES). Pass env_path=None to skip .env entirely.
        SPOTS_CONFIG selects a different config file.
        """
        if env_path is not None:
            load_dotenv(env_path)
        chosen = path or os.environ.get("SPOTS_CONFIG") or (
            _DEFAULT_CONFIG_PATH if os.path.exists(_DEFAULT_CONFIG_PATH) else _EXAMPLE_CONFIG_PATH
        )
        with open(chosen, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        settings = cls(
            camera=_build(CameraConfig, raw.get("camera", {})),
            target=_build(TargetConfig, raw.get("target", {})),
            detection=_build(DetectionConfig, raw.get("detection", {})),
            equipment=_build(EquipmentConfig, raw.get("equipment", {})),
            storage=_build(StorageConfig, raw.get("storage", {})),
            web=_build(WebConfig, raw.get("web", {})),
        )
        settings._apply_env_overrides()
        return settings

    def _apply_env_overrides(self) -> None:
        # Remember the file's own value so save() can put it back, rather
        # than writing a dev override into config.yaml permanently.
        self._overridden_from_file = {}
        for env_key, (section, field_name, parser) in _ENV_OVERRIDES.items():
            raw_value = os.environ.get(env_key)
            if raw_value is None:
                continue
            target = getattr(self, section)
            try:
                value = parser(raw_value)
            except (TypeError, ValueError):
                continue  # unparseable override is ignored, not fatal
            self._overridden_from_file[(section, field_name)] = getattr(target, field_name)
            setattr(target, field_name, value)

    def save(self, path: str | None = None) -> None:
        """Persists to config.yaml (never config.example.yaml, regardless of
        which file was loaded from) so settings changes survive a restart.

        Environment values are written back as whatever the file held, so
        a .env never rewrites the config with development settings.
        """
        chosen = path or _DEFAULT_CONFIG_PATH
        overridden = getattr(self, "_overridden_from_file", {})
        for (section, field_name), original in overridden.items():
            setattr(getattr(self, section), field_name, original)
        try:
            with open(chosen, "w", encoding="utf-8") as f:
                yaml.safe_dump(asdict(self), f, sort_keys=False)
        finally:
            # Put the live overrides back so the running app keeps using them.
            for (section, field_name), _ in overridden.items():
                env_key = next(
                    k for k, v in _ENV_OVERRIDES.items() if (v[0], v[1]) == (section, field_name)
                )
                parser = _ENV_OVERRIDES[env_key][2]
                setattr(getattr(self, section), field_name, parser(os.environ[env_key]))
