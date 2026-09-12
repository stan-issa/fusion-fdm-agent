"""Identifiers, filesystem paths and persisted settings for the add-in.

Every Fusion object we create is keyed by one of the IDs below. They must be
globally unique within Fusion, hence the prefix, and they must stay stable --
changing one orphans the object it names in any Fusion install that already ran
a previous version.
"""

import json
import os

PREFIX = "fdmAgent"

ADDIN_NAME = "Fusion FDM Agent"

# Fusion object IDs.
PANEL_ID = PREFIX + "_Panel"
PANEL_NAME = "FDM Agent"
COMMAND_ID = PREFIX + "_ShowChat"
COMMAND_NAME = "FDM Agent"
COMMAND_TOOLTIP = "Open the FDM Agent chat panel"
PALETTE_ID = PREFIX + "_ChatPalette"
PALETTE_NAME = "FDM Agent"

# Workspace whose toolbar receives our custom panel.
WORKSPACE_ID = "FusionSolidEnvironment"
# Our panel is inserted before this stock panel so it lands in a predictable spot.
PANEL_POSITION_ID = "SolidScriptsAddinsPanel"

# Custom event used to hop from background threads onto Fusion's main thread.
PUMP_EVENT_ID = PREFIX + "_Pump"

PALETTE_WIDTH = 420
PALETTE_HEIGHT = 640

# Repo-relative locations.
ADDIN_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCES_DIR = os.path.join(ADDIN_DIR, "resources")
PALETTE_DIR = os.path.join(RESOURCES_DIR, "palette")
PALETTE_HTML = os.path.join(PALETTE_DIR, "index.html")
BUTTON_ICONS_DIR = os.path.join(RESOURCES_DIR, "chat")
REPO_ROOT = os.path.dirname(os.path.dirname(ADDIN_DIR))

# User-level state, deliberately outside the repo: the add-in directory is a
# symlink into a git working tree, so nothing mutable may live there.
HOME_DIR = os.path.join(os.path.expanduser("~"), ".fusion-fdm-agent")
VENV_PYTHON = os.path.join(HOME_DIR, "venv", "bin", "python")
WORKSPACE_DIR = os.path.join(HOME_DIR, "workspace")
LOG_DIR = os.path.join(HOME_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "addin.log")
SIDECAR_LOG_FILE = os.path.join(LOG_DIR, "sidecar.log")
SETTINGS_FILE = os.path.join(HOME_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "backend": "echo",
}


def load_settings():
    """Return persisted settings, falling back to defaults on any problem.

    Settings are a convenience, never a dependency: a corrupt or unreadable file
    must not stop the add-in from loading.
    """
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
        if isinstance(stored, dict):
            settings.update(stored)
    except (OSError, ValueError):
        pass
    return settings


def save_settings(settings):
    """Persist settings, ignoring failures for the same reason as above."""
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)
    except OSError:
        pass
