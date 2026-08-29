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
# Env vars:
#   SPOTS_DIR   Where to install (default: $HOME/spots). Ignored if this
#               script is already running from inside a cloned repo.
#   REPO_URL    Git remote to clone (default: the S.P.O.T.S GitHub repo).
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
  $SUDO apt-get install -y --no-install-recommends \
    git python3-venv python3-pip libatlas-base-dev libopenjp2-7 libtiff6
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
  echo "    to \"zcam\" and camera.ip to your Z CAM's IP address."
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
echo "  1. Edit $INSTALL_DIR/config.yaml for your camera/target setup."
echo "  2. Run 'spots' to start the dashboard."
echo "  3. After pulling repo updates in the future, run 'spots -update'."
