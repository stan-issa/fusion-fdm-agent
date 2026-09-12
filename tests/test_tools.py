"""Exercises the loopback tool path without Fusion.

Covers the surface that is easy to get quietly wrong: the socket protocol, the
token check, and the hop onto the "main thread" and back. The Fusion
operations themselves are replaced by a fake registry -- they need a running
Fusion and are verified by using the add-in.

Both halves are imported here, the add-in's server and the sidecar's client,
so the two ends are tested against each other rather than against a mock of
each other.

Run with ./scripts/test.sh
"""

import asyncio
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "adsk_stub"))
sys.path.insert(0, os.path.join(ROOT, "addin"))
sys.path.insert(0, os.path.join(ROOT, "sidecar"))

from FusionFDMAgent.lib.events import MainThreadPump   # noqa: E402
from FusionFDMAgent.lib.toolserver import ToolCall, ToolServer  # noqa: E402
from fdm_sidecar import fusion_client                  # noqa: E402


class FakeEvent:
    def add(self, handler):
        pass

    def remove(self, handler):
        return True


class FakeApp:
    def __init__(self):
        self.woken = threading.Event()

    def registerCustomEvent(self, event_id):
        return FakeEvent()

    def unregisterCustomEvent(self, event_id):
        return True

    def fireCustomEvent(self, event_id, data=""):
        self.woken.set()
        return True


class ToolError(Exception):
    """Mirrors design_tools.ToolError, which toolserver detects by name."""


def _greet(name, excited=False):
    return {"greeting": "hello " + name, "excited": excited}


def _explode():
    raise ToolError("no design is open")


REGISTRY = {"greet": _greet, "explode": _explode}

failures = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s %s" % (label, detail))
        failures.append(label)


def main() -> int:
    app = FakeApp()
    pump = MainThreadPump(app, "fdmAgent_ToolTest")
    server = ToolServer(pump, REGISTRY)

    executed = []

    def consume(item):
        if isinstance(item, ToolCall):
            # Stand in for Fusion's main thread.
            executed.append(threading.current_thread().name)
            server.execute(item)

    pump.start(consume)
    port = server.start()
    os.environ["FDM_AGENT_TOOL_PORT"] = str(port)
    os.environ["FDM_AGENT_TOOL_TOKEN"] = server.token

    results = {}

    def run_client():
        async def calls():
            results["ok"] = await fusion_client.call("greet", {"name": "world"})
            try:
                await fusion_client.call("explode", {})
            except Exception as exc:
                results["tool_error"] = (type(exc).__name__, str(exc))
            try:
                await fusion_client.call("nosuch", {})
            except Exception as exc:
                results["unknown"] = (type(exc).__name__, str(exc))
            try:
                await fusion_client.call("greet", {"wrong": 1})
            except Exception as exc:
                results["bad_args"] = (type(exc).__name__, str(exc))

            # Same server, wrong token: must be refused.
            good = os.environ["FDM_AGENT_TOOL_TOKEN"]
            os.environ["FDM_AGENT_TOOL_TOKEN"] = "0" * 64
            try:
                await fusion_client.call("greet", {"name": "intruder"})
            except Exception as exc:
                results["bad_token"] = (type(exc).__name__, str(exc))
            finally:
                os.environ["FDM_AGENT_TOOL_TOKEN"] = good

        asyncio.run(calls())
        results["done"] = True

    client = threading.Thread(target=run_client, name="client", daemon=True)
    client.start()

    # Drain the pump the way Fusion's main thread would.
    deadline = time.time() + 30
    while time.time() < deadline and not results.get("done"):
        if app.woken.wait(0.05):
            app.woken.clear()
            pump.drain()
    client.join(timeout=5)

    try:
        check("call returns a result", results.get("ok") == {"greeting": "hello world", "excited": False},
              repr(results.get("ok")))
        check("ran on the draining thread", executed and all(n == "MainThread" for n in executed),
              repr(executed))

        kind, message = results.get("tool_error", ("", ""))
        check("tool error reaches the caller", kind == "FusionCallFailed" and "no design is open" in message,
              repr(results.get("tool_error")))

        kind, message = results.get("unknown", ("", ""))
        check("unknown tool is rejected", kind == "FusionCallFailed" and "nosuch" in message,
              repr(results.get("unknown")))

        kind, message = results.get("bad_args", ("", ""))
        check("bad arguments are reported", kind == "FusionCallFailed" and "Bad arguments" in message,
              repr(results.get("bad_args")))

        kind, message = results.get("bad_token", ("", ""))
        check("wrong token is refused", kind == "FusionCallFailed" and "Unauthorised" in message,
              repr(results.get("bad_token")))
    finally:
        server.stop()
        pump.stop()

    if failures:
        print("\n%d check(s) failed" % len(failures))
        return 1
    print("\nOK: sidecar -> loopback -> main thread -> back works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
