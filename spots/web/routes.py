from __future__ import annotations

import csv
import io
import os
import time
from datetime import datetime

import cv2
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

from spots.vision.calibration import Calibration
from spots.vision.detection import invert_homography, warp_point
from spots.vision.groups import best_subgroup, compute_group_stats

bp = Blueprint("spots", __name__)

_JPEG_QUALITY = 80
_STREAM_INTERVAL_S = 1.0 / 15  # cap the MJPEG feed independent of detection sample rate


def _worker():
    return current_app.config["WORKER"]


def _settings():
    return current_app.config["SETTINGS"]


def _storage():
    return current_app.config["STORAGE"]


@bp.route("/")
def index():
    return render_template("index.html", target=_settings().target)


def _draw_overlay(frame, snapshot, homography):
    # Shots/stats are computed in the detector's anchor coordinate space.
    # The live feed shows the raw, unwarped camera frame, so anchor-space
    # points need the inverse homography applied to land in the right place.
    homography_inv = invert_homography(homography)

    for shot in snapshot.shots:
        center = warp_point((shot.x_px, shot.y_px), homography_inv)
        cv2.drawMarker(
            frame, (int(center[0]), int(center[1])), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 24, 2
        )
    if snapshot.stats is not None and snapshot.calibration is not None:
        cx_units, cy_units = snapshot.stats.center
        cx_px = snapshot.calibration.origin_px[0] + cx_units / snapshot.calibration.units_per_px
        cy_px = snapshot.calibration.origin_px[1] - cy_units / snapshot.calibration.units_per_px
        center = warp_point((cx_px, cy_px), homography_inv)
        cv2.drawMarker(
            frame, (int(center[0]), int(center[1])), (255, 128, 0), cv2.MARKER_CROSS, 30, 2
        )
    return frame


def _mjpeg_generator(worker):
    while True:
        frame = worker.get_latest_frame()
        if frame is not None:
            snapshot = worker.state.snapshot()
            homography = worker.get_last_homography()
            annotated = _draw_overlay(frame.copy(), snapshot, homography)
            ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
            if ok:
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
                )
        time.sleep(_STREAM_INTERVAL_S)


@bp.route("/video_feed")
def video_feed():
    return Response(
        _mjpeg_generator(_worker()), mimetype="multipart/x-mixed-replace; boundary=frame"
    )


def _stats_dict(stats):
    if stats is None:
        return None
    return {
        "shot_count": stats.shot_count,
        "center": stats.center,
        "extreme_spread": stats.extreme_spread,
        "mean_radius": stats.mean_radius,
        "std_dev": stats.std_dev,
    }


def _best_subgroups_dict(points, unit_name):
    return {
        str(n): _stats_dict(stats) for n, stats in _best_subgroups_for_points(points, unit_name).items()
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
            "shots": [
                {
                    "seq": s.seq,
                    "x_units": s.x_units,
                    "y_units": s.y_units,
                }
                for s in snapshot.shots
            ],
            "stats": _stats_dict(snapshot.stats),
            "best_subgroups": _best_subgroups_dict(points, unit_name),
        }
    )


@bp.route("/api/session/new", methods=["POST"])
def api_new_session():
    try:
        _worker().new_target()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"ok": True})


@bp.route("/api/session/undo", methods=["POST"])
def api_undo():
    _worker().undo_last()
    return jsonify({"ok": True})


@bp.route("/api/calibration", methods=["POST"])
def api_calibration():
    data = request.get_json(force=True)
    p1, p2, distance = data["p1"], data["p2"], float(data["distance"])
    calibration = Calibration.from_two_points(
        tuple(p1), tuple(p2), distance, _settings().target.unit_name, origin_px=tuple(p1)
    )
    _worker().set_calibration(calibration)
    return jsonify({"ok": True, "units_per_px": calibration.units_per_px})


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
    stats = compute_group_stats(points, unit_name)
    best_subgroups = _best_subgroups_for_points(points, unit_name)
    return render_template(
        "session_detail.html",
        session=session,
        shots=shots,
        stats=stats,
        best_subgroups=best_subgroups,
        unit_name=unit_name,
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
    writer.writerow(["seq", f"x_{unit_name}", f"y_{unit_name}", "x_px", "y_px", "timestamp"])
    for s in shots:
        ts = datetime.fromtimestamp(s["created_at"]).isoformat(timespec="seconds")
        writer.writerow([s["seq"], s["x_units"], s["y_units"], s["x_px"], s["y_px"], ts])

    resp = Response(buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename=spots_session_{session_id}.csv"
    return resp
