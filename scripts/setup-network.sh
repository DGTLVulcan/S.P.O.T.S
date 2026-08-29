#!/usr/bin/env bash
# Configures the Pi as its own network: wlan0 becomes a WiFi access point
# for phones/tablets, and eth0 becomes a static-IP DHCP server for the Z CAM
# plugged directly into it. Uses NetworkManager (nmcli), the default network
# stack on Raspberry Pi OS (Bookworm and later).
#
# WARNING: if you're running this over SSH via WiFi, putting wlan0 into AP
# mode will drop that connection immediately. Run this from the console, or
# over SSH via Ethernet, or a wired connection to the Pi.
#
# Safe to re-run -- existing spots-ap/spots-eth profiles are updated in
# place rather than duplicated.
#
# Env vars:
#   SPOTS_AP_SSID       WiFi network name (default: SPOTS)
#   SPOTS_AP_PASSWORD   WiFi password, 8-63 chars (default: randomly generated)
#   SPOTS_AP_IP         Pi's IP on the WiFi network (default: 192.168.4.1)
#   SPOTS_ETH_IP        Pi's IP on the Ethernet link to the camera (default: 192.168.10.1)
#   SPOTS_WIFI_COUNTRY  2-letter WiFi regulatory country code (default: US)
set -euo pipefail

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli not found -- this script needs NetworkManager (Raspberry Pi OS Bookworm+)." >&2
  echo "Skipping network setup. Configure wlan0/eth0 manually if you're on an older image." >&2
  exit 0
fi

AP_SSID="${SPOTS_AP_SSID:-SPOTS}"
AP_PASSWORD="${SPOTS_AP_PASSWORD:-$(tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 12)}"
AP_IP="${SPOTS_AP_IP:-192.168.4.1}"
ETH_IP="${SPOTS_ETH_IP:-192.168.10.1}"
WIFI_COUNTRY="${SPOTS_WIFI_COUNTRY:-US}"

if [ "${#AP_PASSWORD}" -lt 8 ]; then
  echo "error: WiFi password must be at least 8 characters (got ${#AP_PASSWORD})" >&2
  exit 1
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

if command -v raspi-config >/dev/null 2>&1; then
  echo "==> Setting WiFi country to $WIFI_COUNTRY (required for AP mode to broadcast)"
  $SUDO raspi-config nonint do_wifi_country "$WIFI_COUNTRY" || true
fi
$SUDO rfkill unblock wifi 2>/dev/null || true

# Make sure no other autoconnecting profile on these devices fights with
# ours for control of the interface (e.g. the default "Wired connection 1"
# NetworkManager creates automatically on first boot).
disable_other_profiles() {
  local device="$1" keep="$2"
  while IFS=: read -r name dev; do
    [ "$dev" = "$device" ] || continue
    [ "$name" = "$keep" ] && continue
    echo "==> Disabling autoconnect on existing profile '$name' (was bound to $device)"
    $SUDO nmcli connection modify "$name" autoconnect no || true
  done < <(nmcli -t -f NAME,DEVICE connection show)
}

echo "==> Configuring wlan0 as a WiFi access point (SSID: $AP_SSID)"
disable_other_profiles wlan0 spots-ap
if ! nmcli -t -f NAME connection show | grep -qx spots-ap; then
  $SUDO nmcli connection add type wifi ifname wlan0 con-name spots-ap autoconnect yes ssid "$AP_SSID"
fi
$SUDO nmcli connection modify spots-ap \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  ipv4.method shared \
  ipv4.addresses "${AP_IP}/24" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$AP_PASSWORD" \
  connection.autoconnect-priority 100
$SUDO nmcli connection up spots-ap

echo "==> Configuring eth0 as a static-IP DHCP server for the camera"
disable_other_profiles eth0 spots-eth
if ! nmcli -t -f NAME connection show | grep -qx spots-eth; then
  $SUDO nmcli connection add type ethernet ifname eth0 con-name spots-eth autoconnect yes
fi
$SUDO nmcli connection modify spots-eth \
  ipv4.method shared \
  ipv4.addresses "${ETH_IP}/24" \
  connection.autoconnect-priority 100
$SUDO nmcli connection up spots-eth

echo
echo "==> Network setup complete."
echo "    WiFi:     SSID '$AP_SSID', password '$AP_PASSWORD'"
echo "    Dashboard: http://${AP_IP}:8080/ (or http://$(hostname).local:8080/)"
echo "    Camera link: eth0 static IP $ETH_IP, DHCP serves the Z CAM automatically"
echo
echo "    Forgot the password later? Retrieve it with:"
echo "    sudo nmcli -s -g 802-11-wireless-security.psk connection show spots-ap"
