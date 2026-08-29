#!/usr/bin/env bash
# Launcher installed by scripts/install.sh -- do not run this copy directly,
# run the installed one (`spots`) so INSTALL_DIR below is filled in.
#
# install.sh (and -update's self-refresh below) fill in the two lines below
# by matching on "^INSTALL_DIR=" / "^REPO_URL=" (line-anchored), not on the
# placeholder text itself -- a magic-token substitution would corrupt the
# -update block's own embedded sed command below, which necessarily
# contains that same token as a literal string.
set -euo pipefail

INSTALL_DIR="/path/set/by/install.sh"
REPO_URL="set/by/install.sh"

cd "$INSTALL_DIR"

update=0
init_network=0
init_service=0
for arg in "$@"; do
  case "$arg" in
    -update|--update)
      update=1
      ;;
    -initnetwork|--initnetwork)
      init_network=1
      ;;
    -initservice|--initservice)
      init_service=1
      ;;
    *)
      echo "spots: unknown argument '$arg'" >&2
      echo "usage: spots [-update] [-initnetwork] [-initservice]" >&2
      echo "  -update       pull latest changes and reinstall dependencies" >&2
      echo "  -initnetwork  force (re)run the WiFi AP / camera DHCP setup" >&2
      echo "  -initservice  force (re)install the systemd autostart service" >&2
      exit 1
      ;;
  esac
done

has_service() {
  command -v systemctl >/dev/null 2>&1 \
    && [ -n "$(systemctl list-unit-files spots.service --no-legend 2>/dev/null)" ]
}

if [ "$update" -eq 1 ]; then
  echo "==> Pulling latest changes from $REPO_URL"
  git pull --ff-only
  echo "==> Updating Python dependencies"
  source .venv/bin/activate
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt

  # Refresh the installed launcher itself so new flags/fixes here take
  # effect immediately, without disturbing this already-running copy --
  # write to a temp file and rename over it (atomic) rather than truncating
  # the file this script is currently executing from.
  echo "==> Refreshing the installed 'spots' command"
  launcher="$(command -v spots || echo "$HOME/.local/bin/spots")"
  sed \
    -e "s|^INSTALL_DIR=.*|INSTALL_DIR=\"$INSTALL_DIR\"|" \
    -e "s|^REPO_URL=.*|REPO_URL=\"$REPO_URL\"|" \
    "$INSTALL_DIR/scripts/spots.sh" > "${launcher}.new"
  chmod +x "${launcher}.new"
  mv "${launcher}.new" "$launcher"

  echo "==> Update complete"
fi

if [ "$init_network" -eq 1 ]; then
  echo "==> Forcing network setup (WiFi AP + camera DHCP)"
  sudo bash "$INSTALL_DIR/scripts/setup-network.sh"
fi

if [ "$init_service" -eq 1 ]; then
  echo "==> (Re)installing the systemd autostart service"
  sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__USER__|$(id -un)|g" \
    "$INSTALL_DIR/scripts/spots.service" | sudo tee /etc/systemd/system/spots.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable spots.service
  sudo systemctl restart spots.service
  echo "==> spots.service installed and started -- it will now also start on boot"
fi

if [ "$init_network" -eq 1 ] || [ "$init_service" -eq 1 ]; then
  exit 0
fi

if [ "$update" -eq 1 ]; then
  if has_service; then
    echo "==> Restarting spots.service"
    sudo systemctl restart spots.service
    exit 0
  fi
  # No service installed (manual/dev setup) -- fall through and start it
  # directly, same as plain `spots`.
else
  source .venv/bin/activate
fi

if has_service && systemctl is-active --quiet spots.service; then
  echo "spots.service is already running -- the dashboard is up."
  echo "  View logs:          journalctl -u spots -f"
  echo "  Run in foreground:  sudo systemctl stop spots && spots"
  echo "  Not actually up?    spots -initservice"
  exit 0
fi

exec python S.P.O.T.S.py
