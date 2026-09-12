"""Sidecar entry point: newline-delimited JSON over stdin/stdout.

Run standalone to exercise the protocol without Fusion::

    echo '{"action":"send","turnId":"1","text":"hi"}' | python -m fdm_sidecar

Everything runs on one asyncio loop. Each turn is its own task, which is what
makes cancellation a one-liner.
"""

import asyncio
import json
import logging
import os
import sys
import traceback

from . import protocol
from .backends import BACKEND_CLASSES
from .backends.base import describe

_LOG = logging.getLogger("fdm_sidecar")


def _configure_logging() -> None:
    path = os.environ.get("FDM_AGENT_LOG_FILE")
    handlers: list[logging.Handler] = []
    if path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            handlers.append(logging.FileHandler(path, encoding="utf-8"))
        except OSError:
            pass
    if not handlers:
        # stdout is the protocol channel, so diagnostics must go to stderr.
        handlers.append(logging.StreamHandler(sys.stderr))
    # Root stays at INFO so asyncio's own debug chatter does not drown the log;
    # our logger opts into DEBUG for itself.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )
    _LOG.setLevel(logging.DEBUG)


class Sidecar:
    def __init__(self, workspace: str):
        self.workspace = workspace
        self._backends = {
            cls.name: cls(self.emit, workspace) for cls in BACKEND_CLASSES
        }
        self._descriptions: list[dict] = []
        self._current = None
        self._turns: dict[str, asyncio.Task] = {}

    # -- output ------------------------------------------------------------

    def emit(self, message: dict) -> None:
        """Write one protocol message. Loop thread only."""
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    # -- lifecycle ---------------------------------------------------------

    async def run(self) -> None:
        await self._probe_backends()

        default = self._default_backend()
        await self._activate(default)
        self.emit(protocol.ready(self._current.name, self._descriptions))
        _LOG.info("sidecar ready, backend=%s", self._current.name)

        await self._read_loop()
        await self._shutdown()

    async def _probe_backends(self) -> None:
        descriptions = []
        for name, backend in self._backends.items():
            try:
                availability = await backend.available()
            except Exception:
                _LOG.error("availability check failed for %s\n%s", name, traceback.format_exc())
                from .backends.base import Availability

                availability = Availability(available=False, detail="Check failed.")
            descriptions.append(describe(type(backend), availability))
        self._descriptions = descriptions

    def _default_backend(self) -> str:
        for description in self._descriptions:
            if description["available"]:
                return description["name"]
        return "echo"

    async def _activate(self, name: str) -> None:
        backend = self._backends.get(name)
        if backend is None:
            raise KeyError(name)
        if self._current is not None:
            await self._current.stop()
        await backend.start()
        self._current = backend

    async def _read_loop(self) -> None:
        while True:
            # readline on a pipe blocks, so it belongs on a worker thread.
            line = await asyncio.to_thread(sys.stdin.readline)
            if line == "":
                _LOG.info("stdin closed")
                return
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                _LOG.warning("ignoring non-JSON input: %s", line[:500])
                continue
            try:
                await self._dispatch(message)
            except Exception:
                _LOG.error("dispatch failed for %r\n%s", message, traceback.format_exc())

    async def _shutdown(self) -> None:
        for task in list(self._turns.values()):
            task.cancel()
        if self._turns:
            await asyncio.gather(*self._turns.values(), return_exceptions=True)
        if self._current is not None:
            await self._current.stop()

    # -- dispatch ----------------------------------------------------------

    async def _dispatch(self, message: dict) -> None:
        action = message.get("action")
        if action == protocol.IN_SEND:
            self._start_turn(message.get("turnId"), message.get("text", ""))
        elif action == protocol.IN_CANCEL:
            await self._cancel_turn(message.get("turnId"))
        elif action == protocol.IN_SET_BACKEND:
            await self._set_backend(message.get("backend"))
        else:
            _LOG.warning("unknown action %r", action)

    def _start_turn(self, turn_id, text: str) -> None:
        if not turn_id:
            _LOG.warning("send without a turnId")
            return
        if turn_id in self._turns:
            _LOG.warning("turn %s already running", turn_id)
            return
        task = asyncio.create_task(self._run_turn(turn_id, text))
        self._turns[turn_id] = task

    async def _run_turn(self, turn_id: str, text: str) -> None:
        try:
            await self._current.send(turn_id, text)
        except asyncio.CancelledError:
            # The backend emits its own turnEnd when it sees the cancellation.
            raise
        except NotImplementedError as exc:
            self.emit(protocol.turn_end(turn_id, error=str(exc) or "Not implemented."))
        except Exception as exc:
            _LOG.error("turn %s failed\n%s", turn_id, traceback.format_exc())
            self.emit(protocol.turn_end(turn_id, error="{}: {}".format(type(exc).__name__, exc)))
        finally:
            self._turns.pop(turn_id, None)

    async def _cancel_turn(self, turn_id) -> None:
        task = self._turns.get(turn_id)
        if task is None:
            return
        _LOG.info("cancelling turn %s", turn_id)
        await self._current.cancel(turn_id)
        task.cancel()

    async def _set_backend(self, name) -> None:
        if not name or name == getattr(self._current, "name", None):
            return
        previous = self._current.name if self._current else None
        try:
            await self._activate(name)
            _LOG.info("backend switched to %s", name)
        except Exception as exc:
            _LOG.error("could not switch to %s\n%s", name, traceback.format_exc())
            self.emit(protocol.log("error", "Cannot use {}: {}".format(name, exc)))
            if previous:
                # Leave the user on something that works rather than nothing.
                try:
                    await self._activate(previous)
                except Exception:
                    _LOG.error("could not restore %s\n%s", previous, traceback.format_exc())
        self.emit(protocol.state(self._current.name, self._descriptions))


def main() -> int:
    _configure_logging()
    workspace = os.environ.get("FDM_AGENT_WORKSPACE") or os.getcwd()
    try:
        asyncio.run(Sidecar(workspace).run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
