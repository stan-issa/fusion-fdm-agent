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
from .logging_util import get_logger
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
        elif action == "approvalReply":
            self._forward({
                "action": "approvalResponse",
                "id": payload.get("id"),
                "allow": bool(payload.get("allow")),
            })
        else:
            self._log.warning("unknown palette action %r", action)

        return json.dumps({"ok": True})

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
        try:
            self._palette.incomingFromHTML.remove(self._handler)
        except Exception:
            pass
        self._handler = None
        self._sidecar.stop()
