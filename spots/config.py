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

# Environment overrides, applied on top of the YAML. The point is local
# development: run against the synthetic camera on a laptop without editing
# (and accidentally committing, or shipping to the Pi) a config.yaml that
# the field install needs pointed at the real camera.
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

    Deliberately hand-rolled rather than pulling in python-dotenv: it is a
    dozen lines, and the Pi install has one fewer package to fetch over a
    field connection. A variable already set in the real environment always
    wins, so `SPOTS_CAMERA_SOURCE=synthetic python S.P.O.T.S.py` beats the
    file. Returns what it applied; missing file is not an error.
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
    # Size a hole from the bullet diameter and the calibrated scale rather
    # than from these fixed pixel figures, which only ever suit one framing.
    # Falls back to them whenever it can't be worked out -- before
    # calibration, or with no bullet diameter recorded on the ammo.
    auto_hole_area: bool = True
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
class EquipmentConfig:
    """Which rifle/scope/ammo is currently selected in the dashboard header.

    Only the ids live here; the equipment itself is in the database, where it
    can be added to and edited. The selected scope supplies the turret click
    value used for scope correction, so different scopes carry different
    values instead of one global setting.
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


def _build(config_class, raw: dict):
    """Builds a config dataclass from YAML, ignoring keys it doesn't declare.

    Without this, removing a setting would break every existing install:
    config.yaml still holds the old key, and the dataclass constructor would
    raise TypeError on an unexpected keyword, so the app wouldn't start at
    all after an update. Unknown keys are simply dropped -- they disappear
    from the file the next time settings are saved.
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
        # Remembers what the file said so save() can put it back -- otherwise
        # a dev override would be written into config.yaml the first time
        # anything is saved from the Settings page, quietly turning a local
        # convenience into the committed configuration.
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

        Values that came from the environment are written back as whatever
        the file originally held, so running with a .env never rewrites the
        config with development settings.
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
