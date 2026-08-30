from __future__ import annotations

import atexit
import logging

import requests
from flask import Flask

from spots.camera.client import ZCamClient, ZCamError
from spots.camera.discovery import discover_zcam_ip
from spots.camera.source import RtspFrameSource, SwitchableFrameSource, SyntheticFrameSource, ZoomFrameSource
from spots.config import Settings
from spots.storage import Storage
from spots.web.routes import bp
from spots.worker import DetectionWorker

logger = logging.getLogger(__name__)


def _make_zcam_factory(settings: Settings):
    def factory():
        # The camera always plugs into the Pi's Ethernet port and gets its
        # address from the Pi's own DHCP server (scripts/setup-network.sh),
        # so re-discover on every connect rather than trusting a stale IP.
        ip = discover_zcam_ip(settings.camera.ip or None)
        if ip is None:
            raise ZCamError("Could not find a Z CAM on the Ethernet link")
        if ip != settings.camera.ip:
            settings.camera.ip = ip
            settings.save()

        client = ZCamClient(
            ip,
            settings.camera.stream_width,
            settings.camera.stream_height,
            settings.camera.stream_bitrate,
        )
        client.connect()
        return RtspFrameSource(client.rtsp_url()), client

    return factory


def _migrate_equipment_selection(settings: Settings, storage: Storage) -> None:
    """Moves a selection previously kept in config.yaml into the database.

    The selection used to live in config.yaml while the equipment it refers
    to lived in the database -- two files that had to agree, where a
    regenerated or unwritable config silently lost the choice. It is now
    stored beside the equipment; this carries an existing choice across
    once, then clears it from the config so there's only one owner.
    """
    equipment = getattr(settings, "equipment", None)
    if equipment is None:
        return
    legacy = {
        "rifle": equipment.rifle_id,
        "scope": equipment.scope_id,
        "ammo": equipment.ammo_id,
    }
    if not any(legacy.values()):
        return
    already = storage.get_selected_equipment()
    moved = False
    for kind, item_id in legacy.items():
        if item_id and not already.get(kind):
            item = storage.get_equipment(item_id)
            if item is not None and item["kind"] == kind:
                storage.set_selected_equipment(kind, item_id)
                moved = True
        setattr(equipment, f"{kind}_id", None)
    try:
        settings.save()
    except OSError as exc:
        # Not fatal: the selection is already safe in the database, the
        # stale config keys are simply ignored from here on.
        logger.warning("Could not clear the old equipment selection from config: %s", exc)
    if moved:
        logger.info("Moved the equipment selection from config.yaml into the database")


def create_app(settings: Settings) -> Flask:
    switchable = SwitchableFrameSource(SyntheticFrameSource(), _make_zcam_factory(settings))
    if settings.camera.source == "zcam":
        # Preserves prior behavior for anyone who already configured "zcam"
        # as their default: connect eagerly at startup rather than waiting
        # for a dashboard toggle. But the camera being unreachable (powered
        # off, unplugged, asleep) must never take the whole app down with
        # it -- fall back to synthetic and let the dashboard's Live Feed
        # toggle retry once the camera's actually up.
        try:
            switchable.switch_to("zcam")
        except (requests.RequestException, ZCamError) as exc:
            logger.warning(
                "Could not connect to Z CAM at startup (%s) -- starting on the "
                "synthetic feed instead. Use the Live Feed toggle once the "
                "camera is reachable.",
                exc,
            )

    frame_source = ZoomFrameSource(
        switchable,
        settings.camera.digital_zoom,
        settings.camera.zoom_center_x,
        settings.camera.zoom_center_y,
    )
    storage = Storage(settings.storage.db_path)
    _migrate_equipment_selection(settings, storage)
    worker = DetectionWorker(
        frame_source, storage, settings.target, settings.detection, settings.storage.snapshot_dir
    )
    worker.start()
    # Pick up where the last run left off, so a restart or a Pi reboot
    # mid-string doesn't lose the shots and calibration already recorded.
    if worker.resume_last_session():
        logger.info("Resumed the most recent session from storage")
    # Auto hole-sizing needs the selected ammo's bullet diameter; without
    # this a resumed session would silently fall back to the fixed pixel
    # figures until the ammo was re-selected.
    selected_ammo = storage.get_selected_equipment().get("ammo")
    if selected_ammo:
        item = storage.get_equipment(selected_ammo)
        if item:
            worker.set_bullet_diameter_mm((item.get("specs") or {}).get("bullet_diameter_mm"))

    def _shutdown():
        worker.stop()
        zcam_client = switchable.get_zcam_client()
        if zcam_client is not None:
            zcam_client.close()
        storage.close()

    atexit.register(_shutdown)

    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config["WORKER"] = worker
    app.config["STORAGE"] = storage
    app.register_blueprint(bp)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.load()
    app = create_app(settings)
    app.run(host=settings.web.host, port=settings.web.port, threaded=True)


if __name__ == "__main__":
    main()
