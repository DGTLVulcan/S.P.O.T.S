"""Pi health readings for the dashboard.

Everything here is best-effort and read straight from /proc and /sys rather
than pulling in psutil: on a field device the point is to notice a full SD
card or a throttling CPU before it ruins a session, and any reading that
isn't available (running off-Pi, a kernel without that sysfs node) is
reported as None rather than raising.
"""
from __future__ import annotations

import os
import shutil
import time

# Pi 5 throttles around 80-85 C. Warn early enough to do something about it
# -- shade the enclosure, or take the lid off -- rather than at the cliff.
_TEMP_WARN_C = 70.0
_TEMP_CRITICAL_C = 80.0
_DISK_WARN_MB = 1024.0
_DISK_CRITICAL_MB = 256.0


def _read_first_line(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return None


def cpu_temperature_c() -> float | None:
    raw = _read_first_line("/sys/class/thermal/thermal_zone0/temp")
    if raw is None:
        return None
    try:
        # Reported in millidegrees on every Pi kernel.
        return int(raw) / 1000.0
    except ValueError:
        return None


def throttled_flags() -> dict | None:
    """Raspberry Pi under-voltage / throttling state.

    The bits are documented by the firmware: 0 under-voltage now, 1 arm
    frequency capped now, 2 throttled now, 3 soft temp limit now, and bits
    16-19 are the same conditions "since boot". A cheap USB supply showing
    up as under-voltage is a classic cause of a Pi behaving oddly in the
    field, and it is otherwise invisible.
    """
    raw = _read_first_line("/sys/devices/platform/soc/soc:firmware/get_throttled")
    if raw is None:
        return None
    try:
        bits = int(raw, 16 if raw.startswith("0x") else 10)
    except ValueError:
        return None
    return {
        "under_voltage_now": bool(bits & 0x1),
        "throttled_now": bool(bits & 0x4),
        "under_voltage_since_boot": bool(bits & 0x10000),
        "throttled_since_boot": bool(bits & 0x40000),
    }


def memory_mb() -> dict | None:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            fields = {}
            for line in handle:
                key, _, rest = line.partition(":")
                fields[key] = rest.strip().split()[0]
    except (OSError, IndexError):
        return None
    try:
        total = int(fields["MemTotal"]) / 1024.0
        available = int(fields["MemAvailable"]) / 1024.0
    except (KeyError, ValueError):
        return None
    return {"total_mb": total, "available_mb": available, "used_percent": 100.0 * (1 - available / total)}


def uptime_s() -> float | None:
    raw = _read_first_line("/proc/uptime")
    if raw is None:
        return None
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return None


def disk_free_mb(path: str) -> dict | None:
    try:
        usage = shutil.disk_usage(os.path.abspath(path) if os.path.exists(path) else ".")
    except OSError:
        return None
    return {
        "free_mb": usage.free / 1e6,
        "total_mb": usage.total / 1e6,
        "used_percent": 100.0 * usage.used / usage.total if usage.total else 0.0,
    }


def load_average() -> list[float] | None:
    try:
        return list(os.getloadavg())
    except (OSError, AttributeError):
        return None  # not available on Windows


def collect(storage_path: str, feed_active: str, camera_connected: bool) -> dict:
    """Everything the dashboard shows, plus a rolled-up status.

    `status` is the worst of the individual checks, so the UI can show one
    badge without re-deriving the thresholds: "ok", "warn" or "critical".
    """
    temperature = cpu_temperature_c()
    disk = disk_free_mb(storage_path)
    throttled = throttled_flags()
    memory = memory_mb()

    warnings: list[str] = []
    status = "ok"

    def escalate(level: str, message: str) -> None:
        nonlocal status
        warnings.append(message)
        if level == "critical" or status == "critical":
            status = "critical"
        else:
            status = "warn"

    if temperature is not None:
        if temperature >= _TEMP_CRITICAL_C:
            escalate("critical", f"CPU at {temperature:.0f} C -- throttling likely")
        elif temperature >= _TEMP_WARN_C:
            escalate("warn", f"CPU at {temperature:.0f} C")
    if disk is not None:
        if disk["free_mb"] <= _DISK_CRITICAL_MB:
            escalate("critical", f"Only {disk['free_mb']:.0f} MB of disk left")
        elif disk["free_mb"] <= _DISK_WARN_MB:
            escalate("warn", f"{disk['free_mb']:.0f} MB of disk left")
    if throttled:
        if throttled["under_voltage_now"]:
            escalate("critical", "Under-voltage right now -- check the power supply")
        elif throttled["throttled_now"]:
            escalate("critical", "CPU is being throttled right now")
        elif throttled["under_voltage_since_boot"]:
            escalate("warn", "Under-voltage seen since boot")
        elif throttled["throttled_since_boot"]:
            escalate("warn", "Throttling seen since boot")
    if feed_active == "zcam" and not camera_connected:
        escalate("warn", "Live feed selected but the camera isn't connected")

    return {
        "status": status,
        "warnings": warnings,
        "cpu_temp_c": temperature,
        "load_average": load_average(),
        "memory": memory,
        "disk": disk,
        "throttled": throttled,
        "uptime_s": uptime_s(),
        "feed_active": feed_active,
        "camera_connected": camera_connected,
        "server_time": time.time(),
    }
