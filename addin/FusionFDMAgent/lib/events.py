"""Marshalling from background threads onto Fusion's main thread.

Fusion API objects may only be touched on the main thread. ``fireCustomEvent``
is the sanctioned way to get there, but firing it once per streamed token is
unreliable -- Fusion coalesces rapid custom events, so deltas get dropped.

The pump therefore separates payload from signal: producers push items onto a
queue and fire a contentless wake-up event, and the handler drains the entire
queue on each wake-up. A coalesced wake-up then costs nothing, because the next
drain picks up everything that accumulated.
"""

import queue
import traceback

import adsk.core

from .logging_util import get_logger


class _PumpHandler(adsk.core.CustomEventHandler):
    """Drains the pump's queue on the main thread."""

    def __init__(self, pump):
        super().__init__()
        self._pump = pump

    def notify(self, args):
        try:
            self._pump.drain()
        except Exception:
            get_logger().error("pump drain failed\n%s", traceback.format_exc())


class MainThreadPump:
    """Moves work items from any thread onto Fusion's main thread."""

    def __init__(self, app, event_id):
        self._app = app
        self._event_id = event_id
        self._queue = queue.Queue()
        self._event = None
        self._handler = None
        self._consumer = None
        self._running = False

    def start(self, consumer):
        """Register the custom event and route drained items to ``consumer``."""
        self._consumer = consumer

        # A previous run that failed to tear down cleanly leaves the event
        # registered, and re-registering the same ID then fails.
        try:
            self._app.unregisterCustomEvent(self._event_id)
        except Exception:
            pass

        self._event = self._app.registerCustomEvent(self._event_id)
        self._handler = _PumpHandler(self)
        self._event.add(self._handler)
        self._running = True

    def post(self, item):
        """Queue an item from any thread and ask the main thread to drain."""
        if not self._running:
            return
        self._queue.put(item)
        try:
            self._app.fireCustomEvent(self._event_id, "")
        except Exception:
            # Fusion shutting down mid-stream; the item is simply dropped.
            pass

    def drain(self):
        """Hand every queued item to the consumer. Main thread only."""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if self._consumer is None:
                continue
            try:
                self._consumer(item)
            except Exception:
                get_logger().error(
                    "pump consumer failed for %r\n%s", item, traceback.format_exc()
                )

    def stop(self):
        """Unregister the event and discard anything still queued."""
        self._running = False
        if self._event is not None and self._handler is not None:
            try:
                self._event.remove(self._handler)
            except Exception:
                pass
        try:
            self._app.unregisterCustomEvent(self._event_id)
        except Exception:
            pass
        self._event = None
        self._handler = None
        self._consumer = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
