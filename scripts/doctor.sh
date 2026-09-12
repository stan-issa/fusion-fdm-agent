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
  target="$(readlink "$ADDIN_LINK")"
  if [ "$target" = "$ADDIN_SRC" ]; then
    ok "link         $ADDIN_LINK -> $target"
  else
    warn "link         points elsewhere: $target"
  fi
elif [ -e "$ADDIN_LINK" ]; then
  warn "link         $ADDIN_LINK exists but is not a symlink"
else
  bad "link         not installed. Run ./scripts/install.sh"; note_fail
fi

if [ -f "$ADDIN_SRC/$ADDIN_NAME.manifest" ]; then
  ok "manifest     present"
else
  bad "manifest     missing at $ADDIN_SRC/$ADDIN_NAME.manifest"; note_fail
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
