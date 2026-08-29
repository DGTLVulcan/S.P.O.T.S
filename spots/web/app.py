from __future__ import annotations

import atexit
import logging

import requests
from flask import Flask

from spots.camera.client import ZCamClient, ZCamError
from spots.camera.source import RtspFrameSource, SwitchableFrameSource, SyntheticFrameSource, ZoomFrameSource
from spots.config import Settings
from spots.storage import Storage
from spots.web.routes import bp
from spots.worker import DetectionWorker

logger = logging.getLogger(__name__)


def _make_zcam_factory(settings: Settings):
    def factory():
        client = ZCamClient(
            settings.camera.ip,
            settings.camera.stream_width,
            settings.camera.stream_height,
            settings.camera.stream_bitrate,
        )
        client.connect()
        return RtspFrameSource(client.rtsp_url()), client

    return factory


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
    worker = DetectionWorker(
        frame_source, storage, settings.target, settings.detection, settings.storage.snapshot_dir
    )
    worker.start()

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
