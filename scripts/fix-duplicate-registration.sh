#!/usr/bin/env bash
# Remove stale FusionFDMAgent entries from Fusion's add-in registry.
#
# Fusion remembers every add-in path it has seen in JSLoadedScriptsinfo. An
# earlier symlink-based install made it record the resolved repo path as a
# *second* add-in, which then auto-ran alongside the real one. Installing by
# copy stops that happening again, but the stale entry is already persisted and
# has to be removed here.
#
# Fusion rewrites this file when it exits, so it must not be running.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# Match the main executable only. The bundle nests a dozen helper apps
# (ADPClientService, Autodesk Identity Manager, CER...) that keep running long
# after Fusion quits, so a substring match on "Autodesk Fusion.app" reports
# Fusion as running when it is not.
fusion_pids="$(pgrep -f "Autodesk Fusion\.app/Contents/MacOS/Autodesk Fusion" 2>/dev/null || true)"
if [ -n "$fusion_pids" ]; then
  bad "Fusion is running (pid $(echo $fusion_pids | tr '\n' ' ')). Quit it first --"
  echo "        it rewrites this file on exit and would undo the cleanup."
  exit 1
fi

base="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360"
found=0

while IFS= read -r registry; do
  [ -n "$registry" ] || continue
  found=1
  ADDIN_NAME="$ADDIN_NAME" ADDIN_LINK="$ADDIN_LINK" python3 - "$registry" <<'PY'
import json, os, shutil, sys

path = sys.argv[1]
name = os.environ["ADDIN_NAME"]
keep_prefix = os.environ["ADDIN_LINK"] + os.sep

with open(path, encoding="utf-8") as handle:
    data = json.load(handle)

scripts = data.get("loadedScripts", [])
ours = [s for s in scripts if s.get("name") == name]
stale = [s for s in ours if not s.get("path", "").startswith(keep_prefix)]

if not stale:
    print("  ok    %s: %d entry for %s, nothing to remove" % (path, len(ours), name))
    sys.exit(0)

backup = path + ".bak"
shutil.copy2(path, backup)

data["loadedScripts"] = [s for s in scripts if s not in stale]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=4)

for entry in stale:
    print("  removed  %s" % entry.get("path"))
print("  backup   %s" % backup)
PY
done < <(find "$base" -maxdepth 2 -name JSLoadedScriptsinfo 2>/dev/null)

if [ "$found" -eq 0 ]; then
  warn "no JSLoadedScriptsinfo found under $base"
fi
