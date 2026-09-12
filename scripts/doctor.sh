#!/usr/bin/env bash
# Report everything the add-in needs, so setup failures are diagnosable
# without digging through Fusion's own log.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

status=0
note_fail() { status=1; }

echo "Fusion"
if [ -d "$FUSION_API_DIR" ]; then
  api_version="$(cat "$FUSION_API_DIR/version.txt" 2>/dev/null || echo unknown)"
  ok "API dir      $FUSION_API_DIR (version $api_version)"
else
  bad "API dir      not found at $FUSION_API_DIR"; note_fail
fi

fusion_app="$(ls -d "$HOME/Library/Application Support/Autodesk/webdeploy/production"/*/Autodesk*.app 2>/dev/null | head -n1)"
if [ -n "$fusion_app" ]; then
  embedded="$(ls "$fusion_app/Contents/Frameworks/Python.framework/Versions" 2>/dev/null | grep -v Current | head -n1)"
  ok "application  $(basename "$fusion_app") (embedded Python ${embedded:-unknown})"
else
  warn "application  not found under webdeploy/production"
fi

echo
echo "Add-in"
if [ -L "$ADDIN_LINK" ]; then
  # Fusion resolves a symlinked add-in and registers the target as a *second*
  # add-in, which then runs twice. Never install this way.
  bad "install      $ADDIN_LINK is a symlink; Fusion will register it twice"
  echo "               Run ./scripts/install.sh to replace it with a copy, then"
  echo "               ./scripts/fix-duplicate-registration.sh with Fusion closed."
  note_fail
elif [ -d "$ADDIN_LINK" ]; then
  if [ -f "$ADDIN_LINK/$ADDIN_NAME.manifest" ]; then
    if diff -rq --exclude=__pycache__ "$ADDIN_SRC" "$ADDIN_LINK" >/dev/null 2>&1; then
      ok "install      $ADDIN_LINK (up to date)"
    else
      warn "install      $ADDIN_LINK differs from the repo. Run ./scripts/install.sh"
    fi
  else
    bad "install      $ADDIN_LINK has no manifest"; note_fail
  fi
elif [ -e "$ADDIN_LINK" ]; then
  bad "install      $ADDIN_LINK exists but is not a directory"; note_fail
else
  bad "install      not installed. Run ./scripts/install.sh"; note_fail
fi

if [ -f "$ADDIN_SRC/$ADDIN_NAME.manifest" ]; then
  ok "manifest     present in repo"
else
  bad "manifest     missing at $ADDIN_SRC/$ADDIN_NAME.manifest"; note_fail
fi

# Fusion remembers every add-in path it has ever seen. More than one entry for
# us means a stale registration that will auto-run a second instance.
registry_report="$(
  ADDIN_NAME="$ADDIN_NAME" ADDIN_LINK="$ADDIN_LINK" python3 - <<'PY' 2>/dev/null
import glob, json, os

name = os.environ["ADDIN_NAME"]
keep = os.environ["ADDIN_LINK"] + os.sep
base = os.path.expanduser("~/Library/Application Support/Autodesk/Autodesk Fusion 360")
stale, total = [], 0
for path in glob.glob(os.path.join(base, "*", "JSLoadedScriptsinfo")):
    try:
        with open(path, encoding="utf-8") as handle:
            entries = json.load(handle).get("loadedScripts", [])
    except (OSError, ValueError):
        continue
    for entry in entries:
        if entry.get("name") != name:
            continue
        total += 1
        if not entry.get("path", "").startswith(keep):
            stale.append(entry["path"])
print(total)
for path in stale:
    print(path)
PY
)"
registry_total="$(printf '%s\n' "$registry_report" | head -n1)"
registry_stale="$(printf '%s\n' "$registry_report" | tail -n +2 | sed '/^$/d')"
if [ -z "$registry_total" ]; then
  warn "registry     could not be read"
elif [ -n "$registry_stale" ]; then
  bad "registry     $registry_total entries for $ADDIN_NAME; stale:"
  printf '                 %s\n' $registry_stale
  echo "               Quit Fusion, then ./scripts/fix-duplicate-registration.sh"
  note_fail
elif [ "$registry_total" = "0" ]; then
  warn "registry     no entry yet (Fusion has not loaded the add-in)"
else
  ok "registry     1 entry, no duplicates"
fi

echo
echo "Sidecar"
if [ -x "$VENV_PY" ]; then
  venv_version="$("$VENV_PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo unknown)"
  ok "venv         $VENV_DIR (Python $venv_version)"

  if "$VENV_PY" -c 'import fdm_sidecar' 2>/dev/null; then
    ok "package      fdm_sidecar importable"
  else
    bad "package      fdm_sidecar not importable. Run ./scripts/bootstrap.sh"; note_fail
  fi

  sdk="$("$VENV_PY" -c 'import importlib.metadata as m; print(m.version("claude-agent-sdk"))' 2>/dev/null)"
  if [ -n "$sdk" ]; then
    ok "agent SDK    claude-agent-sdk $sdk"
  else
    warn "agent SDK    claude-agent-sdk not installed (echo backend still works)"
  fi
else
  bad "venv         missing. Run ./scripts/bootstrap.sh"; note_fail
fi

[ -d "$WORKSPACE_DIR" ] && ok "workspace    $WORKSPACE_DIR" || warn "workspace    missing: $WORKSPACE_DIR"

echo
echo "Agent CLIs"
for cli in claude codex; do
  path="$(command -v "$cli" 2>/dev/null || true)"
  if [ -z "$path" ] && [ -x "$HOME/.local/bin/$cli" ]; then
    path="$HOME/.local/bin/$cli"
  fi
  if [ -n "$path" ]; then
    version="$("$path" --version 2>/dev/null | head -n1 || echo unknown)"
    ok "$(printf '%-12s' "$cli") $version  ($path)"
  else
    warn "$(printf '%-12s' "$cli") not found"
  fi
done

echo
echo "Logs"
for f in "$LOG_DIR/addin.log" "$LOG_DIR/sidecar.log"; do
  if [ -f "$f" ]; then
    ok "$(printf '%-12s' "$(basename "$f")") $(wc -l < "$f" | tr -d ' ') lines"
  else
    warn "$(printf '%-12s' "$(basename "$f")") not yet written"
  fi
done

echo
if [ "$status" -eq 0 ]; then
  printf '%sReady.%s\n' "$C_OK" "$C_OFF"
else
  printf '%sNot ready -- see FAIL lines above.%s\n' "$C_BAD" "$C_OFF"
fi
exit "$status"
