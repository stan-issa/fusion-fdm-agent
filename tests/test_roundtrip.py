"""End-to-end check of the add-in's plumbing, without launching Fusion.

Covers palette -> bridge -> sidecar subprocess -> pump -> palette. Only the
Fusion UI objects are stubbed (see ``adsk_stub/``); the sidecar is the real one,
running in the real venv.

The threading mirrors Fusion's: the sidecar's reader thread only *enqueues*, and
draining happens on the thread standing in for Fusion's main thread. That is the
part most worth protecting -- a change that made the pump drain on the producer's
thread would pass a naive test and deadlock Fusion.

Run with::

    ./scripts/test.sh
"""

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "adsk_stub"))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addin")
)

from FusionFDMAgent.lib.bridge import ChatBridge          # noqa: E402
from FusionFDMAgent.lib.events import MainThreadPump      # noqa: E402


class FakeEvent:
    def add(self, handler):
        self.handler = handler

    def remove(self, handler):
        return True


class FakeApp:
    """``fireCustomEvent`` only raises a flag; the main thread does the draining."""

    def __init__(self):
        self.woken = threading.Event()

    def registerCustomEvent(self, event_id):
        return FakeEvent()

    def unregisterCustomEvent(self, event_id):
        return True

    def fireCustomEvent(self, event_id, data=""):
        self.woken.set()
        return True


class FakePalette:
    class _Incoming:
        def add(self, handler):
            pass

        def remove(self, handler):
            return True

    def __init__(self):
        self.sent = []
        self.incomingFromHTML = FakePalette._Incoming()

    def sendInfoToHTML(self, action, data):
        self.sent.append((action, json.loads(data)))


def main() -> int:
    app = FakeApp()
    palette = FakePalette()
    pump = MainThreadPump(app, "fdmAgent_TestPump")
    # The bridge only stores the pump at construction, so it can be built before
    # the pump is started and then wired in as the consumer.
    bridge = ChatBridge(palette, pump)
    pump.start(bridge.deliver)

    def pump_until(predicate, timeout=30):
        """Stand in for Fusion's main loop."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if app.woken.wait(0.05):
                app.woken.clear()
                pump.drain()
            if predicate():
                return True
        return False

    try:
        bridge.handle_from_html("ready", "{}")
        if not pump_until(
            lambda: any(a == "state" and p["status"] == "ready" for a, p in palette.sent)
        ):
            print("FAIL: sidecar never reported ready")
            return 1

        state = [p for a, p in palette.sent if a == "state"][-1]
        print("status   :", state["status"])
        print("backend  :", state["backend"])
        print("backends :", [(b["name"], b["available"]) for b in state["backends"]])

        palette.sent.clear()
        bridge.handle_from_html("send", json.dumps({"turnId": "t1", "text": "ping"}))
        if not pump_until(lambda: any(a == "turnEnd" for a, _ in palette.sent)):
            print("FAIL: no turnEnd arrived")
            return 1

        deltas = [p["text"] for a, p in palette.sent if a == "delta"]
        ends = [p for a, p in palette.sent if a == "turnEnd"]
        print("deltas   :", len(deltas), "chunks")
        print("turnEnd  :", ends)

        # The whole point of the echo backend: the reply must arrive in pieces.
        # One delta carrying the entire reply means the pump was bypassed.
        if len(deltas) < 20:
            print("FAIL: reply did not stream incrementally")
            return 1
        if len(ends) != 1 or "error" in ends[0]:
            print("FAIL: expected exactly one clean turnEnd")
            return 1
    finally:
        bridge.stop()
        pump.stop()

    print("\nOK: palette -> bridge -> sidecar -> pump -> palette round-trip works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
