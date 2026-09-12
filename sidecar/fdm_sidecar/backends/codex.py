"""Codex backend.

Detection works today; the conversation itself is deferred -- and deliberately
deferred further than the Claude backend.

The Codex CLI's non-interactive surface (subcommand, JSON output format,
session resumption, MCP support) has not been verified first-hand for this
project. Rather than code against a guess, this stub reports what it can find
on the machine, and the adapter gets written once `codex --help` has been read
against a real install.
"""

import asyncio
import os
import shutil

from .base import Availability, Backend


async def _cli_version(path: str) -> str | None:
    try:
        process = await asyncio.create_subprocess_exec(
            path, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except (OSError, asyncio.TimeoutError):
        return None
    if process.returncode != 0:
        return None
    return stdout.decode("utf-8", "replace").strip() or None


class CodexBackend(Backend):
    name = "codex"
    label = "Codex"

    async def available(self) -> Availability:
        cli = shutil.which("codex") or _expanded("~/.local/bin/codex")
        if not cli:
            return Availability(
                available=False,
                detail="`codex` not found on PATH. Install the Codex CLI to enable it.",
            )
        version = await _cli_version(cli)
        return Availability(
            available=False,
            version=version,
            detail="Found, but the adapter is not implemented yet.",
        )

    async def start(self) -> None:
        raise NotImplementedError(
            "The Codex backend is not implemented yet. Use the echo backend."
        )

    async def send(self, turn_id: str, text: str) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        return


def _expanded(path: str) -> str | None:
    resolved = os.path.expanduser(path)
    if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
        return resolved
    return None
