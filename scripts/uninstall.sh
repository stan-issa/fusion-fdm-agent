#!/usr/bin/env bash
# Remove the add-in symlink. Leaves ~/.fusion-fdm-agent alone.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [ -L "$ADDIN_LINK" ]; then
  rm "$ADDIN_LINK"
  ok "removed $ADDIN_LINK"
elif [ -e "$ADDIN_LINK" ]; then
  bad "$ADDIN_LINK is not a symlink; refusing to delete it."
  exit 1
else
  ok "nothing to remove"
fi

echo
echo "Sidecar venv and logs kept at $HOME_DIR (delete manually if you want them gone)."
