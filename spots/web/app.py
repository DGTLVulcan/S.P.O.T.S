from __future__ import annotations

import atexit
import logging

from flask import Flask

from spots.camera.client import ZCamClient
from spots.camera.source import FrameSource, RtspFrameSource, SyntheticFrameSource
from spots.config import Settings
from spots.storage import Storage
from spots.web.routes import bp
from spots.worker import DetectionWorker

logger = logging.getLogger(__name__)


def _build_frame_source(settings: Settings) -> tuple[FrameSource, ZCamClient | None]:
    if settings.camera.source == "synthetic":
        return SyntheticFrameSource(), None

    if settings.camera.source == "zcam":
        client = ZCamClient(
            settings.camera.ip,
            settings.camera.stream_width,
            settings.camera.stream_height,
            settings.camera.stream_bitrate,
        )
        client.connect()
        return RtspFrameSource(client.rtsp_url()), client

    raise ValueError(f"Unknown camera.source: {settings.camera.source!r}")


def create_app(settings: Settings) -> Flask:
    frame_source, zcam_client = _build_frame_source(settings)
    storage = Storage(settings.storage.db_path)
    worker = DetectionWorker(
        frame_source, storage, settings.target, settings.detection, settings.storage.snapshot_dir
    )
    worker.start()

    def _shutdown():
        worker.stop()
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
