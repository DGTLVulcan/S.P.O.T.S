#!/usr/bin/env bash
# S.P.O.T.S installer -- run on a fresh Raspberry Pi (Raspberry Pi OS /
# Debian-based). Pulls the app from git, installs OS + Python dependencies,
# and installs a `spots` command to start it.
#
# Usage (fresh Pi, no repo cloned yet):
#   curl -fsSL https://raw.githubusercontent.com/DGTLVulcan/S.P.O.T.S/main/scripts/install.sh | bash
#
# Usage (repo already cloned):
#   ./scripts/install.sh
#
# Also sets the Pi up as its own network (WiFi access point for phones/
# tablets + DHCP for the Z CAM on Ethernet, see scripts/setup-network.sh)
# and installs a systemd service so S.P.O.T.S starts automatically on boot.
#
# WARNING: if you're running this over SSH via WiFi, the network setup step
# puts wlan0 into access-point mode and will drop that connection. Run this
# from the console, over Ethernet, or set SPOTS_SKIP_NETWORK=1 and run
# scripts/setup-network.sh yourself later from the console.
#
# Env vars:
#   SPOTS_DIR            Where to install (default: $HOME/spots). Ignored if
#                        this script is already running from inside a clone.
#   REPO_URL             Git remote to clone (default: the S.P.O.T.S GitHub repo).
#   SPOTS_SKIP_NETWORK   Set to 1 to skip the WiFi AP / Ethernet DHCP setup.
#   SPOTS_SKIP_SERVICE   Set to 1 to skip installing the systemd autostart service.
#   SPOTS_AP_SSID, SPOTS_AP_PASSWORD, SPOTS_AP_IP, SPOTS_ETH_IP,
#   SPOTS_WIFI_COUNTRY   Passed through to scripts/setup-network.sh.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/DGTLVulcan/S.P.O.T.S.git}"

# If this script is running from inside an already-cloned repo (e.g. the
# user cloned it themselves and ran ./scripts/install.sh), install in place
# instead of cloning a second copy.
script_path=""
if [ -n "${BASH_SOURCE:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -n "$script_path" ] && [ -f "$script_path/../S.P.O.T.S.py" ]; then
  INSTALL_DIR="$(cd "$script_path/.." && pwd)"
else
  INSTALL_DIR="${SPOTS_DIR:-$HOME/spots}"
fi

echo "==> Installing S.P.O.T.S to $INSTALL_DIR"

if command -v apt-get >/dev/null 2>&1; then
  SUDO=""
  if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
  fi
  echo "==> Installing OS packages (git, Python venv, OpenCV runtime libs)"
  $SUDO apt-get update
  # libatlas-base-dev was ATLAS's Debian package -- ATLAS was dropped from
  # the archive in Bookworm, so numpy/OpenCV now want OpenBLAS instead.
  $SUDO apt-get install -y --no-install-recommends \
    git python3-venv python3-pip libopenblas-dev libopenjp2-7 libtiff6
else
  echo "==> apt-get not found, skipping OS package install (assuming they're already present)"
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "==> Repo already present, pulling latest"
  git -C "$INSTALL_DIR" pull --ff-only
elif [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  echo "error: $INSTALL_DIR already exists and isn't a git repo -- remove it or set SPOTS_DIR to a different path" >&2
  exit 1
else
  echo "==> Cloning $REPO_URL"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

echo "==> Setting up Python virtual environment"
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ ! -f config.yaml ]; then
  echo "==> Creating config.yaml from config.example.yaml"
  cp config.example.yaml config.yaml
  echo "    Edit $INSTALL_DIR/config.yaml before your first run: set camera.source"
  echo "    to \"zcam\" (camera.ip can stay blank -- it's auto-discovered)."
else
  echo "==> config.yaml already exists, leaving it as-is"
fi

echo "==> Installing the 'spots' command"
mkdir -p "$HOME/.local/bin"
sed \
  -e "s|__INSTALL_DIR__|$INSTALL_DIR|" \
  -e "s|__REPO_URL__|$REPO_URL|" \
  "$INSTALL_DIR/scripts/spots.sh" > "$HOME/.local/bin/spots"
chmod +x "$HOME/.local/bin/spots"

if [ "${SPOTS_SKIP_NETWORK:-0}" != "1" ]; then
  echo "==> Setting up the Pi's own network (WiFi AP + camera DHCP)"
  bash "$INSTALL_DIR/scripts/setup-network.sh"
else
  echo "==> SPOTS_SKIP_NETWORK=1, skipping WiFi AP / Ethernet DHCP setup"
fi

if [ "${SPOTS_SKIP_SERVICE:-0}" != "1" ] && command -v systemctl >/dev/null 2>&1; then
  echo "==> Installing systemd service so S.P.O.T.S starts on boot"
  SUDO=""
  if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
  fi
  sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|" \
    -e "s|__USER__|$(id -un)|" \
    "$INSTALL_DIR/scripts/spots.service" | $SUDO tee /etc/systemd/system/spots.service >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable spots.service
  $SUDO systemctl restart spots.service
else
  echo "==> Skipping systemd service install"
fi

echo
echo "==> Done."
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    echo "NOTE: $HOME/.local/bin isn't on your PATH yet. Add this to ~/.bashrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "then restart your shell (or run: source ~/.bashrc)"
    ;;
esac
echo
echo "Next steps:"
echo "  1. Edit $INSTALL_DIR/config.yaml if you need non-default camera/target settings."
echo "  2. S.P.O.T.S is now running as a service and will start automatically on"
echo "     every boot -- join the WiFi network printed above and browse to the"
echo "     dashboard. Use 'journalctl -u spots -f' to view its logs."
echo "  3. After pulling repo updates in the future, run 'spots -update'."
