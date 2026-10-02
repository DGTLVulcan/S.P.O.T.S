#!/usr/bin/env bash
# Installs ZWO's ASI camera SDK so S.P.O.T.S can use a ZWO ASI camera over
# USB. ZWO's cameras are not UVC devices: they need libASICamera2 and its
# udev rule, which ZWO distributes as one archive.
#
# Usage:
#   1. Download "ASI Camera SDK" for Linux & Mac from ZWO's developer page
#      (a file named like ASI_linux_mac_SDK_V1.xx.tar.bz2).
#   2. bash scripts/install-asi-sdk.sh ~/Downloads/ASI_linux_mac_SDK_V1.xx.tar.bz2
#
# Copies the library for this machine's architecture to /usr/local/lib,
# installs the udev rule (camera access without root, and a larger USB
# buffer), and installs libusb. Safe to re-run with a newer SDK.
set -euo pipefail

if [ $# -ne 1 ] || [ ! -f "$1" ]; then
  echo "usage: $0 <path to ASI_linux_mac_SDK_*.tar.bz2>" >&2
  exit 1
fi
ARCHIVE="$1"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

case "$(uname -m)" in
  aarch64|arm64) ARCH=armv8 ;;
  armv7l) ARCH=armv7 ;;
  armv6l) ARCH=armv6 ;;
  x86_64) ARCH=x64 ;;
  i?86) ARCH=x86 ;;
  *) echo "error: no ZWO library for architecture $(uname -m)" >&2; exit 1 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> Unpacking $ARCHIVE"
tar -xf "$ARCHIVE" -C "$WORK"

LIB="$(find "$WORK" -path "*/lib/$ARCH/libASICamera2.so*" -type f | sort | tail -n 1)"
if [ -z "$LIB" ]; then
  echo "error: no lib/$ARCH/libASICamera2.so in that archive -- is it the Linux SDK?" >&2
  exit 1
fi
RULES="$(find "$WORK" -name "asi.rules" -type f | head -n 1)"

if command -v apt-get >/dev/null 2>&1; then
  echo "==> Installing libusb"
  $SUDO apt-get install -y --no-install-recommends libusb-1.0-0
fi

echo "==> Installing $(basename "$LIB") for $ARCH"
$SUDO install -m 0644 "$LIB" /usr/local/lib/
# An unversioned name as well, so the library is found whatever the release.
$SUDO ln -sf "/usr/local/lib/$(basename "$LIB")" /usr/local/lib/libASICamera2.so
$SUDO ldconfig

if [ -n "$RULES" ]; then
  echo "==> Installing the udev rule"
  $SUDO install -m 0644 "$RULES" /etc/udev/rules.d/99-asi.rules
  $SUDO udevadm control --reload-rules
  $SUDO udevadm trigger
else
  echo "warning: no asi.rules in the archive -- the camera may need root to open" >&2
fi

echo
echo "==> Done. Unplug the camera and plug it back in so the rule applies,"
echo "    then in S.P.O.T.S set Settings > Camera > Live camera to ZWO ASI290MC"
echo "    and restart: sudo systemctl restart spots"
