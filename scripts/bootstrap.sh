#!/usr/bin/env bash
# Create the sidecar's virtualenv and user-level directories.
#
# The sidecar needs pip-installed packages, which cannot go into Fusion's
# embedded interpreter -- hence a venv of its own, outside the repo.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "Bootstrapping the FDM Agent sidecar"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  bad "$PYTHON_BIN not found. Set PYTHON_BIN to a Python 3.10+ interpreter."
  exit 1
fi

version="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  bad "Python $version is too old; the sidecar needs 3.10 or newer."
  exit 1
}
ok "interpreter  $PYTHON_BIN (Python $version)"

mkdir -p "$WORKSPACE_DIR" "$LOG_DIR"
ok "directories  $HOME_DIR"

if [ ! -x "$VENV_PY" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
ok "venv         $VENV_DIR"

"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet --editable "$REPO_ROOT/sidecar"
ok "sidecar      installed (editable)"

# Optional so a network failure here still leaves a working echo backend.
if "$VENV_PY" -m pip install --quiet "claude-agent-sdk>=0.1.0"; then
  sdk_version="$("$VENV_PY" -c 'import importlib.metadata as m; print(m.version("claude-agent-sdk"))' 2>/dev/null || echo unknown)"
  ok "agent SDK    claude-agent-sdk $sdk_version"
else
  warn "claude-agent-sdk could not be installed; the echo backend still works."
fi

echo
echo "Next: ./scripts/install.sh"
