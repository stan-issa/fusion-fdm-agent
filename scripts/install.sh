#!/usr/bin/env bash
# Symlink the add-in into Fusion's AddIns directory.
#
# A symlink rather than a copy: Fusion follows it, so editing files in the repo
# is the whole dev loop -- stop and re-run the add-in in Fusion to pick changes up.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [ ! -d "$FUSION_API_DIR" ]; then
  bad "Fusion API directory not found at:"
  echo "      $FUSION_API_DIR"
  echo "    Is Autodesk Fusion installed for this user?"
  exit 1
fi

mkdir -p "$FUSION_ADDINS_DIR"

if [ -e "$ADDIN_LINK" ] || [ -L "$ADDIN_LINK" ]; then
  if [ -L "$ADDIN_LINK" ] && [ "$(readlink "$ADDIN_LINK")" = "$ADDIN_SRC" ]; then
    ok "already linked"
    exit 0
  fi
  if [ -L "$ADDIN_LINK" ]; then
    rm "$ADDIN_LINK"
  else
    bad "$ADDIN_LINK exists and is not a symlink. Move it aside and retry."
    exit 1
  fi
fi

ln -s "$ADDIN_SRC" "$ADDIN_LINK"
ok "linked  $ADDIN_LINK -> $ADDIN_SRC"

echo
echo "In Fusion: Utilities > Add-Ins (Shift+S) > $ADDIN_NAME > Run."
