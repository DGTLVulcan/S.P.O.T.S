"""Automatic discovery of the Z CAM's IP address on the Pi's Ethernet link.

Field setup: the Z CAM always plugs into the Pi's Ethernet port, and (per
scripts/setup-network.sh) the Pi itself hands out DHCP on that link, so the
camera's address isn't fixed in advance and can change across power cycles.
Rather than depend on any particular DHCP server's lease-file format, this
just probes the Ethernet interface's subnet directly for whatever answers
like a Z CAM -- works regardless of which DHCP server (or a static IP)
actually put the camera on that address.
"""
from __future__ import annotations

import ipaddress
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 0.3
_MAX_WORKERS = 64


def _probe(ip: str) -> bool:
    try:
        resp = requests.get(f"http://{ip}/info", timeout=_PROBE_TIMEOUT_S)
    except requests.RequestException:
        return False
    return resp.status_code == 200


def _iface_ipv4_network(iface: str) -> ipaddress.IPv4Network | None:
    """Reads the interface's IPv4 address/prefix via `ip -4 addr show`,
    since that's present on every Raspberry Pi OS install with no extra
    Python dependencies. Returns None off-Pi (or if the interface is down/
    unconfigured) so discovery just fails closed rather than raising.
    """
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", iface],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        for i, tok in enumerate(parts):
            if tok == "inet" and i + 1 < len(parts):
                try:
                    return ipaddress.ip_interface(parts[i + 1]).network
                except ValueError:
                    continue
    return None


def discover_zcam_ip(configured_ip: str | None, eth_iface: str = "eth0") -> str | None:
    """Finds the Z CAM's IP address.

    Tries `configured_ip` first if given (fast path -- also the only thing
    that works when run off the Pi, e.g. local dev), then falls back to
    probing every host on `eth_iface`'s subnet in parallel. Returns None if
    nothing answers like a camera.
    """
    if configured_ip and _probe(configured_ip):
        return configured_ip

    network = _iface_ipv4_network(eth_iface)
    if network is None:
        logger.warning("Could not determine %s's subnet for Z CAM discovery", eth_iface)
        return None

    candidates = [str(ip) for ip in network.hosts() if str(ip) != configured_ip]
    if not candidates:
        return None

    logger.info("Scanning %s (%d hosts) on %s for a Z CAM...", network, len(candidates), eth_iface)
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_probe, ip): ip for ip in candidates}
        for future in as_completed(futures):
            if future.result():
                found = futures[future]
                logger.info("Found Z CAM at %s", found)
                return found

    logger.warning("No Z CAM found on %s (%s)", eth_iface, network)
    return None
