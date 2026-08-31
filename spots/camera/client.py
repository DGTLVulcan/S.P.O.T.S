"""HTTP control client for a Z CAM E2-series camera.

Wraps only the endpoints this project needs -- session control, stream
configuration, a reachability check -- per imaginevision/Z-Camera-Doc's
E2/protocol/http/http.md.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_S = 5.0
_REQUEST_TIMEOUT_S = 3.0


class ZCamError(RuntimeError):
    pass


class ZCamClient:
    """Controls a Z CAM E2-series camera over its HTTP API.

    connect() verifies reachability and occupies a control session;
    close() releases it.
    """

    def __init__(self, ip: str, stream_width: int, stream_height: int, stream_bitrate: int):
        self._base = f"http://{ip}"
        self._stream_width = stream_width
        self._stream_height = stream_height
        self._stream_bitrate = stream_bitrate
        self._session = requests.Session()
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_heartbeat = threading.Event()

    def _get(self, path: str, **params) -> dict:
        url = f"{self._base}{path}"
        resp = self._session.get(url, params=params, timeout=_REQUEST_TIMEOUT_S)
        if resp.status_code == 409:
            raise ZCamError(f"{path}: camera control session held by another client")
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {}

    def connect(self) -> None:
        info = self._get("/info")
        logger.info("Connected to Z CAM: %s", info)
        self._get("/ctrl/session", action="occupy")
        self._get(
            "/ctrl/stream_setting",
            index="stream1",
            width=self._stream_width,
            height=self._stream_height,
            bitrate=self._stream_bitrate,
        )
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_heartbeat.wait(_HEARTBEAT_INTERVAL_S):
            try:
                self._get("/ctrl/session")
            except requests.RequestException as exc:
                logger.warning("Z CAM heartbeat failed: %s", exc)

    def rtsp_url(self) -> str:
        ip = self._base.removeprefix("http://")
        return f"rtsp://{ip}/live_stream"

    def get_setting(self, key: str) -> dict:
        """Queries a camera setting via /ctrl/get?k=<key>. Response shape
        depends on the setting's type: choice ({"value","opts"}), range
        ({"value","min","max","step"}), or string ({"value"}) -- always
        includes "ro" (read-only) as documented in Z-Camera-Doc's api.js.
        """
        return self._get("/ctrl/get", k=key)

    def set_setting(self, key: str, value) -> dict:
        """Applies a camera setting via /ctrl/set?<key>=<value> (the query
        parameter *is* the setting's key name, per api.js: `/ctrl/set?${key}=${value}`).
        """
        return self._get("/ctrl/set", **{key: value})

    def close(self) -> None:
        self._stop_heartbeat.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=_HEARTBEAT_INTERVAL_S)
        try:
            self._get("/ctrl/session", action="quit")
        except requests.RequestException as exc:
            logger.warning("Failed to release Z CAM session cleanly: %s", exc)
