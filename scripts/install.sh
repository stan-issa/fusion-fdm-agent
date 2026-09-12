#!/usr/bin/env bash
# Copy the add-in into Fusion's AddIns directory.
#
# Deliberately a copy, not a symlink. Fusion canonicalizes a symlinked add-in
# folder and registers the *resolved* path as a second add-in, so a symlinked
# install shows up twice in Utilities > Add-Ins and, after a restart, runs twice
# -- two instances fighting over the same panel and palette IDs.
#
# Re-run this after editing anything under addin/ to push the changes, then
# Stop and Run the add-in in Fusion.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [ ! -d "$FUSION_API_DIR" ]; then
  bad "Fusion API directory not found at:"
  echo "      $FUSION_API_DIR"
  echo "    Is Autodesk Fusion installed for this user?"
  exit 1
fi

mkdir -p "$FUSION_ADDINS_DIR"

first_install=1
if [ -L "$ADDIN_LINK" ]; then
  # An install from before this script copied instead of linking.
  rm "$ADDIN_LINK"
  warn "removed old symlink install (it caused duplicate registration)"
  echo "        Run ./scripts/fix-duplicate-registration.sh with Fusion closed"
  echo "        to drop the stale second entry Fusion recorded."
elif [ -d "$ADDIN_LINK" ]; then
  first_install=0
fi

rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "$ADDIN_SRC/" "$ADDIN_LINK/"

if [ "$first_install" -eq 1 ]; then
  ok "installed  $ADDIN_LINK"
  echo
  echo "In Fusion: Utilities > Add-Ins (Shift+S) > $ADDIN_NAME > Run."
else
  ok "synced     $ADDIN_LINK"
  echo
  echo "In Fusion: Utilities > Add-Ins > $ADDIN_NAME > Stop, then Run."
fi
