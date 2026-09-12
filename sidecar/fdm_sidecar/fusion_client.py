"""Client for the add-in's loopback tool server.

The add-in binds an ephemeral port on 127.0.0.1 and passes the port and a
per-session token to this process in its environment. One connection per call
keeps things simple; calls are infrequent and each already costs a round trip
to Fusion's main thread.
"""

import asyncio
import json
import os
from typing import Any

# The add-in gives up on a call after 120s; wait slightly longer so its
# specific timeout message is what surfaces, rather than a generic one here.
_TIMEOUT_SECONDS = 150


class FusionUnavailable(Exception):
    """Fusion cannot be reached, so no design tool can run."""


class FusionCallFailed(Exception):
    """Fusion was reached but the tool itself failed."""


def configured() -> bool:
    return bool(os.environ.get("FDM_AGENT_TOOL_PORT") and os.environ.get("FDM_AGENT_TOOL_TOKEN"))


async def call(tool: str, payload: dict[str, Any]) -> Any:
    """Run one tool inside Fusion and return its result."""
    port = os.environ.get("FDM_AGENT_TOOL_PORT")
    token = os.environ.get("FDM_AGENT_TOOL_TOKEN")
    if not port or not token:
        raise FusionUnavailable(
            "Not connected to Fusion. The add-in starts this sidecar with a "
            "port and token; running it standalone has neither."
        )

    request = json.dumps({"token": token, "tool": tool, "input": payload}) + "\n"

    try:
        response = await asyncio.wait_for(
            _round_trip(int(port), request), timeout=_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError as exc:
        raise FusionUnavailable("Fusion did not respond within {}s.".format(_TIMEOUT_SECONDS)) from exc
    except OSError as exc:
        raise FusionUnavailable("Could not reach Fusion: {}".format(exc)) from exc

    if not response.get("ok"):
        raise FusionCallFailed(response.get("error") or "Unknown error.")
    return response.get("result")


async def _round_trip(port: int, request: str) -> dict:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(request.encode("utf-8"))
        await writer.drain()
        line = await reader.readline()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    if not line:
        raise FusionUnavailable("Fusion closed the connection without replying.")
    return json.loads(line.decode("utf-8"))
