"""Loopback server letting the sidecar call into Fusion.

The agent runs in the sidecar, but only this process can touch the Fusion API,
and only on its main thread. So the add-in listens on ``127.0.0.1`` and the
sidecar connects to it.

Bound to the loopback interface on an ephemeral port, with a per-session token
that is generated at startup and handed to the sidecar through its environment.
The token is what stops any other local process from driving Fusion -- a port
on localhost is otherwise open to everything on the machine.

Threading: a socket thread accepts the call, hands it to the main thread
through the pump, and blocks on an Event until the main thread has filled in
the result.
"""

import json
import secrets
import socket
import threading
import traceback

from .logging_util import get_logger

# Long enough for a substantial script, short enough that a wedged main thread
# reports an error instead of holding the agent forever.
CALL_TIMEOUT_SECONDS = 120
_MAX_REQUEST_BYTES = 4 * 1024 * 1024


class ToolCall:
    """One pending call, carried across threads by the pump."""

    def __init__(self, tool, payload):
        self.tool = tool
        self.payload = payload
        self.result = None
        self.error = None
        self.done = threading.Event()


class ToolServer:
    def __init__(self, pump, registry):
        self._pump = pump
        self._registry = registry
        self._socket = None
        self._thread = None
        self._running = False
        self._log = get_logger()
        self.port = None
        self.token = None

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Bind and begin accepting. Returns the chosen port."""
        self.token = secrets.token_hex(32)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self.port = self._socket.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(
            target=self._accept_loop, name="fdm-toolserver", daemon=True
        )
        self._thread.start()
        self._log.info("tool server listening on 127.0.0.1:%s", self.port)
        return self.port

    def stop(self):
        self._running = False
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        self.port = None
        self.token = None
        self._log.info("tool server stopped")

    # -- socket side -------------------------------------------------------

    def _accept_loop(self):
        while self._running:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return  # closed during stop()
            threading.Thread(
                target=self._serve, args=(connection,),
                name="fdm-toolcall", daemon=True,
            ).start()

    def _serve(self, connection):
        try:
            connection.settimeout(CALL_TIMEOUT_SECONDS + 30)
            request = self._read_request(connection)
            response = self._handle(request)
        except Exception:
            self._log.error("tool call failed\n%s", traceback.format_exc())
            response = {"ok": False, "error": "Internal error in the tool server."}
        try:
            connection.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def _read_request(self, connection):
        buffer = b""
        while b"\n" not in buffer:
            chunk = connection.recv(65536)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > _MAX_REQUEST_BYTES:
                raise ValueError("request too large")
        return json.loads(buffer.decode("utf-8"))

    def _handle(self, request):
        if not isinstance(request, dict):
            return {"ok": False, "error": "Malformed request."}

        # Compared in constant time: the token is the only thing standing
        # between any local process and the user's CAD model.
        token = request.get("token") or ""
        if not self.token or not secrets.compare_digest(token, self.token):
            self._log.warning("rejected tool call with a bad token")
            return {"ok": False, "error": "Unauthorised."}

        tool = request.get("tool")
        if tool not in self._registry:
            return {"ok": False, "error": "Unknown tool {!r}.".format(tool)}

        payload = request.get("input") or {}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "Tool input must be an object."}

        call = ToolCall(tool, payload)
        self._pump.post(call)
        if not call.done.wait(CALL_TIMEOUT_SECONDS):
            return {
                "ok": False,
                "error": "Timed out after {}s waiting for Fusion. It may be busy "
                         "or showing a dialog.".format(CALL_TIMEOUT_SECONDS),
            }
        if call.error is not None:
            return {"ok": False, "error": call.error}
        return {"ok": True, "result": call.result}

    # -- main thread -------------------------------------------------------

    def execute(self, call):
        """Run one call. Main thread only, reached through the pump."""
        function = self._registry.get(call.tool)
        try:
            if function is None:
                raise KeyError(call.tool)
            call.result = function(**call.payload)
        except TypeError as exc:
            call.error = "Bad arguments for {}: {}".format(call.tool, exc)
        except Exception as exc:
            # design_tools raises ToolError with messages meant to be read by
            # the agent; anything else is a bug and gets logged in full.
            if type(exc).__name__ != "ToolError":
                self._log.error("%s raised\n%s", call.tool, traceback.format_exc())
            call.error = str(exc) or type(exc).__name__
        finally:
            call.done.set()
