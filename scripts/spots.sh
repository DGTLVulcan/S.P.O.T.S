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

if [ "$update" -eq 1 ]; then
  echo "==> Pulling latest changes from $REPO_URL"
  git pull --ff-only
  echo "==> Updating Python dependencies"
  source .venv/bin/activate
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  echo "==> Update complete"
else
  source .venv/bin/activate
fi

exec python S.P.O.T.S.py
