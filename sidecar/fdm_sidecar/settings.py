"""Reads the user settings file shared with the add-in.

The add-in owns ``~/.fusion-fdm-agent/settings.json`` and passes its path in
``FDM_AGENT_SETTINGS``. Settings are always a convenience here: a missing or
malformed file falls back to defaults rather than stopping the sidecar.
"""

import json
import os
from typing import Any

DEFAULTS: dict[str, Any] = {
    # Model id, or None to use whatever the Claude Code install defaults to.
    "model": None,
    # Tools the agent may use inside the workspace. Bash is off by default: the
    # workspace confines the agent's *cwd*, not what a shell command can reach.
    "allowBash": False,
    # Appended to the built-in system prompt.
    "systemPromptExtra": "",
    # Skip the approval prompt for tools that change the design. Off by
    # default: run_fusion_script executes arbitrary code against the open
    # document.
    "autoApprove": False,
}


def _settings_path() -> str:
    configured = os.environ.get("FDM_AGENT_SETTINGS")
    if configured:
        return configured
    return os.path.expanduser("~/.fusion-fdm-agent/settings.json")


def backend_settings(name: str) -> dict[str, Any]:
    """Return the settings block for one backend, merged over the defaults."""
    settings = dict(DEFAULTS)
    try:
        with open(_settings_path(), encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return settings

    block = stored.get(name) if isinstance(stored, dict) else None
    if isinstance(block, dict):
        settings.update(block)
    return settings
