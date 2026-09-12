"""Remembers what the user last selected in Fusion.

Reading ``ui.activeSelections`` when a tool runs does not work, and the reason
is easy to miss: clicking into the palette to type moves focus, and Fusion
clears the selection on the way. By the time "chamfer this face" reaches a
tool, the face the user meant is no longer selected.

So the selection is captured as it happens. A handler on
``activeSelectionChanged`` keeps the last *non-empty* selection, and tools read
that instead. Emptying the selection is deliberately not recorded: it is
almost always the side effect of clicking somewhere else, not an instruction.

Main thread only -- Fusion fires the event there, and that is also the only
place the cached entities may be touched.
"""

import time

import adsk.core

from .logging_util import get_logger

# Past this, a remembered selection is reported with its age so the caller can
# decide. It is never silently discarded: a stale answer the user can see is
# better than a confident "nothing is selected".
STALE_AFTER_SECONDS = 300


class _SelectionChangedHandler(adsk.core.ActiveSelectionEventHandler):
    def __init__(self, watcher):
        super().__init__()
        self._watcher = watcher

    def notify(self, args):
        try:
            self._watcher.capture()
        except Exception:
            get_logger().debug("selection capture failed", exc_info=True)


class SelectionWatcher:
    """Keeps the most recent non-empty Fusion selection."""

    def __init__(self, ui):
        self._ui = ui
        self._handler = None
        self._entities = []
        self._captured_at = None

    def start(self):
        if self._handler is not None:
            return
        self._handler = _SelectionChangedHandler(self)
        try:
            self._ui.activeSelectionChanged.add(self._handler)
        except Exception:
            get_logger().error("could not watch selection changes", exc_info=True)
            self._handler = None
            return
        # Whatever is selected right now counts, so the watcher is useful
        # immediately rather than only after the next click.
        self.capture()

    def stop(self):
        if self._handler is None:
            return
        try:
            self._ui.activeSelectionChanged.remove(self._handler)
        except Exception:
            pass
        self._handler = None
        self._entities = []
        self._captured_at = None

    def capture(self):
        """Record the current selection, if there is one. Main thread only."""
        selections = self._ui.activeSelections
        if selections.count == 0:
            return
        entities = []
        for index in range(selections.count):
            try:
                entities.append(selections.item(index).entity)
            except Exception:
                continue
        if entities:
            self._entities = entities
            self._captured_at = time.monotonic()

    def entities(self):
        """The remembered selection, dropping anything no longer valid."""
        live = []
        for entity in self._entities:
            try:
                if entity.isValid:
                    live.append(entity)
            except Exception:
                continue
        if len(live) != len(self._entities):
            self._entities = live
        return list(live)

    def age_seconds(self):
        if self._captured_at is None:
            return None
        return time.monotonic() - self._captured_at

    def is_stale(self):
        age = self.age_seconds()
        return age is not None and age > STALE_AFTER_SECONDS


# The add-in creates exactly one of these at startup; tools reach it here
# rather than threading it through every call signature.
_watcher = None


def install(ui):
    global _watcher
    uninstall()
    _watcher = SelectionWatcher(ui)
    _watcher.start()
    return _watcher


def uninstall():
    global _watcher
    if _watcher is not None:
        _watcher.stop()
        _watcher = None


def watcher():
    return _watcher
