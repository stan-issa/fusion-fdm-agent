#!/usr/bin/env bash
# Run the round-trip test. Needs ./scripts/bootstrap.sh to have run first.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [ ! -x "$VENV_PY" ]; then
  bad "Sidecar venv missing. Run ./scripts/bootstrap.sh first."
  exit 1
fi

# The add-in half runs under the system interpreter (it is stdlib-only); the
# sidecar it spawns uses the venv, exactly as it does inside Fusion.
exec "${PYTHON_BIN:-python3}" "$REPO_ROOT/tests/test_roundtrip.py"
