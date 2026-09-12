#!/usr/bin/env bash
# Run the test suite. Needs ./scripts/bootstrap.sh to have run first.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [ ! -x "$VENV_PY" ]; then
  bad "Sidecar venv missing. Run ./scripts/bootstrap.sh first."
  exit 1
fi

SYS_PY="${PYTHON_BIN:-python3}"
status=0

run() {
  local name="$1"; shift
  echo
  echo "── $name"
  if "$@"; then :; else status=1; fi
}

# The add-in half is stdlib-only and runs under the system interpreter, the
# same split as inside Fusion. Only the approval test needs the SDK.
run "round trip (palette -> sidecar -> palette)" \
  "$SYS_PY" "$REPO_ROOT/tests/test_roundtrip.py"
run "tool path (sidecar -> loopback -> main thread)" \
  "$SYS_PY" "$REPO_ROOT/tests/test_tools.py"
run "rules (geometry, identity, parameters, tool parity)" \
  "$SYS_PY" "$REPO_ROOT/tests/test_rules.py"
run "approvals" \
  "$VENV_PY" "$REPO_ROOT/tests/test_approval.py"

echo
if [ "$status" -eq 0 ]; then
  printf '%sAll tests passed.%s\n' "$C_OK" "$C_OFF"
else
  printf '%sFailures above.%s\n' "$C_BAD" "$C_OFF"
fi
exit "$status"
