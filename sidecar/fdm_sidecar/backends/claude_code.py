"""Claude Code backend.

Detection works today; the conversation itself is the next pass.

Design note for that pass: use the Agent SDK's ``ClaudeSDKClient`` rather than
shelling out to ``claude -p``. The CLI's print mode is one-shot -- multi-turn
continuity means spawning a fresh process with ``--resume`` each time -- while
``ClaudeSDKClient`` holds a single streaming session and, through
``create_sdk_mcp_server``, lets us hand the model in-process tools. Those tools
are where the Fusion design operations will live, calling back into the add-in
over its loopback port.
"""

import asyncio
import importlib.util
import os
import shutil

from .base import Availability, Backend

_SDK_MODULE = "claude_agent_sdk"

# The native installer's default location, which is not always on the PATH
# inherited from Fusion.
_FALLBACK_CLI = os.path.expanduser("~/.local/bin/claude")


def _find_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    if os.path.isfile(_FALLBACK_CLI) and os.access(_FALLBACK_CLI, os.X_OK):
        return _FALLBACK_CLI
    return None


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


class ClaudeCodeBackend(Backend):
    name = "claude"
    label = "Claude Code"

    async def available(self) -> Availability:
        has_sdk = importlib.util.find_spec(_SDK_MODULE) is not None
        cli = _find_cli()

        if not has_sdk:
            return Availability(
                available=False,
                detail=(
                    "claude-agent-sdk is not installed in the sidecar venv. "
                    "Run scripts/bootstrap.sh."
                ),
            )

        version = await _cli_version(cli) if cli else None
        # The SDK ships its own native binaries, so a missing PATH entry is
        # worth reporting but is not disqualifying.
        detail = "Not implemented yet (pass 2)."
        if not cli:
            detail += " No `claude` on PATH; the SDK's bundled binary will be used."
        return Availability(available=False, version=version, detail=detail)

    async def start(self) -> None:
        raise NotImplementedError(
            "The Claude Code backend is not implemented yet. Use the echo backend."
        )

    async def send(self, turn_id: str, text: str) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        return
