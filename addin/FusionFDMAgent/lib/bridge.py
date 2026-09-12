"""Routes messages between the chat palette and the sidecar.

Both hops speak the same small vocabulary, so a message from the sidecar is
mostly forwarded to the palette unchanged. See docs/PROTOCOL.md for the wire
format.

Threading: ``handle_from_html`` and ``deliver`` both run on Fusion's main
thread -- the first because Fusion fires palette events there, the second
because it is only ever reached through the pump.
"""

import json
import traceback

import adsk.core

from .. import config
from . import rules_controller
from .logging_util import get_logger
from .rules.base import RuleError
from .sidecar import SidecarError, SidecarProcess

# Messages the sidecar may emit that we pass straight through to the palette.
_PASSTHROUGH_ACTIONS = ("delta", "toolUse", "turnEnd", "log", "approvalRequest")


class _IncomingHandler(adsk.core.HTMLEventHandler):
    """Receives ``adsk.fusionSendData`` calls from the palette's JavaScript."""

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge

    def notify(self, args):
        try:
            args.returnData = self._bridge.handle_from_html(args.action, args.data)
        except Exception:
            get_logger().error(
                "palette action %r failed\n%s", args.action, traceback.format_exc()
            )
            args.returnData = json.dumps({"ok": False})


class ChatBridge:
    """Owns the palette conversation and the sidecar that answers it."""

    def __init__(self, palette, pump, tool_server=None):
        self._palette = palette
        self._pump = pump
        self._tool_server = tool_server
        self._log = get_logger()
        self._settings = config.load_settings()
        self._status = "starting"
        self._detail = ""
        self._backends = []
        self._handler = _IncomingHandler(self)
        palette.incomingFromHTML.add(self._handler)
        # A check the agent runs is still a check: the Rules tab should show
        # it rather than sit on a stale list behind the conversation.
        rules_controller.set_result_listener(self._on_rule_result)

        self._sidecar = SidecarProcess(
            on_message=self._on_sidecar_message,
            on_exit=self._on_sidecar_exit,
            extra_env=self._tool_env(),
        )

    def _tool_env(self):
        """How the sidecar reaches Fusion, and proves it is allowed to."""
        if self._tool_server is None or self._tool_server.port is None:
            return {}
        return {
            "FDM_AGENT_TOOL_PORT": str(self._tool_server.port),
            "FDM_AGENT_TOOL_TOKEN": self._tool_server.token,
        }

    # -- palette -> here ---------------------------------------------------

    def handle_from_html(self, action, data):
        payload = {}
        if data:
            try:
                payload = json.loads(data)
            except ValueError:
                self._log.warning("palette sent non-JSON for %r: %s", action, data[:200])

        if action == "ready":
            self._on_palette_ready()
        elif action == "send":
            self._forward({
                "action": "send",
                "turnId": payload.get("turnId"),
                "text": payload.get("text", ""),
            })
        elif action == "cancel":
            self._forward({"action": "cancel", "turnId": payload.get("turnId")})
        elif action == "setBackend":
            self._set_backend(payload.get("backend"))
        elif action == "restart":
            self._restart()
        elif action.startswith("rules"):
            self._handle_rules(action, payload)
        elif action == "approvalReply":
            self._forward({
                "action": "approvalResponse",
                "id": payload.get("id"),
                "allow": bool(payload.get("allow")),
            })
        else:
            self._log.warning("unknown palette action %r", action)

        return json.dumps({"ok": True})

    # -- rules -------------------------------------------------------------

    def _handle_rules(self, action, payload):
        """Run one Rules-tab request.

        Every branch answers exactly once, including on failure, for the same
        reason a turn always ends in `turnEnd`: the panel disables its buttons
        while a request is out, and a request that never comes back leaves
        them disabled for good.
        """
        request_id = payload.get("requestId")
        try:
            if action == "rulesList":
                self._to_html("rules", rules_controller.list_rules())
            elif action == "rulesCheck":
                self._to_html("rulesResult", _tag(rules_controller.check_rules(
                    payload.get("rules"), payload.get("params"), announce=False,
                ), request_id))
            elif action == "rulesApply":
                # The user ticked these and pressed Apply. That click is the
                # consent, so there is no approval card here -- one would be
                # asking the same question twice. The agent's path through
                # apply_rule_fix still goes through the card.
                self._to_html("rulesApplied", _tag(rules_controller.apply_rule_fix(
                    payload.get("findingIds"), payload.get("params"), announce=False,
                ), request_id))
            elif action == "rulesReveal":
                self._to_html("rulesRevealed", _tag(
                    rules_controller.reveal(payload.get("findingId")), request_id
                ))
            elif action == "rulesIgnore":
                self._to_html("rulesResult", _tag(rules_controller.ignore(
                    payload.get("findingId"), bool(payload.get("ignore", True)),
                ), request_id))
            elif action == "rulesSetParams":
                self._to_html("rules", rules_controller.save_rule_settings(
                    payload.get("rule"), payload.get("enabled"), payload.get("params"),
                ))
            else:
                self._log.warning("unknown rules action %r", action)
                return
        except RuleError as exc:
            self._to_html(_reply_for(action), _tag({"error": str(exc)}, request_id))
        except Exception as exc:
            self._log.error("%s failed\n%s", action, traceback.format_exc())
            self._to_html(_reply_for(action), _tag(
                {"error": "{}: {}".format(type(exc).__name__, exc)}, request_id
            ))

    def _on_rule_result(self, result):
        """Push findings the agent produced into the Rules tab."""
        self._to_html("rulesResult", result)

    def _on_palette_ready(self):
        self._log.info("palette ready")
        if not self._sidecar.is_running():
            self._start_sidecar()
        else:
            self._push_state()

    def _set_backend(self, backend):
        if not backend:
            return
        self._settings["backend"] = backend
        config.save_settings(self._settings)
        self._log.info("backend set to %s", backend)
        self._forward({"action": "setBackend", "backend": backend})

    def _restart(self):
        self._log.info("restarting sidecar on palette request")
        self._sidecar.stop()
        # The port and token are regenerated whenever the add-in restarts, so
        # re-read them rather than reusing what the process was spawned with.
        self._sidecar.set_extra_env(self._tool_env())
        self._start_sidecar()

    def _forward(self, message):
        """Send a message to the sidecar, surfacing failures in the palette."""
        try:
            self._sidecar.send(message)
        except SidecarError as exc:
            self._log.error("forward failed: %s", exc)
            self._status = "error"
            self._detail = str(exc)
            self._push_state()
            turn_id = message.get("turnId")
            if turn_id:
                self._to_html("turnEnd", {"turnId": turn_id, "error": str(exc)})

    # -- sidecar lifecycle -------------------------------------------------

    def _start_sidecar(self):
        try:
            self._sidecar.start()
            self._status = "starting"
            self._detail = ""
        except SidecarError as exc:
            self._log.error("sidecar start failed: %s", exc)
            self._status = "error"
            self._detail = str(exc)
        self._push_state()

    def _on_sidecar_message(self, message):
        """Background thread. Hand off to the main thread and return."""
        self._pump.post(message)

    def _on_sidecar_exit(self, code):
        """Background thread. Same rule as above."""
        self._pump.post({
            "action": "sidecarExit",
            "code": code,
        })

    # -- pump -> palette ---------------------------------------------------

    def deliver(self, message):
        """Consume one pumped sidecar message. Main thread only."""
        action = message.get("action")

        if action == "ready":
            self._status = "ready"
            self._detail = ""
            self._backends = message.get("backends", [])
            backend = message.get("backend")
            if backend:
                self._settings["backend"] = backend
            # The sidecar boots with its own default; align it with the user's
            # stored choice now that we know which backends exist.
            desired = self._settings.get("backend")
            if desired and desired != backend:
                self._forward({"action": "setBackend", "backend": desired})
            self._push_state()
        elif action == "state":
            self._backends = message.get("backends", self._backends)
            if message.get("backend"):
                self._settings["backend"] = message["backend"]
            self._push_state()
        elif action == "sidecarExit":
            self._status = "error"
            self._detail = "Sidecar exited (code {}). See {}.".format(
                message.get("code"), config.SIDECAR_LOG_FILE
            )
            self._push_state()
        elif action in _PASSTHROUGH_ACTIONS:
            payload = dict(message)
            payload.pop("action", None)
            if action == "approvalRequest":
                _explain_rule_fix(payload)
            self._to_html("approval" if action == "approvalRequest" else action, payload)
        else:
            self._log.warning("unknown sidecar action %r", action)

    def _push_state(self):
        self._to_html("state", {
            "backend": self._settings.get("backend"),
            "backends": self._backends,
            "status": self._status,
            "detail": self._detail,
            "workspace": config.WORKSPACE_DIR,
        })

    def _to_html(self, action, payload):
        try:
            self._palette.sendInfoToHTML(action, json.dumps(payload, ensure_ascii=False))
        except Exception:
            # Palette closed or Fusion shutting down.
            self._log.debug("sendInfoToHTML(%s) failed\n%s", action, traceback.format_exc())

    # -- teardown ----------------------------------------------------------

    def stop(self):
        rules_controller.set_result_listener(None)
        try:
            self._palette.incomingFromHTML.remove(self._handler)
        except Exception:
            pass
        self._handler = None
        self._sidecar.stop()


# Which message answers which request, so a failure is reported on the channel
# the palette is listening to rather than vanishing.
_RULES_REPLIES = {
    "rulesList": "rules",
    "rulesCheck": "rulesResult",
    "rulesApply": "rulesApplied",
    "rulesReveal": "rulesRevealed",
    "rulesIgnore": "rulesResult",
    "rulesSetParams": "rules",
}


def _reply_for(action):
    return _RULES_REPLIES.get(action, "rulesResult")


def _tag(payload, request_id):
    if request_id:
        payload = dict(payload)
        payload["requestId"] = request_id
    return payload


def _explain_rule_fix(payload):
    """Turn a list of finding ids into something a person can approve.

    On the wire the request is `{"finding_ids": ["bed_chamfer:1f2a3b4c"]}`,
    which tells the user nothing. The add-in owns the rule session, so this is
    the only place that has both the request and its meaning. The ids stay in
    the payload: the card explains the request, it does not replace it.
    """
    tool = payload.get("tool") or ""
    if not tool.endswith("apply_rule_fix"):
        return
    ids = (payload.get("input") or {}).get("finding_ids") or []
    try:
        payload["findings"] = rules_controller.describe_findings(ids)
    except Exception:
        get_logger().debug("could not describe findings for approval", exc_info=True)
