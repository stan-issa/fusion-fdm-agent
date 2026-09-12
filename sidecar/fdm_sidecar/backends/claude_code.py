"""Claude Code backend, built on the Agent SDK's persistent client.

``ClaudeSDKClient`` is used rather than shelling out to ``claude -p`` because
print mode is one-shot: continuing a conversation means a fresh process with
``--resume`` each turn. One long-lived client *is* the session, and it is also
what will let pass 3 hand the model in-process Fusion tools through
``create_sdk_mcp_server`` and approve each call via ``can_use_tool``.

Two details are load-bearing:

* ``include_partial_messages`` turns on token-level ``StreamEvent`` deltas.
  Without it the reply only arrives as whole blocks and the palette's streaming
  is wasted.
* ``permission_mode="dontAsk"`` runs pre-approved tools and *denies* anything
  else. The tempting ``"default"`` would block waiting for an approval that,
  in a headless sidecar, nobody can give -- the turn would simply hang.
"""

import asyncio
import importlib.util
import os
import shutil

from .. import fusion_client, fusion_tools, protocol
from ..settings import backend_settings
from .base import Availability, Backend, summarise_input

_SDK_MODULE = "claude_agent_sdk"

# The native installer's location, which is not always on the PATH Fusion
# hands down to the sidecar.
_FALLBACK_CLI = os.path.expanduser("~/.local/bin/claude")

# Read-only and workspace-file tools. Bash is added only when the user opts in.
_BASE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]

_SYSTEM_PROMPT = """\
You are an assistant embedded in a chat panel inside Autodesk Fusion, helping \
with parts designed for FDM (fused deposition modelling) 3D printing.

Favour advice that reflects how FDM actually behaves: layer adhesion and \
anisotropy, overhang angles and support, wall count and infill against \
strength, shrinkage and warping, bridging, elephant's foot, and tolerances for \
holes and mating parts.

Keep replies short and concrete. This is a narrow side panel, not a document.\
"""

_FUSION_PROMPT = """\

You have live access to the open Fusion document:

- get_design_tree and get_parameters read it. Call them before reasoning about \
  the user's model; do not guess at dimensions or structure, and do not claim \
  to have looked at something you have not.
- get_selection says what the user has selected. "This face", "these holes" \
  and "the bottom" mean nothing without it.
- list_rules and check_rules run the built-in FDM printability rules.
- set_parameter changes one parameter's expression.
- apply_rule_fix applies the fixes check_rules found, by id.
- run_fusion_script executes Python against the Fusion API in the running \
  application.

The last three change the user's document and each one asks them to approve it \
first, so:

- Prefer set_parameter over a script whenever the parameter already exists.
- Keep scripts short and readable. The user is reading them in a narrow panel \
  to decide whether to allow them, so a wall of code is a wall they cannot \
  check.
- Do one coherent change per script rather than batching unrelated edits, so a \
  refusal costs only the part they objected to.
- Say what you are about to do and why before calling them.
- If the user declines, do not retry the same call. Ask what they would prefer.

On the rules specifically:

- Use check_rules rather than writing your own geometry-inspection script for \
  anything a rule already covers. It measures the model properly, reports what \
  it deliberately skipped and why, and returns ids that apply_rule_fix acts on.
- Its answer depends entirely on the build direction, which it reports along \
  with where that came from. If the source is "default" or "inferred", say so \
  and offer to use a face the user selects instead. Never present findings as \
  settled when the orientation behind them was a guess.
- Summarise findings; do not dump the JSON. The user has the same list in the \
  Rules tab beside you.
- The user can apply fixes themselves from that tab, so the model may have \
  changed since your last look. If a result seems out of date, check again \
  rather than assuming.
- apply_rule_fix re-checks the model and returns the fresh findings. Report \
  what it actually returned rather than what you expected it to do.

Fusion's API works in centimetres internally. Design tree, selection and rule \
values are already converted to millimetres; anything you compute in a script \
is not.\
"""


_NO_FUSION_PROMPT = """\

You do not currently have access to the live Fusion document, so you cannot \
read the design tree, inspect parameters or change geometry. If a question \
needs something only the open model can tell you, ask the user rather than \
assuming.\
"""


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


_NOT_SIGNED_IN = (
    "Claude Code is not signed in. Run `claude` in a terminal, log in, then "
    "restart this panel."
)

# SDK error codes that are worth rephrasing for someone looking at a CAD panel.
_ERROR_MESSAGES = {
    "authentication_failed": _NOT_SIGNED_IN,
    "billing_error": "Claude rejected the request for billing reasons.",
    "rate_limit": "Rate limited by the Claude API. Wait a moment and retry.",
}


class ClaudeCodeBackend(Backend):
    name = "claude"
    label = "Claude Code"

    def __init__(self, emit, workspace, approve=None):
        super().__init__(emit, workspace, approve)
        self._client = None
        self._settings = backend_settings(self.name)
        self._fusion_connected = False

    # -- availability ------------------------------------------------------

    async def available(self) -> Availability:
        if importlib.util.find_spec(_SDK_MODULE) is None:
            return Availability(
                available=False,
                detail=(
                    "claude-agent-sdk is not installed in the sidecar venv. "
                    "Run scripts/bootstrap.sh."
                ),
            )

        cli = _find_cli()
        version = await _cli_version(cli) if cli else None
        # The SDK bundles its own Claude Code binary, so a missing PATH entry
        # is informative but not disqualifying. Sign-in state is not checked
        # here: doing so would cost a model call, so it surfaces on first use.
        detail = "Ready." if cli else "Using the SDK's bundled Claude Code binary."
        return Availability(available=True, version=version, detail=detail)

    # -- lifecycle ---------------------------------------------------------

    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions

        tools = list(_BASE_TOOLS)
        if self._settings.get("allowBash"):
            tools.append("Bash")

        # Only advertise the design tools when the add-in actually gave us a
        # way to reach Fusion. Offering them standalone would have the model
        # confidently describing a document it cannot see.
        self._fusion_connected = fusion_client.configured()

        prompt = _SYSTEM_PROMPT
        mcp_servers = {}
        if self._fusion_connected:
            prompt += _FUSION_PROMPT
            tools.extend(fusion_tools.ALLOWED_TOOLS)
            mcp_servers[fusion_tools.SERVER_NAME] = fusion_tools.build_server()
        else:
            prompt += _NO_FUSION_PROMPT

        extra = (self._settings.get("systemPromptExtra") or "").strip()
        if extra:
            prompt = prompt + "\n\n" + extra

        options = {
            "cwd": self.workspace,
            "system_prompt": prompt,
            "allowed_tools": tools,
            "mcp_servers": mcp_servers,
            "can_use_tool": self._can_use_tool,
            # Runs pre-approved tools, denies the rest. Never blocks on a
            # prompt nobody is there to answer.
            "permission_mode": "dontAsk",
            # Token-level deltas, so the palette streams.
            "include_partial_messages": True,
        }
        model = self._settings.get("model")
        if model:
            options["model"] = model
        return ClaudeAgentOptions(**options)

    async def _can_use_tool(self, tool_name, tool_input, context):
        """Gate tool calls that would change the user's document.

        Everything else is allowed outright -- the allowed_tools list already
        decides what exists, and prompting for a read would only train the
        user to click through.
        """
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if tool_name not in fusion_tools.MUTATING_TOOLS:
            return PermissionResultAllow()
        if self._settings.get("autoApprove"):
            return PermissionResultAllow()

        if await self.approve(tool_name, tool_input):
            return PermissionResultAllow()
        return PermissionResultDeny(
            message="The user declined this change. Ask what they would prefer "
                    "instead of retrying."
        )

    async def start(self) -> None:
        from claude_agent_sdk import ClaudeSDKClient

        os.makedirs(self.workspace, exist_ok=True)
        self._client = ClaudeSDKClient(self._options())
        try:
            await self._client.connect()
        except Exception:
            self._client = None
            raise
        self.log("info", "claude backend connected (cwd={}, fusion={})".format(
            self.workspace, "yes" if self._fusion_connected else "no"
        ))

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            pass

    async def cancel(self, turn_id: str) -> None:
        """Ask Claude to stop generating.

        The runner also cancels the task driving :meth:`send`, but that alone
        would leave the model still working on the other side of the pipe.
        """
        if self._client is None:
            return
        try:
            await self._client.interrupt()
        except Exception as exc:
            self.log("warning", "interrupt failed: {}".format(exc))

    # -- turns -------------------------------------------------------------

    async def send(self, turn_id: str, text: str) -> None:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            StreamEvent,
            TextBlock,
            ToolUseBlock,
        )

        if self._client is None:
            self.emit(protocol.turn_end(turn_id, error="Backend is not connected."))
            return

        streamed = False
        # If partial messages never arrive, the whole reply would be lost, so
        # keep the complete blocks as a fallback and emit them at the end.
        fallback: list[str] = []
        error: str | None = None

        try:
            await self._client.query(text)

            async for message in self._client.receive_response():
                if isinstance(message, StreamEvent):
                    chunk = _text_delta(message.event)
                    if chunk:
                        streamed = True
                        self.emit(protocol.delta(turn_id, chunk))

                elif isinstance(message, AssistantMessage):
                    if message.error:
                        error = _ERROR_MESSAGES.get(
                            message.error,
                            "Claude returned an error: {}".format(message.error),
                        )
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            self.emit(protocol.tool_use(
                                turn_id, block.name, summarise_input(block.input)
                            ))
                        elif isinstance(block, TextBlock):
                            fallback.append(block.text)

                elif isinstance(message, ResultMessage):
                    if message.is_error and error is None:
                        error = _result_error(message)
                    break

        except asyncio.CancelledError:
            self.emit(protocol.turn_end(turn_id, error="Cancelled."))
            raise
        except Exception as exc:
            self.emit(protocol.turn_end(turn_id, error=_describe(exc)))
            return

        if not streamed and fallback:
            self.emit(protocol.delta(turn_id, "".join(fallback)))

        self.emit(protocol.turn_end(turn_id, error=error))


def _text_delta(event: dict) -> str | None:
    """Pull assistant text out of one raw streaming event, if it carries any."""
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return None
    return delta.get("text") or None


def _result_error(message) -> str:
    errors = getattr(message, "errors", None)
    if errors:
        return "; ".join(str(e) for e in errors)
    if getattr(message, "result", None):
        return str(message.result)
    return "Turn failed ({}).".format(getattr(message, "subtype", "unknown"))


def _describe(exc: Exception) -> str:
    """Turn an SDK exception into something readable in a CAD side panel."""
    text = str(exc)
    name = type(exc).__name__
    if name == "CLINotFoundError":
        return (
            "Could not find the Claude Code binary. Install it with "
            "`curl -fsSL https://claude.ai/install.sh | bash`."
        )
    if "authentication" in text.lower() or "not logged in" in text.lower():
        return _NOT_SIGNED_IN
    return "{}: {}".format(name, text) if text else name
