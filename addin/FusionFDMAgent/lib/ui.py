"""Creates and tears down every Fusion UI object the add-in owns.

Teardown is the hard half. Fusion keeps panels, command definitions and
palettes alive across add-in stops, so anything left behind makes the next
start fail on a duplicate ID. ``stop`` therefore removes everything it can and
never raises, and ``start`` also clears any leftovers before creating its own.
"""

import pathlib
import traceback

import adsk.core

from .. import config
from .bridge import ChatBridge
from .events import MainThreadPump
from .logging_util import get_logger


class _CommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, addin_ui):
        super().__init__()
        self._ui = addin_ui

    def notify(self, args):
        try:
            self._ui.show_palette()
        except Exception:
            get_logger().error("show_palette failed\n%s", traceback.format_exc())


class _CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, addin_ui):
        super().__init__()
        self._ui = addin_ui

    def notify(self, args):
        try:
            handler = _CommandExecuteHandler(self._ui)
            args.command.execute.add(handler)
            # Handlers are only weakly held by Fusion; a local would be garbage
            # collected before the event fires and the button would do nothing.
            self._ui.retain(handler)
            # Nothing to configure, so skip the command dialog entirely.
            args.command.isRepeatable = False
            args.command.isExecutedWhenPreEmpted = False
        except Exception:
            get_logger().error("commandCreated failed\n%s", traceback.format_exc())


class AddInUI:
    """Owns the toolbar panel, its button, and the chat palette."""

    def __init__(self, app, ui):
        self._app = app
        self._ui = ui
        self._log = get_logger()
        self._handlers = []
        self._command_definition = None
        self._panel = None
        self._control = None
        self._palette = None
        self._bridge = None
        self._pump = MainThreadPump(app, config.PUMP_EVENT_ID)

    def retain(self, handler):
        """Keep a strong reference to an event handler for the add-in's life."""
        self._handlers.append(handler)

    # -- startup -----------------------------------------------------------

    def start(self):
        self._remove_existing_ui()

        self._command_definition = self._ui.commandDefinitions.addButtonDefinition(
            config.COMMAND_ID,
            config.COMMAND_NAME,
            config.COMMAND_TOOLTIP,
            config.BUTTON_ICONS_DIR,
        )
        created = _CommandCreatedHandler(self)
        self._command_definition.commandCreated.add(created)
        self.retain(created)

        workspace = self._ui.workspaces.itemById(config.WORKSPACE_ID)
        if workspace is None:
            raise RuntimeError("Workspace {} not found".format(config.WORKSPACE_ID))

        self._panel = workspace.toolbarPanels.add(
            config.PANEL_ID, config.PANEL_NAME, config.PANEL_POSITION_ID, False
        )
        self._control = self._panel.controls.addCommand(self._command_definition)
        self._control.isPromoted = True

        self._pump.start(self._on_pumped)
        self._log.info("UI started")

    # -- palette -----------------------------------------------------------

    def show_palette(self):
        """Create the palette on first use, then just re-show it."""
        if self._palette is None:
            existing = self._ui.palettes.itemById(config.PALETTE_ID)
            if existing is not None:
                # Left over from a previous run; its HTML handler is gone, so
                # a fresh one is safer than adopting it.
                existing.deleteMe()

            # Created hidden so the bridge is listening before the page can
            # load and send its opening "ready" -- a message sent before the
            # handler is attached is simply lost, and the palette then waits
            # forever for a reply to a question nobody heard.
            self._palette = self._ui.palettes.add2(
                config.PALETTE_ID,
                config.PALETTE_NAME,
                _file_url(config.PALETTE_HTML),
                False,  # isVisible
                True,   # showCloseButton
                True,   # isResizable
                config.PALETTE_WIDTH,
                config.PALETTE_HEIGHT,
            )
            self._palette.dockingState = (
                adsk.core.PaletteDockingStates.PaletteDockStateRight
            )
            self._bridge = ChatBridge(self._palette, self._pump)
            self._palette.isVisible = True
            self._log.info("palette created")
        else:
            self._palette.isVisible = True

    def _on_pumped(self, message):
        """Main thread. Every off-thread message lands here."""
        if self._bridge is not None:
            self._bridge.deliver(message)

    # -- teardown ----------------------------------------------------------

    def stop(self):
        """Remove everything. Safe to call twice, and never raises."""
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                self._log.error("bridge stop failed\n%s", traceback.format_exc())
            self._bridge = None

        try:
            self._pump.stop()
        except Exception:
            self._log.error("pump stop failed\n%s", traceback.format_exc())

        if self._palette is not None:
            try:
                self._palette.deleteMe()
            except Exception:
                pass
            self._palette = None

        self._remove_existing_ui()
        self._handlers = []
        self._command_definition = None
        self._panel = None
        self._control = None
        self._log.info("UI stopped")

    def _remove_existing_ui(self):
        """Delete our toolbar objects if they exist, from this run or a prior one."""
        workspace = self._ui.workspaces.itemById(config.WORKSPACE_ID)
        if workspace is not None:
            panel = workspace.toolbarPanels.itemById(config.PANEL_ID)
            if panel is not None:
                control = panel.controls.itemById(config.COMMAND_ID)
                if control is not None:
                    _safe_delete(control)
                _safe_delete(panel)

        definition = self._ui.commandDefinitions.itemById(config.COMMAND_ID)
        if definition is not None:
            _safe_delete(definition)


def _safe_delete(item):
    try:
        item.deleteMe()
    except Exception:
        get_logger().debug("deleteMe failed\n%s", traceback.format_exc())


def _file_url(path):
    """Fusion wants a URL for palette content, not a filesystem path.

    Percent-encoding is not optional here. The add-in is normally loaded through
    a symlink under "Application Support", so the path it reports for itself
    contains spaces, and a naively concatenated file:// URL fails to load.
    """
    return pathlib.Path(path).as_uri()
