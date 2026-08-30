from __future__ import annotations

import csv
import io
import logging
import os
import time
from datetime import datetime

import cv2
import requests
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from spots import health
from spots.camera.client import ZCamError
from spots.camera.controls import CAMERA_CONTROL_KEYS, CAMERA_CONTROLS
from spots.equipment_specs import (
    calibre_of,
    calibres_match,
    clean_specs,
    schema_payload,
    summarise,
)
from spots.storage import EQUIPMENT_KINDS
from spots.vision.calibration import Calibration
from spots.vision.detection import invert_homography, warp_point
from spots.vision.groups import best_subgroup, compute_group_stats, scope_correction, to_moa

logger = logging.getLogger(__name__)

bp = Blueprint("spots", __name__)

# MJPEG frame rate and JPEG quality now come from WebConfig (see
# config.example.yaml's web.stream_fps / web.stream_quality) so the biggest
# steady CPU cost on a Pi is tunable without a code change.


def _worker():
    return current_app.config["WORKER"]


def _settings():
    return current_app.config["SETTINGS"]


def _storage():
    return current_app.config["STORAGE"]


def _zcam_client():
    # Dynamic, not a fixed startup value: the Z CAM connection is now made
    # lazily on first switch to the live feed (see SwitchableFrameSource),
    # so this may go from None to a real client mid-run.
    return _worker().get_zcam_client()


# BGR (OpenCV) equivalents of the validated blue/red pair from the design
# system's categorical palette -- shots and the group-center marker, chosen
# so they clear the CVD/normal-vision separation checks (unlike red/orange).
_SHOT_MARKER_BGR = (214, 120, 42)  # #2a78d6
_CENTER_MARKER_BGR = (72, 73, 227)  # #e34948


@bp.route("/")
def index():
    return render_template("index.html", target=_settings().target)


def _stream_target_size(width, height, max_width):
    """Streamed frame size for a native (width, height), or None to send it
    unscaled. Kept as one function so the click-coordinate conversion below
    and the stream itself can never disagree about the scale in use.
    """
    if max_width <= 0 or width <= max_width:
        return None
    return (max_width, max(1, round(height * max_width / width)))


def _view_to_frame_px(x, y):
    """Converts a click from streamed-image pixels to native frame pixels.

    The browser measures clicks against the <img>'s naturalWidth/Height,
    which is whatever the stream is actually sending -- so once the stream
    is downscaled, every click arrives shrunk by the same factor.
    Calibration, the detector and the overlay all work in native frame
    pixels, so undo it here, at the one boundary where view coordinates
    enter the app, rather than in four separate places in the browser.
    """
    max_width = _settings().web.stream_max_width
    if max_width <= 0:
        return x, y
    frame = _worker().get_latest_frame()
    if frame is None:
        return x, y
    native_height, native_width = frame.shape[:2]
    target = _stream_target_size(native_width, native_height, max_width)
    if target is None:
        return x, y
    return x * native_width / target[0], y * native_height / target[1]


def _draw_overlay(frame, snapshot, homography, scale=1.0):
    # Shots/stats are computed in the detector's anchor coordinate space.
    # The live feed shows the raw, unwarped camera frame, so anchor-space
    # points need the inverse homography applied to land in the right place.
    # Test shots are the exception: they're recorded directly in the raw
    # frame's own coordinate space (same convention as a Calibrate click),
    # since there's no detection event to have put them in anchor space to
    # begin with -- warping them here would misplace them.
    homography_inv = invert_homography(homography)

    for shot in snapshot.shots:
        center = (
            (shot.x_px, shot.y_px)
            if shot.is_test
            else warp_point((shot.x_px, shot.y_px), homography_inv)
        )
        cv2.drawMarker(
            frame,
            (int(center[0] * scale), int(center[1] * scale)),
            _SHOT_MARKER_BGR,
            cv2.MARKER_SQUARE if shot.is_test else cv2.MARKER_TILTED_CROSS,
            24,
            2,
        )
    if snapshot.stats is not None and snapshot.calibration is not None:
        cx_units, cy_units = snapshot.stats.center
        cx_px = snapshot.calibration.origin_px[0] + cx_units / snapshot.calibration.units_per_px
        cy_px = snapshot.calibration.origin_px[1] - cy_units / snapshot.calibration.units_per_px
        center = warp_point((cx_px, cy_px), homography_inv)
        cv2.drawMarker(
            frame,
            (int(center[0] * scale), int(center[1] * scale)),
            _CENTER_MARKER_BGR,
            cv2.MARKER_CROSS,
            30,
            2,
        )
    return frame


def _render_frame_jpeg(worker, quality, max_width):
    """Grabs the current frame, draws the overlay on it and JPEG-encodes it.
    Returns the encoded bytes, or None if no frame is available yet.
    """
    frame = worker.get_latest_frame()
    if frame is None:
        return None
    snapshot = worker.state.snapshot()
    homography = worker.get_last_homography()
    # Downscale BEFORE drawing, so markers keep a constant on-screen size
    # and stay crisp instead of being shrunk into faint lines. Shot
    # positions are in native frame pixels, so _draw_overlay scales them by
    # the same factor.
    target = _stream_target_size(frame.shape[1], frame.shape[0], max_width)
    scale = 1.0
    if target is not None:
        scale = target[0] / frame.shape[1]
        frame = cv2.resize(frame, target, interpolation=cv2.INTER_AREA)
    # No defensive copy: FrameSource.get_latest_frame() contracts to hand
    # back an array we own, so the overlay can be drawn straight into it
    # (a 1080p copy per streamed frame is not free on a Pi).
    annotated = _draw_overlay(frame, snapshot, homography, scale)
    ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


@bp.route("/frame.jpg")
def frame_jpeg():
    """One frame, fetched on demand by the dashboard.

    The dashboard pulls frames one at a time rather than consuming the
    MJPEG stream below, because a push stream has no backpressure: it
    keeps emitting on a timer whether or not the client is keeping up,
    and the excess piles into the kernel socket buffer (a couple of MB is
    ~70 frames here). Over a link slower than the emit rate -- which is
    what a Pi hosting its own 2.4GHz AP is -- the picture you see ends up
    being whatever was queued seconds ago, and reloading the page is the
    only way to discard the backlog. Pulling one frame at a time means
    only ever one is in flight, so latency is a single round trip and the
    rate self-adjusts to whatever the link can actually carry.
    """
    web = _settings().web
    data = _render_frame_jpeg(_worker(), web.stream_quality, web.stream_max_width)
    if data is None:
        abort(503)
    response = Response(data, mimetype="image/jpeg")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


def _mjpeg_generator(worker, interval_s, quality, max_width):
    while True:
        data = _render_frame_jpeg(worker, quality, max_width)
        if data is not None:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n"
        time.sleep(interval_s)


@bp.route("/video_feed")
def video_feed():
    """Continuous MJPEG stream. The dashboard no longer uses this (see
    /frame.jpg for why); kept because it is handy to point VLC/ffplay at,
    and it works fine on a link with headroom.
    """
    # Resolve everything needing app context up front: the generator is
    # consumed by the WSGI server after this request's context is gone.
    web = _settings().web
    interval_s = 1.0 / max(web.stream_fps, 0.1)
    return Response(
        _mjpeg_generator(_worker(), interval_s, web.stream_quality, web.stream_max_width),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


def _stats_dict(stats, unit_name, distance_m):
    if stats is None:
        return None
    return {
        "shot_count": stats.shot_count,
        "center": stats.center,
        "extreme_spread": stats.extreme_spread,
        "mean_radius": stats.mean_radius,
        "std_dev": stats.std_dev,
        "extreme_spread_moa": to_moa(stats.extreme_spread, unit_name, distance_m),
    }


def _scope_correction_dict(snapshot, target):
    """Turret advice for the current group, or None when it wouldn't mean
    anything: no group yet, no calibration, a calibration whose origin is
    still the first calibration click rather than the marked target centre,
    or no scope selected to take a click value from.
    """
    calibration = snapshot.calibration
    if snapshot.stats is None or calibration is None:
        return None
    if not calibration.origin_is_target_center:
        return None
    scope = _selected_equipment()["scope"]
    if scope is None or not scope["click_value"]:
        return None
    correction = scope_correction(
        snapshot.stats.center,
        target.unit_name,
        snapshot.distance_m,
        scope["click_value"],
        scope["click_unit"] or "moa",
    )
    if correction is not None:
        correction["scope_name"] = scope["name"]
    return correction


def _best_subgroups_dict(points, unit_name, distance_m):
    return {
        str(n): _stats_dict(stats, unit_name, distance_m)
        for n, stats in _best_subgroups_for_points(points, unit_name).items()
    }


@bp.route("/api/shots")
def api_shots():
    snapshot = _worker().state.snapshot()
    unit_name = _settings().target.unit_name
    points = [(s.x_units, s.y_units) for s in snapshot.shots if s.x_units is not None]
    return jsonify(
        {
            "session_id": snapshot.session_id,
            "calibrated": snapshot.calibration is not None,
            "unit_name": unit_name,
            "distance_m": snapshot.distance_m,
            "shots": [
                {
                    "seq": s.seq,
                    "x_units": s.x_units,
                    "y_units": s.y_units,
                    "is_test": s.is_test,
                    "excluded": s.excluded,
                    "created_at": s.created_at,
                }
                for s in snapshot.shots
            ],
            "stats": _stats_dict(snapshot.stats, unit_name, snapshot.distance_m),
            "best_subgroups": _best_subgroups_dict(points, unit_name, snapshot.distance_m),
            "scope_correction": _scope_correction_dict(snapshot, _settings().target),
            "equipment": {
                kind: (item["name"] if item else None)
                for kind, item in _selected_equipment().items()
            },
        }
    )


def _selected_equipment():
    """The currently selected rifle/scope/ammo records, by kind.

    The selection lives in the database alongside the equipment itself, so
    the two can't drift apart, and a record deleted since being selected
    reads as None rather than wedging the dashboard.
    """
    storage = _storage()
    selected = storage.get_selected_equipment()
    return {kind: (storage.get_equipment(i) if i else None) for kind, i in selected.items()}


def _equipment_payload():
    storage = _storage()
    chosen = _selected_equipment()
    # A rifle and its ammo have to agree on chambering. Compatibility is
    # decided here rather than in the browser so the rule that filters the
    # dropdowns is the same one that validates a selection.
    opposite = {"rifle": calibre_of(chosen["ammo"]), "ammo": calibre_of(chosen["rifle"])}

    def decorate(item):
        # A one-line "what is this" for the header dropdowns, derived from
        # whichever specs are filled in.
        item["summary"] = summarise(item)
        item["compatible"] = calibres_match(calibre_of(item), opposite.get(item["kind"]))
        return item

    return {
        # Explicit order: Flask sorts JSON object keys, so the schema/items
        # maps come back alphabetical (ammo, rifle, scope) and can't carry
        # the running order on their own.
        "order": list(EQUIPMENT_KINDS),
        "calibres": {kind: calibre_of(item) for kind, item in chosen.items()},
        "schema": schema_payload(),
        "items": {
            kind: [decorate(i) for i in storage.list_equipment(kind)] for kind in EQUIPMENT_KINDS
        },
        "selected": storage.get_selected_equipment(),
    }


@bp.route("/api/equipment")
def api_equipment_list():
    return jsonify(_equipment_payload())


def _parse_click_fields(data, errors):
    """Turret click value/unit, which only scopes carry. Blank means unset."""
    raw_value = data.get("click_value")
    click_value = None
    if raw_value not in (None, ""):
        try:
            click_value = float(raw_value)
        except (TypeError, ValueError):
            errors.append("Click value must be a number")
        else:
            if click_value <= 0:
                errors.append("Click value must be greater than zero")
    click_unit = data.get("click_unit") or "moa"
    if click_unit not in ("moa", "mrad"):
        errors.append("Click unit must be 'moa' or 'mrad'")
    return click_value, click_unit


@bp.route("/api/equipment", methods=["POST"])
def api_equipment_add():
    data = request.get_json(force=True)
    kind = data.get("kind")
    if kind not in EQUIPMENT_KINDS:
        return jsonify({"error": f"kind must be one of {', '.join(EQUIPMENT_KINDS)}"}), 400
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name can't be empty"}), 400
    errors: list[str] = []
    specs_in = data.get("specs") or {}
    click_value, click_unit = (
        _parse_click_fields(specs_in, errors) if kind == "scope" else (None, None)
    )
    specs, spec_errors = clean_specs(kind, specs_in)
    errors.extend(spec_errors)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400
    new_id = _storage().add_equipment(
        kind, name, (data.get("notes") or "").strip() or None, click_value, click_unit, specs
    )
    return jsonify({"ok": True, "id": new_id})


@bp.route("/api/equipment/<int:equipment_id>", methods=["POST"])
def api_equipment_update(equipment_id):
    existing = _storage().get_equipment(equipment_id)
    if existing is None:
        return jsonify({"error": f"No equipment #{equipment_id}"}), 404
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name can't be empty"}), 400
    errors: list[str] = []
    specs_in = data.get("specs") or {}
    click_value, click_unit = (
        _parse_click_fields(specs_in, errors) if existing["kind"] == "scope" else (None, None)
    )
    specs, spec_errors = clean_specs(existing["kind"], specs_in)
    errors.extend(spec_errors)
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400
    _storage().update_equipment(
        equipment_id, name, (data.get("notes") or "").strip() or None,
        click_value, click_unit, specs,
    )
    return jsonify({"ok": True})


@bp.route("/api/equipment/<int:equipment_id>/delete", methods=["POST"])
def api_equipment_delete(equipment_id):
    existing = _storage().get_equipment(equipment_id)
    if existing is None:
        return jsonify({"error": f"No equipment #{equipment_id}"}), 404
    _storage().delete_equipment(equipment_id)
    # get_selected_equipment() clears a dangling selection on read, so the
    # header stops showing a rifle that no longer exists without needing a
    # separate fix-up here.
    _storage().get_selected_equipment()
    return jsonify({"ok": True})


@bp.route("/api/equipment/select", methods=["POST"])
def api_equipment_select():
    data = request.get_json(force=True)
    kind = data.get("kind")
    if kind not in EQUIPMENT_KINDS:
        return jsonify({"error": f"kind must be one of {', '.join(EQUIPMENT_KINDS)}"}), 400
    raw_id = data.get("id")
    item_id = None
    if raw_id not in (None, ""):
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            return jsonify({"error": "id must be a number, or null to clear"}), 400
        item = _storage().get_equipment(item_id)
        if item is None or item["kind"] != kind:
            return jsonify({"error": f"No {kind} #{item_id}"}), 404

    chosen = _selected_equipment()
    cleared = None
    if kind == "ammo" and item_id is not None:
        # Ammo must suit the rifle already chosen -- the dropdown doesn't
        # offer a mismatch, so this only catches a direct API call.
        rifle = chosen["rifle"]
        if not calibres_match(calibre_of(item), calibre_of(rifle)):
            return jsonify({
                "error": f"{item['name']} is {calibre_of(item)}, but "
                         f"{rifle['name']} is {calibre_of(rifle)}"
            }), 409
    elif kind == "rifle" and item_id is not None:
        # Changing rifle is how you change calibre, so this must never be
        # refused: drop the now-unusable ammo instead of getting stuck
        # unable to pick either half of a new pairing.
        ammo = chosen["ammo"]
        if ammo is not None and not calibres_match(calibre_of(item), calibre_of(ammo)):
            _storage().set_selected_equipment("ammo", None)
            cleared = ammo["name"]

    _storage().set_selected_equipment(kind, item_id)
    return jsonify({"ok": True, "selected": item_id, "cleared_ammo": cleared})


@bp.route("/api/health")
def api_health():
    settings = _settings()
    return jsonify(
        health.collect(
            settings.storage.snapshot_dir,
            _worker().get_active_feed(),
            _worker().get_zcam_client() is not None,
        )
    )


@bp.route("/api/zoom")
def api_zoom_get():
    level, center_x, center_y = _worker().get_zoom()
    return jsonify({"level": level, "center_x": center_x, "center_y": center_y})


@bp.route("/api/zoom", methods=["POST"])
def api_zoom_set():
    data = request.get_json(force=True)
    try:
        # Clamp here too (not just in ZoomFrameSource) so config.yaml always
        # matches what's actually active.
        level = max(1.0, float(data["level"]))
        center_x = min(max(0.0, float(data["center_x"])), 1.0)
        center_y = min(max(0.0, float(data["center_y"])), 1.0)
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "level, center_x, center_y must all be numbers"}), 400

    _worker().set_zoom(level, center_x, center_y)

    settings = _settings()
    settings.camera.digital_zoom = level
    settings.camera.zoom_center_x = center_x
    settings.camera.zoom_center_y = center_y
    settings.save()

    return jsonify({"ok": True, "level": level, "center_x": center_x, "center_y": center_y})


@bp.route("/api/distance")
def api_distance_get():
    return jsonify({"distance_m": _settings().target.distance_m})


@bp.route("/api/distance", methods=["POST"])
def api_distance_set():
    data = request.get_json(force=True)
    try:
        distance_m = max(0.0, float(data["distance_m"]))
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "distance_m must be a number"}), 400

    _worker().set_distance(distance_m)
    _settings().save()

    return jsonify({"ok": True, "distance_m": distance_m})


@bp.route("/api/feed")
def api_feed_get():
    return jsonify({"active": _worker().get_active_feed()})


@bp.route("/api/feed", methods=["POST"])
def api_feed_set():
    data = request.get_json(force=True)
    target = data.get("target")
    if target not in ("synthetic", "zcam"):
        return jsonify({"error": "target must be 'synthetic' or 'zcam'"}), 400

    try:
        _worker().switch_feed(target)
    except (requests.RequestException, ZCamError) as exc:
        return jsonify({"error": f"Could not connect to the Z CAM: {exc}"}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"ok": True, "active": target})


@bp.route("/api/simulate/hole", methods=["POST"])
def api_simulate_hole():
    data = request.get_json(force=True)
    try:
        x = float(data["x"])
        y = float(data["y"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "x, y must both be numbers"}), 400

    x, y = _view_to_frame_px(x, y)
    if not _worker().add_simulated_hole(x, y):
        return jsonify({"error": "Simulated feed isn't active"}), 409

    return jsonify({"ok": True})


@bp.route("/api/test_shot", methods=["POST"])
def api_test_shot():
    data = request.get_json(force=True)
    try:
        x = float(data["x"])
        y = float(data["y"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "x, y must both be numbers"}), 400

    x, y = _view_to_frame_px(x, y)
    if not _worker().add_test_shot(x, y):
        return jsonify({"error": "No active session -- click New Target first"}), 409

    return jsonify({"ok": True})


@bp.route("/api/session/new", methods=["POST"])
def api_new_session():
    # Stamp the selected equipment onto the session by NAME, so history still
    # shows what a string was shot with even if that rifle is deleted later.
    chosen = _selected_equipment()
    try:
        _worker().new_target(
            equipment={kind: (item["name"] if item else None) for kind, item in chosen.items()}
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"ok": True})


@bp.route("/api/session/undo", methods=["POST"])
def api_undo():
    _worker().undo_last()
    return jsonify({"ok": True})


@bp.route("/api/shots/<int:seq>/delete", methods=["POST"])
def api_delete_shot(seq):
    if not _worker().delete_shot(seq):
        return jsonify({"error": f"No shot #{seq} in the current session"}), 404
    return jsonify({"ok": True})


@bp.route("/api/shots/<int:seq>/exclude", methods=["POST"])
def api_exclude_shot(seq):
    data = request.get_json(force=True)
    excluded = data.get("excluded", True)
    if not isinstance(excluded, bool):
        return jsonify({"error": "excluded must be true or false"}), 400
    if not _worker().set_shot_excluded(seq, excluded):
        return jsonify({"error": f"No shot #{seq} in the current session"}), 404
    return jsonify({"ok": True, "excluded": excluded})


@bp.route("/api/calibration", methods=["POST"])
def api_calibration():
    data = request.get_json(force=True)
    try:
        p1, p2, distance = tuple(data["p1"]), tuple(data["p2"]), float(data["distance"])
        p1 = _view_to_frame_px(float(p1[0]), float(p1[1]))
        p2 = _view_to_frame_px(float(p2[0]), float(p2[1]))
    except (KeyError, TypeError, ValueError, IndexError):
        return jsonify({"error": "p1, p2 must be [x, y] pairs and distance a number"}), 400

    try:
        calibration = Calibration.from_two_points(
            p1, p2, distance, _settings().target.unit_name, origin_px=p1
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    _worker().set_calibration(calibration)
    return jsonify({"ok": True, "units_per_px": calibration.units_per_px})


@bp.route("/api/calibration/center", methods=["POST"])
def api_calibration_center():
    data = request.get_json(force=True)
    try:
        x = float(data["x"])
        y = float(data["y"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "x, y must both be numbers"}), 400

    x, y = _view_to_frame_px(x, y)
    if not _worker().mark_center(x, y):
        return jsonify({"error": "Calibrate scale first, then mark the target's center"}), 409

    return jsonify({"ok": True})


def _purge_snapshots(snapshot_paths, session_ids):
    """Removes snapshot images for deleted sessions, then their now-empty
    directories -- otherwise they accumulate on the SD card with no session
    left pointing at them.
    """
    snapshot_dir = os.path.abspath(_settings().storage.snapshot_dir)
    for relpath in snapshot_paths:
        candidate = os.path.abspath(os.path.join(snapshot_dir, relpath))
        # Never follow a stored path outside the snapshot directory.
        # commonpath() also raises for paths on different drives.
        try:
            inside = os.path.commonpath([snapshot_dir, candidate]) == snapshot_dir
        except ValueError:
            inside = False
        if not inside:
            logger.warning("Refusing to delete snapshot outside %s: %s", snapshot_dir, candidate)
            continue
        try:
            os.remove(candidate)
        except OSError:
            logger.warning("Could not remove snapshot %s", candidate)
    for session_id in session_ids:
        try:
            os.rmdir(os.path.join(snapshot_dir, str(session_id)))
        except OSError:
            pass  # not empty or not there -- harmless either way


@bp.route("/api/session/<int:session_id>/rename", methods=["POST"])
def api_rename_session(session_id):
    data = request.get_json(force=True)
    name = data.get("name")
    if name is not None and not isinstance(name, str):
        return jsonify({"error": "name must be a string (or null to clear it)"}), 400
    if not _storage().rename_session(session_id, name):
        return jsonify({"error": f"No session #{session_id}"}), 404
    return jsonify({"ok": True})


@bp.route("/api/session/<int:session_id>/delete", methods=["POST"])
def api_delete_session(session_id):
    if _storage().get_session(session_id) is None:
        return jsonify({"error": f"No session #{session_id}"}), 404

    _purge_snapshots(_storage().delete_session(session_id), [session_id])

    # If the session being deleted is the one on screen, clear it rather
    # than leaving the dashboard showing shots that no longer exist.
    if _worker().state.snapshot().session_id == session_id:
        _worker().clear_session()

    return jsonify({"ok": True})


@bp.route("/api/sessions/delete_all", methods=["POST"])
def api_delete_all_sessions():
    session_ids = [s["id"] for s in _storage().list_sessions()]
    _purge_snapshots(_storage().delete_all_sessions(), session_ids)
    # Everything is gone, including whatever the dashboard was attached to.
    _worker().clear_session()
    return jsonify({"ok": True, "deleted": len(session_ids)})


@bp.route("/api/calibration/reset", methods=["POST"])
def api_calibration_reset():
    _worker().set_calibration(None)
    return jsonify({"ok": True})


def _best_subgroups_for_points(points, unit_name):
    target = _settings().target
    if len(points) > target.best_subgroup_max_shots:
        return {}
    result = {}
    for n in target.best_subgroup_sizes:
        stats = best_subgroup(points, n, unit_name)
        if stats is not None:
            result[n] = stats
    return result


def _fmt_ts(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds).strftime("%Y-%m-%d %H:%M:%S")


@bp.route("/sessions")
def sessions_list():
    sessions = _storage().list_sessions()
    for s in sessions:
        s["created_at_str"] = _fmt_ts(s["created_at"])
    return render_template("sessions_list.html", sessions=sessions)


@bp.route("/sessions/<int:session_id>")
def session_detail(session_id):
    session = _storage().get_session(session_id)
    if session is None:
        abort(404)
    shots = _storage().get_shots(session_id)
    unit_name = session["unit_name"]
    session["created_at_str"] = _fmt_ts(session["created_at"])
    for s in shots:
        s["created_at_str"] = _fmt_ts(s["created_at"])
    points = [(s["x_units"], s["y_units"]) for s in shots if s["x_units"] is not None]
    distance_m = session["distance_m"]
    stats = compute_group_stats(points, unit_name)
    best_subgroups = _best_subgroups_for_points(points, unit_name)
    return render_template(
        "session_detail.html",
        session=session,
        shots=shots,
        stats=stats,
        stats_moa=to_moa(stats.extreme_spread, unit_name, distance_m) if stats else None,
        best_subgroups=best_subgroups,
        best_subgroups_moa={
            n: to_moa(bs.extreme_spread, unit_name, distance_m) for n, bs in best_subgroups.items()
        },
        unit_name=unit_name,
        # Same shape renderTargetDiagram() consumes on the live dashboard.
        diagram_shots=[
            {"x_units": s["x_units"], "y_units": s["y_units"], "is_test": s["is_test"]}
            for s in shots
            if s["x_units"] is not None
        ],
        diagram_center=list(stats.center) if stats else None,
    )


@bp.route("/snapshots/<path:relpath>")
def snapshot_file(relpath):
    # A relative snapshot_dir resolves against Flask's app root_path
    # (spots/web/), not the process cwd where DetectionWorker actually
    # writes snapshots -- anchor it to cwd explicitly to match.
    snapshot_dir = os.path.abspath(_settings().storage.snapshot_dir)
    return send_from_directory(snapshot_dir, relpath)


@bp.route("/api/session/<int:session_id>/export.csv")
def export_csv(session_id):
    session = _storage().get_session(session_id)
    if session is None:
        abort(404)
    shots = _storage().get_shots(session_id)
    unit_name = session["unit_name"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["seq", f"x_{unit_name}", f"y_{unit_name}", "x_px", "y_px", "is_test", "timestamp"]
    )
    for s in shots:
        ts = datetime.fromtimestamp(s["created_at"]).isoformat(timespec="seconds")
        writer.writerow(
            [s["seq"], s["x_units"], s["y_units"], s["x_px"], s["y_px"], s["is_test"], ts]
        )

    resp = Response(buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename=spots_session_{session_id}.csv"
    return resp


# Fields that take effect immediately because the running worker/detector
# hold a live reference to the same Settings.target / Settings.detection
# objects being edited. Everything else (camera connection, sample rate,
# re-alignment on/off/method) is baked into objects built once at app
# startup, so those are saved to config.yaml but need a restart to apply.
_HOT_RELOAD_DETECTION_FIELDS = {
    "diff_threshold",
    "min_hole_area_px",
    "max_hole_area_px",
    "min_circularity",
    "min_shot_spacing_px",
    "debounce_frames",
    "realignment_min_matches",
}
_RESTART_REQUIRED_DETECTION_FIELDS = {"sample_fps", "realignment_enabled", "realignment_method"}


def _parse_int(form, name, errors):
    try:
        return int(form[name])
    except (KeyError, ValueError):
        errors.append(f"'{name}' must be a whole number")
        return None


def _parse_float(form, name, errors):
    try:
        return float(form[name])
    except (KeyError, ValueError):
        errors.append(f"'{name}' must be a number")
        return None


def _parse_subgroup_sizes(form, errors):
    raw = form.get("target.best_subgroup_sizes", "")
    try:
        sizes = [int(part.strip()) for part in raw.split(",") if part.strip()]
    except ValueError:
        errors.append("'Best subgroup sizes' must be a comma-separated list of whole numbers")
        return None
    if any(n <= 0 for n in sizes):
        errors.append("'Best subgroup sizes' must all be positive")
        return None
    return sizes


def _apply_settings_form(settings, form) -> list[str]:
    errors: list[str] = []

    unit_name = form.get("target.unit_name", "").strip()
    if not unit_name:
        errors.append("Unit name can't be empty")
    width_units = _parse_float(form, "target.width_units", errors)
    best_subgroup_sizes = _parse_subgroup_sizes(form, errors)
    best_subgroup_max_shots = _parse_int(form, "target.best_subgroup_max_shots", errors)

    diff_threshold = _parse_int(form, "detection.diff_threshold", errors)
    min_hole_area_px = _parse_int(form, "detection.min_hole_area_px", errors)
    max_hole_area_px = _parse_int(form, "detection.max_hole_area_px", errors)
    min_circularity = _parse_float(form, "detection.min_circularity", errors)
    min_shot_spacing_px = _parse_int(form, "detection.min_shot_spacing_px", errors)
    debounce_frames = _parse_int(form, "detection.debounce_frames", errors)
    sample_fps = _parse_float(form, "detection.sample_fps", errors)
    realignment_min_matches = _parse_int(form, "detection.realignment_min_matches", errors)
    realignment_method = form.get("detection.realignment_method", "orb")
    if realignment_method not in ("orb", "sift"):
        errors.append("Re-alignment method must be 'orb' or 'sift'")

    camera_source = form.get("camera.source", "synthetic")
    if camera_source not in ("zcam", "synthetic"):
        errors.append("Camera source must be 'zcam' or 'synthetic'")
    camera_ip = form.get("camera.ip", "").strip()
    stream_width = _parse_int(form, "camera.stream_width", errors)
    stream_height = _parse_int(form, "camera.stream_height", errors)
    stream_bitrate = _parse_int(form, "camera.stream_bitrate", errors)

    if (
        min_hole_area_px is not None
        and max_hole_area_px is not None
        and min_hole_area_px > max_hole_area_px
    ):
        errors.append("Min hole area can't be larger than max hole area")

    if errors:
        return errors

    settings.target.unit_name = unit_name
    settings.target.width_units = width_units
    settings.target.best_subgroup_sizes = best_subgroup_sizes
    settings.target.best_subgroup_max_shots = best_subgroup_max_shots

    settings.detection.diff_threshold = diff_threshold
    settings.detection.min_hole_area_px = min_hole_area_px
    settings.detection.max_hole_area_px = max_hole_area_px
    settings.detection.min_circularity = min_circularity
    settings.detection.min_shot_spacing_px = min_shot_spacing_px
    settings.detection.debounce_frames = debounce_frames
    settings.detection.realignment_min_matches = realignment_min_matches
    # Restart-required fields still get written so config.yaml is correct
    # for next launch, even though the running worker won't pick them up.
    settings.detection.sample_fps = sample_fps
    settings.detection.realignment_enabled = "detection.realignment_enabled" in form
    settings.detection.realignment_method = realignment_method

    settings.camera.source = camera_source
    settings.camera.ip = camera_ip
    settings.camera.stream_width = stream_width
    settings.camera.stream_height = stream_height
    settings.camera.stream_bitrate = stream_bitrate

    settings.save()
    return []


@bp.route("/equipment")
def equipment_page():
    return render_template("equipment.html")


@bp.route("/settings", methods=["GET", "POST"])
def settings_page():
    settings = _settings()
    if request.method == "POST":
        errors = _apply_settings_form(settings, request.form)
        if errors:
            return redirect(url_for("spots.settings_page", error=" / ".join(errors)))
        return redirect(url_for("spots.settings_page", saved=1))

    return render_template(
        "settings.html",
        settings=settings,
        saved=request.args.get("saved"),
        error=request.args.get("error"),
    )


def _camera_control_dict(client, control) -> dict | None:
    try:
        raw = client.get_setting(control.key)
    except (requests.RequestException, ZCamError):
        logger.exception("Failed to read camera control %s", control.key)
        return None
    result = {
        "key": control.key,
        "label": control.label,
        "value": raw.get("value"),
        "type": raw.get("type"),
        "ro": bool(raw.get("ro")),
    }
    if raw.get("type") == 1:
        result["opts"] = raw.get("opts", [])
    elif raw.get("type") == 2:
        result["min"] = raw.get("min")
        result["max"] = raw.get("max")
        result["step"] = raw.get("step")
    return result


@bp.route("/api/camera/controls")
def api_camera_controls_get():
    client = _zcam_client()
    if client is None:
        return jsonify({"available": False, "reason": "Camera source is synthetic", "controls": []})

    controls = [c for c in (_camera_control_dict(client, c) for c in CAMERA_CONTROLS) if c]
    return jsonify({"available": True, "controls": controls})


@bp.route("/api/camera/controls", methods=["POST"])
def api_camera_controls_set():
    client = _zcam_client()
    if client is None:
        return jsonify({"error": "Camera source is synthetic, no hardware to control"}), 503

    data = request.get_json(force=True)
    key = data.get("key")
    if key not in CAMERA_CONTROL_KEYS:
        return jsonify({"error": f"Unknown or unsupported control key: {key!r}"}), 400

    try:
        client.set_setting(key, data.get("value"))
        updated = client.get_setting(key)
    except (requests.RequestException, ZCamError) as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"ok": True, "value": updated.get("value")})
