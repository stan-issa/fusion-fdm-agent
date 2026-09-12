"""A backend with no external dependencies, used to exercise the plumbing.

It streams its reply word by word on a timer. That matters: it is the only way
to prove the whole asynchronous path -- sidecar task, stdio, the add-in's
reader thread, the queue, the custom event, ``sendInfoToHTML``, the DOM -- is
genuinely incremental rather than one batched write at the end.
"""

import asyncio

from .. import protocol
from .base import Availability, Backend

_WORD_DELAY = 0.06

_REPLY = (
    "Echo backend here. I received: {text!r}\n\n"
    "I am a stub with no model behind me, so this is all I can say. "
    "The point of this reply is the way it arrives: one word at a time, "
    "through the sidecar's stdout, the add-in's reader thread, the "
    "main-thread pump and finally the palette. If you are watching it appear "
    "gradually, every link in that chain works.\n\n"
    "Pick a real backend once one is implemented."
)


class EchoBackend(Backend):
    name = "echo"
    label = "Echo (stub)"

    async def available(self) -> Availability:
        return Availability(available=True, version=None, detail="Built in; no setup needed.")

    async def start(self) -> None:
        self.log("info", "echo backend ready")

    async def send(self, turn_id: str, text: str) -> None:
        try:
            reply = _REPLY.format(text=text)
            for index, word in enumerate(reply.split(" ")):
                await asyncio.sleep(_WORD_DELAY)
                chunk = word if index == 0 else " " + word
                self.emit(protocol.delta(turn_id, chunk))
        except asyncio.CancelledError:
            self.emit(protocol.turn_end(turn_id, error="Cancelled."))
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self.emit(protocol.turn_end(turn_id, error=str(exc)))
            return
        self.emit(protocol.turn_end(turn_id))

    async def stop(self) -> None:
        return
