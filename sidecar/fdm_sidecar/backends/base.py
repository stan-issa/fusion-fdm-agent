"""The seam every agent backend implements."""

import abc
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .. import protocol


@dataclass(frozen=True)
class Availability:
    """Whether a backend can actually run, and why not if it cannot."""

    available: bool
    version: Optional[str] = None
    detail: str = ""

    def as_dict(self, name: str, label: str) -> dict:
        return {
            "name": name,
            "label": label,
            "available": self.available,
            "version": self.version,
            "detail": self.detail,
        }


class Backend(abc.ABC):
    """Adapts one external agent to the sidecar's streaming protocol.

    Implementations report progress by calling :meth:`emit` with protocol
    messages rather than returning a value, so a turn can stream.
    """

    #: Stable identifier used on the wire and in settings.
    name: str = ""
    #: Human-readable name shown in the palette's backend picker.
    label: str = ""

    def __init__(self, emit: Callable[[dict], None], workspace: str):
        self._emit = emit
        self.workspace = workspace

    def emit(self, message: dict) -> None:
        self._emit(message)

    def log(self, level: str, message: str) -> None:
        self._emit(protocol.log(level, message))

    @abc.abstractmethod
    async def available(self) -> Availability:
        """Report whether this backend's dependencies are present."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Prepare the backend for its first turn."""

    @abc.abstractmethod
    async def send(self, turn_id: str, text: str) -> None:
        """Run one turn, emitting deltas as they arrive.

        Must emit exactly one ``turnEnd`` for ``turn_id`` before returning,
        including on failure -- the palette leaves the composer disabled until
        it arrives.
        """

    async def cancel(self, turn_id: str) -> None:
        """Stop an in-flight turn.

        The default is a no-op: the runner already cancels the asyncio task
        driving :meth:`send`, which is enough for backends that do all their
        work in-process. Override when an external process needs telling.
        """

    async def stop(self) -> None:
        """Release any resources held by the backend."""


def describe(backend_class, availability: Availability) -> dict:
    return availability.as_dict(backend_class.name, backend_class.label)


def summarise_input(value: Any, limit: int = 300) -> Any:
    """Trim tool input so a large payload cannot flood the palette."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value
