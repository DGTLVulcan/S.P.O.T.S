#!/usr/bin/env bash
# Reverts scripts/setup-network.sh: takes wlan0 out of AP mode so the Pi can
# rejoin a normal WiFi network for internet again. Safe to re-run. Run
# scripts/setup-network.sh (or `spots -initnetwork`) again later to go back
# to range mode.
#
# WARNING: eth0 (the spots-eth camera-DHCP profile) is left alone by default.
# If you're connected to the Pi over SSH *through* eth0, your session's IP
# almost certainly came from spots-eth's own DHCP server -- reverting it will
# likely drop that session. Only set SPOTS_RESET_ETH=1 from the Pi's console,
# or a connection you're not relying on to run this script.
#
# Env vars:
#   SPOTS_WIFI_SSID      If set, reconnects wlan0 to this network as a client
#   SPOTS_WIFI_PASSWORD  Password for SPOTS_WIFI_SSID (omit for an open network)
#   SPOTS_RESET_ETH      Set to 1 to also revert eth0 to a normal DHCP client
set -euo pipefail

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli not found -- nothing to revert." >&2
  exit 0
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

echo "==> Taking wlan0 out of AP mode"
$SUDO nmcli connection down spots-ap 2>/dev/null || true
$SUDO nmcli connection modify spots-ap autoconnect no 2>/dev/null || true

# Re-enable autoconnect on whatever other WiFi/wired profiles exist (setup-
# network.sh disabled them so they wouldn't fight spots-ap/spots-eth for the
# interface), so a previously-known network can reconnect on its own too.
while IFS=: read -r name dev; do
  [ "$name" = "spots-ap" ] && continue
  [ "$name" = "spots-eth" ] && continue
  echo "==> Re-enabling autoconnect on '$name'"
  $SUDO nmcli connection modify "$name" autoconnect yes 2>/dev/null || true
done < <(nmcli -t -f NAME,DEVICE connection show)

if [ -n "${SPOTS_WIFI_SSID:-}" ]; then
  echo "==> Connecting wlan0 to '$SPOTS_WIFI_SSID'"
  if [ -n "${SPOTS_WIFI_PASSWORD:-}" ]; then
    $SUDO nmcli device wifi connect "$SPOTS_WIFI_SSID" password "$SPOTS_WIFI_PASSWORD"
  else
    $SUDO nmcli device wifi connect "$SPOTS_WIFI_SSID"
  fi
else
  echo "==> No SPOTS_WIFI_SSID given -- wlan0 is free to reconnect to a"
  echo "    previously-known network on its own, or connect manually:"
  echo "    sudo nmcli device wifi connect \"<SSID>\" password \"<password>\""
fi

if [ "${SPOTS_RESET_ETH:-0}" = "1" ]; then
  echo "==> Taking eth0 out of camera-DHCP mode (may drop an SSH session using it)"
  $SUDO nmcli connection down spots-eth 2>/dev/null || true
  $SUDO nmcli connection modify spots-eth autoconnect no 2>/dev/null || true
else
  echo "==> Leaving eth0/spots-eth alone (set SPOTS_RESET_ETH=1 to also revert it)"
fi

echo
echo "==> Done. Run scripts/setup-network.sh (or 'spots -initnetwork') again"
echo "    whenever you're ready to go back to range mode."
