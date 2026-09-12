#!/usr/bin/env bash
# Remove the installed add-in. Leaves ~/.fusion-fdm-agent alone.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [ -L "$ADDIN_LINK" ]; then
  rm "$ADDIN_LINK"
  ok "removed symlink $ADDIN_LINK"
elif [ -d "$ADDIN_LINK" ]; then
  # Guard against pointing this at the wrong directory.
  if [ ! -f "$ADDIN_LINK/$ADDIN_NAME.manifest" ]; then
    bad "$ADDIN_LINK does not look like our add-in; refusing to delete it."
    exit 1
  fi
  rm -rf "$ADDIN_LINK"
  ok "removed $ADDIN_LINK"
else
  ok "nothing to remove"
fi

echo
echo "If Fusion still lists the add-in, run ./scripts/fix-duplicate-registration.sh"
echo "with Fusion closed. Sidecar venv and logs stay at $HOME_DIR."
