#!/usr/bin/env bash
# Launcher installed by scripts/install.sh -- do not run this copy directly,
# run the installed one (`spots`) so INSTALL_DIR below is filled in.
set -euo pipefail

INSTALL_DIR="__INSTALL_DIR__"
REPO_URL="__REPO_URL__"

cd "$INSTALL_DIR"

update=0
for arg in "$@"; do
  case "$arg" in
    -update|--update)
      update=1
      ;;
    *)
      echo "spots: unknown argument '$arg'" >&2
      echo "usage: spots [-update]" >&2
      exit 1
      ;;
  esac
done

has_service=0
if command -v systemctl >/dev/null 2>&1 \
  && [ -n "$(systemctl list-unit-files spots.service --no-legend 2>/dev/null)" ]; then
  has_service=1
fi

if [ "$update" -eq 1 ]; then
  echo "==> Pulling latest changes from $REPO_URL"
  git pull --ff-only
  echo "==> Updating Python dependencies"
  source .venv/bin/activate
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  echo "==> Update complete"

  if [ "$has_service" -eq 1 ]; then
    echo "==> Restarting spots.service"
    sudo systemctl restart spots.service
    exit 0
  fi
  # No service installed (manual/dev setup) -- fall through and start it
  # directly, same as plain `spots`.
else
  source .venv/bin/activate
fi

if [ "$has_service" -eq 1 ] && systemctl is-active --quiet spots.service; then
  echo "spots.service is already running -- the dashboard is up."
  echo "  View logs:        journalctl -u spots -f"
  echo "  Run in foreground: sudo systemctl stop spots && spots"
  exit 0
fi

exec python S.P.O.T.S.py
