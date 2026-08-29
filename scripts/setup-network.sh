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
#   SPOTS_AP_PASSWORD   WiFi password, 8-63 chars (default: randomly generated
#                       8-digit PIN -- WPA2-PSK's 8-char minimum rules out
#                       anything shorter, numeric or not)
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
# head -c reads a bounded chunk from urandom *first*, then tr filters it --
# piping urandom (an infinite source) directly into tr | head the other way
# round means head closes the pipe after N bytes while tr is still trying
# to write more, killing tr with SIGPIPE; under `set -o pipefail` that
# aborts the whole script (exit 141) before it prints anything.
AP_PASSWORD="${SPOTS_AP_PASSWORD:-$(head -c 2048 /dev/urandom | tr -dc '0-9' | head -c 8)}"
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

# A fresh Pi ships with WiFi soft-blocked at the kernel level until a
# regulatory country is set -- the two lines above are meant to clear that,
# but either can fail silently (wrong country code, rfkill quirks) and
# nmcli's errors from there are much more confusing than the real cause. so
# check explicitly and stop here with an actionable message instead of
# pressing on into a guaranteed-to-fail AP setup.
if command -v rfkill >/dev/null 2>&1 && rfkill list wifi 2>/dev/null | grep -qi "blocked: yes"; then
  echo "error: WiFi is still rfkill-blocked after attempting to unblock it." >&2
  rfkill list wifi >&2
  echo >&2
  echo "If 'Hard blocked' is yes: check for a physical WiFi/airplane-mode switch." >&2
  echo "If 'Soft blocked' is yes: the regulatory country probably isn't set. Try:" >&2
  echo "  sudo raspi-config  ->  5 Localisation Options -> L4 WLAN Country" >&2
  echo "then re-run this script (or set SPOTS_WIFI_COUNTRY=<your 2-letter code> and re-run)." >&2
  echo "Or unblock directly: sudo rfkill unblock wifi" >&2
  exit 1
fi

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
