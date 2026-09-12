# Shared paths and helpers. Sourced, not executed.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDIN_NAME="FusionFDMAgent"
ADDIN_SRC="$REPO_ROOT/addin/$ADDIN_NAME"
FUSION_API_DIR="$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API"
FUSION_ADDINS_DIR="$FUSION_API_DIR/AddIns"
ADDIN_LINK="$FUSION_ADDINS_DIR/$ADDIN_NAME"

HOME_DIR="$HOME/.fusion-fdm-agent"
VENV_DIR="$HOME_DIR/venv"
VENV_PY="$VENV_DIR/bin/python"
WORKSPACE_DIR="$HOME_DIR/workspace"
LOG_DIR="$HOME_DIR/logs"

if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_BAD=$'\033[31m'; C_WARN=$'\033[33m'; C_OFF=$'\033[0m'
else
  C_OK=''; C_BAD=''; C_WARN=''; C_OFF=''
fi

ok()   { printf '  %sok%s    %s\n' "$C_OK" "$C_OFF" "$1"; }
bad()  { printf '  %sFAIL%s  %s\n' "$C_BAD" "$C_OFF" "$1"; }
warn() { printf '  %swarn%s  %s\n' "$C_WARN" "$C_OFF" "$1"; }
