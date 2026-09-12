"""Agent backends.

Each backend adapts one external agent to the :class:`Backend` interface. The
registry is ordered: the first available backend is the sidecar's default.
"""

from .base import Availability, Backend
from .claude_code import ClaudeCodeBackend
from .codex import CodexBackend
from .echo import EchoBackend

BACKEND_CLASSES = (EchoBackend, ClaudeCodeBackend, CodexBackend)

__all__ = [
    "Availability",
    "Backend",
    "BACKEND_CLASSES",
    "ClaudeCodeBackend",
    "CodexBackend",
    "EchoBackend",
]
