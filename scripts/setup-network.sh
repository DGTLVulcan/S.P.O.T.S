#!/usr/bin/env bash
# Makes the Pi its own network: wlan0 becomes a WiFi access point for
# phones and tablets, eth0 a static-IP DHCP server for the Z CAM plugged
# into it. Uses nmcli, the default stack on Raspberry Pi OS.
#
# WARNING: over SSH on WiFi this drops your connection the moment wlan0
# goes into AP mode. Run it from the console or over Ethernet.
#
# Safe to re-run: spots-ap/spots-eth are updated in place, not duplicated.
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
# head -c reads a bounded chunk BEFORE tr filters it. The other order
# (urandom | tr | head) SIGPIPEs tr when head closes the pipe, and under
# pipefail that killed the whole script with exit 141 before any output.
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

# Print the credentials BEFORE touching the network: everything below can
# drop the SSH session you're running over, taking a freshly generated
# password with it.
echo
echo "==================================================================="
echo "  WiFi access point credentials -- WRITE THESE DOWN NOW"
echo "==================================================================="
echo "    Network (SSID):  $AP_SSID"
echo "    Password:        $AP_PASSWORD"
echo "    Dashboard:       http://${AP_IP}:8080/"
echo "==================================================================="
echo "  Shown before any network change is made, because the steps below"
echo "  will disconnect an SSH session running over WiFi."
echo
echo "  To retrieve the password later:"
echo "    sudo nmcli -s -g 802-11-wireless-security.psk connection show spots-ap"
echo "==================================================================="
echo

if command -v raspi-config >/dev/null 2>&1; then
  echo "==> Setting WiFi country to $WIFI_COUNTRY (required for AP mode to broadcast)"
  $SUDO raspi-config nonint do_wifi_country "$WIFI_COUNTRY" || true
fi
$SUDO rfkill unblock wifi 2>/dev/null || true

# rfkill only clears the KERNEL block. NetworkManager keeps its own
# WirelessEnabled flag in NetworkManager.state, and while that is false it
# holds wlan0 "unavailable" after every reboot however often rfkill is
# cleared. Only nmcli rewrites that file, so the AP survives a reboot.
echo "==> Enabling NetworkManager's WiFi radio (persists across reboots)"
$SUDO nmcli radio wifi on || true

# A fresh Pi soft-blocks WiFi until a regulatory country is set. The lines
# above clear that but can fail silently, and nmcli's later errors hide the
# real cause -- so check here and stop with something actionable.
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

# Stop any other autoconnecting profile grabbing these interfaces -- the
# default "Wired connection 1", or a saved home-WiFi profile.
#
# Deliberately NOT filtered on the DEVICE column: nmcli only fills that in
# for currently-active connections, which silently skipped every inactive
# profile -- exactly the ones that compete for the interface next boot.
disable_competing_profiles() {
  local keep="$1" want_type="$2" want_iface="$3"
  local name type iface
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    [ "$name" = "$keep" ] && continue
    type="$(nmcli -g connection.type connection show "$name" 2>/dev/null || true)"
    iface="$(nmcli -g connection.interface-name connection show "$name" 2>/dev/null || true)"
    # Either it is pinned to our interface, or it is an unpinned profile of
    # the same type, which NetworkManager is free to bring up on it.
    if [ "$iface" = "$want_iface" ] || { [ -z "$iface" ] && [ "$type" = "$want_type" ]; }; then
      echo "==> Disabling autoconnect on competing profile '$name'"
      $SUDO nmcli connection modify "$name" autoconnect no || true
    fi
  done < <(nmcli -g NAME connection show)
}

echo "==> Configuring wlan0 as a WiFi access point (SSID: $AP_SSID)"
disable_competing_profiles spots-ap 802-11-wireless wlan0
if ! nmcli -g NAME connection show | grep -qx spots-ap; then
  $SUDO nmcli connection add type wifi ifname wlan0 con-name spots-ap autoconnect yes ssid "$AP_SSID"
fi
$SUDO nmcli connection modify spots-ap \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  802-11-wireless.powersave 2 \
  ipv4.method shared \
  ipv4.addresses "${AP_IP}/24" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$AP_PASSWORD" \
  connection.autoconnect yes \
  connection.autoconnect-priority 100 \
  connection.autoconnect-retries 0
$SUDO nmcli connection up spots-ap || true

echo "==> Configuring eth0 as a static-IP DHCP server for the camera"
disable_competing_profiles spots-eth 802-3-ethernet eth0
if ! nmcli -g NAME connection show | grep -qx spots-eth; then
  $SUDO nmcli connection add type ethernet ifname eth0 con-name spots-eth autoconnect yes
fi
$SUDO nmcli connection modify spots-eth \
  ipv4.method shared \
  ipv4.addresses "${ETH_IP}/24" \
  connection.autoconnect yes \
  connection.autoconnect-priority 100 \
  connection.autoconnect-retries 0
$SUDO nmcli connection up spots-eth || true

# Confirm both profiles come back on their own: autoconnect has to be
# "yes" on disk, not merely active now, or a reboot quietly reverts to
# NetworkManager's default -- better found here than at the range.
setup_ok=1
for profile in spots-ap spots-eth; do
  autoconnect="$(nmcli -g connection.autoconnect connection show "$profile" 2>/dev/null || true)"
  active="$(nmcli -g GENERAL.STATE connection show "$profile" 2>/dev/null || true)"
  echo "==> $profile: autoconnect=${autoconnect:-unknown} state=${active:-inactive}"
  if [ "$autoconnect" != "yes" ]; then
    echo "warning: $profile is not set to autoconnect -- it will NOT survive a reboot." >&2
    setup_ok=0
  fi
  if [ "$active" != "activated" ]; then
    echo "warning: $profile did not activate now (it is still set to come up on boot)." >&2
    if [ "$profile" = "spots-ap" ]; then
      echo "         Check: nmcli device status; journalctl -u NetworkManager -b | tail -40" >&2
      echo "         NetworkManager needs dnsmasq-base installed for 'shared' DHCP to work." >&2
    fi
    setup_ok=0
  fi
done

echo
if [ "$setup_ok" -eq 1 ]; then
  echo "==> Network setup complete (repeating the credentials from above)."
else
  echo "==> Network setup finished WITH WARNINGS -- see above." >&2
fi
echo "    WiFi:        SSID '$AP_SSID', password '$AP_PASSWORD'"
echo "    Dashboard:   http://${AP_IP}:8080/ (or http://$(hostname).local:8080/)"
echo "    Camera link: eth0 static IP $ETH_IP, DHCP serves the Z CAM automatically"
echo
echo "    Both profiles are saved in NetworkManager with autoconnect on, so"
echo "    they come back by themselves on every boot. They stay that way"
echo "    until you run 'spots -stopnetwork'."
echo
echo "    Forgot the password later? Retrieve it with:"
echo "    sudo nmcli -s -g 802-11-wireless-security.psk connection show spots-ap"

[ "$setup_ok" -eq 1 ] || exit 1
