#!/usr/bin/env bash
# Reverts scripts/setup-network.sh: takes wlan0 out of AP mode so the Pi can
# rejoin a normal WiFi network for internet again. Safe to re-run. Run
# scripts/setup-network.sh (or `spots -initnetwork`) again later to go back
# to range mode.
#
# eth0 is restored to a normal DHCP client too, so the Pi can be plugged into
# an ordinary router/LAN and reached over Ethernet again. While spots-eth is
# active eth0 keeps serving its own DHCP on a fixed address and never asks a
# router for a lease, so it never appears on your LAN at all.
#
# NOTE: if you are SSH'd in *through* eth0, that session's address came from
# spots-eth's own DHCP server, so switching eth0 back will drop it -- you'll
# reconnect at whatever address your router hands out (check the router's
# client list, or try <hostname>.local). The switch is handed to systemd so
# it completes even if the SSH session dies mid-command; interrupting it
# halfway is what would otherwise leave eth0 with no usable profile at all.
# Use SPOTS_KEEP_ETH=1 to leave eth0 in camera-DHCP mode.
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

# Re-enable autoconnect on whatever other WiFi/wired profiles exist (setup-
# network.sh disabled them so they wouldn't fight spots-ap/spots-eth for the
# interface), so a previously-known network can reconnect on its own too.
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

  # Find an existing ethernet profile to hand eth0 back to. A Pi that has
  # only ever run range mode may not have one (NetworkManager's default
  # "Wired connection 1" is created on first carrier and could have been
  # deleted), in which case make one -- otherwise disabling spots-eth would
  # leave eth0 with nothing to come up on at all.
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

  # Order matters: every change that CAN'T drop the connection happens first,
  # so if the link does go down during the final switch the Pi is already
  # configured to come up correctly on its own (and on the next boot).
  $SUDO nmcli connection modify "$dhcp_profile" \
    ipv4.method auto connection.autoconnect yes connection.autoconnect-priority 0 || true
  $SUDO nmcli connection modify spots-eth autoconnect no 2>/dev/null || true

  # The activation itself is what kills an SSH session running over eth0.
  # Run it as a transient systemd unit so it is owned by init rather than
  # this terminal: a hangup partway through would otherwise abort the script
  # after spots-eth is down but before the DHCP profile is up, leaving the
  # Pi unreachable on Ethernet entirely.
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
