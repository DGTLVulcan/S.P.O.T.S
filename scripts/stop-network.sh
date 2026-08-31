#!/usr/bin/env bash
# Reverts scripts/setup-network.sh: wlan0 leaves AP mode so the Pi can
# rejoin a normal WiFi network for internet. Safe to re-run; go back to
# range mode with setup-network.sh or `spots -initnetwork`.
#
# eth0 goes back to being a DHCP client too, so the Pi is reachable on an
# ordinary LAN again -- under spots-eth it serves its own DHCP on a fixed
# address and never appears on your network at all.
#
# NOTE: an SSH session over eth0 is on an address from spots-eth's own DHCP,
# so this drops it; reconnect at whatever the router hands out. The switch
# runs under systemd so a mid-command hangup can't leave eth0 with no usable
# profile. SPOTS_KEEP_ETH=1 leaves eth0 in camera-DHCP mode.
#
# Env vars:
#   SPOTS_WIFI_SSID      If set, reconnects wlan0 to this network as a client
#   SPOTS_WIFI_PASSWORD  Password for SPOTS_WIFI_SSID (omit for an open network)
#   SPOTS_KEEP_ETH       Set to 1 to leave eth0 serving the camera's DHCP
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

# Re-enable autoconnect on the other profiles setup-network.sh disabled,
# so a previously-known network can reconnect on its own.
while IFS= read -r name; do
  [ -n "$name" ] || continue
  [ "$name" = "spots-ap" ] && continue
  [ "$name" = "spots-eth" ] && continue
  echo "==> Re-enabling autoconnect on '$name'"
  $SUDO nmcli connection modify "$name" autoconnect yes 2>/dev/null || true
done < <(nmcli -g NAME connection show)

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

if [ "${SPOTS_KEEP_ETH:-0}" = "1" ]; then
  echo "==> SPOTS_KEEP_ETH=1, leaving eth0 serving the camera's DHCP"
else
  echo "==> Restoring eth0 to a normal DHCP client"

  # Find an ethernet profile to hand eth0 back to, and make one if there
  # isn't any: disabling spots-eth would otherwise leave eth0 with nothing
  # to come up on.
  dhcp_profile=""
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    [ "$name" = "spots-eth" ] && continue
    type="$(nmcli -g connection.type connection show "$name" 2>/dev/null || true)"
    iface="$(nmcli -g connection.interface-name connection show "$name" 2>/dev/null || true)"
    if [ "$type" = "802-3-ethernet" ] && { [ "$iface" = "eth0" ] || [ -z "$iface" ]; }; then
      dhcp_profile="$name"
      break
    fi
  done < <(nmcli -g NAME connection show)

  if [ -z "$dhcp_profile" ]; then
    dhcp_profile="spots-eth-dhcp"
    echo "    No ordinary ethernet profile found -- creating '$dhcp_profile'"
    $SUDO nmcli connection add type ethernet ifname eth0 con-name "$dhcp_profile" \
      autoconnect yes ipv4.method auto >/dev/null
  fi
  echo "    Handing eth0 back to '$dhcp_profile'"

  # Order matters: everything that can't drop the link happens first, so
  # the Pi is already configured correctly if the final switch cuts it.
  $SUDO nmcli connection modify "$dhcp_profile" \
    ipv4.method auto connection.autoconnect yes connection.autoconnect-priority 0 || true
  $SUDO nmcli connection modify spots-eth autoconnect no 2>/dev/null || true

  # This activation is what kills an SSH session on eth0, so hand it to
  # systemd: a hangup partway would otherwise abort with spots-eth down and
  # the DHCP profile not yet up, leaving the Pi unreachable.
  nmcli_path="$(command -v nmcli)"
  if command -v systemd-run >/dev/null 2>&1; then
    echo "    Switching now (detached, so it survives this session dropping)"
    $SUDO systemd-run --collect --unit=spots-restore-eth \
      "$nmcli_path" connection up "$dhcp_profile" >/dev/null 2>&1 || true
  else
    $SUDO nmcli connection up "$dhcp_profile" || true
  fi

  echo
  echo "    If this SSH session drops, that is the switch working. Reconnect at"
  echo "    the address your router assigns (check its client list, or try"
  echo "    ssh $(id -un)@$(hostname).local)."
fi

echo
echo "==> Done. Run scripts/setup-network.sh (or 'spots -initnetwork') again"
echo "    whenever you're ready to go back to range mode."
