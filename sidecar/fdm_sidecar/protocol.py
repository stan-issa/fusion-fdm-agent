"""Wire format shared by the add-in and the sidecar.

One JSON object per line in each direction. The verbs deliberately match the
palette's own protocol so a message can cross both hops largely unchanged --
see docs/PROTOCOL.md.
"""

# Add-in -> sidecar
IN_SEND = "send"
IN_CANCEL = "cancel"
IN_SET_BACKEND = "setBackend"
IN_APPROVAL_RESPONSE = "approvalResponse"

# Sidecar -> add-in
OUT_READY = "ready"
OUT_STATE = "state"
OUT_DELTA = "delta"
OUT_TOOL_USE = "toolUse"
OUT_TURN_END = "turnEnd"
OUT_LOG = "log"
OUT_APPROVAL_REQUEST = "approvalRequest"


def ready(backend, backends):
    return {"action": OUT_READY, "backend": backend, "backends": backends}


def state(backend, backends):
    return {"action": OUT_STATE, "backend": backend, "backends": backends}


def delta(turn_id, text):
    return {"action": OUT_DELTA, "turnId": turn_id, "text": text}


def tool_use(turn_id, name, tool_input):
    return {
        "action": OUT_TOOL_USE,
        "turnId": turn_id,
        "name": name,
        "input": tool_input,
    }


def turn_end(turn_id, error=None):
    message = {"action": OUT_TURN_END, "turnId": turn_id}
    if error:
        message["error"] = error
    return message


def log(level, message):
    return {"action": OUT_LOG, "level": level, "message": message}


def approval_request(request_id, tool, tool_input):
    return {
        "action": OUT_APPROVAL_REQUEST,
        "id": request_id,
        "tool": tool,
        "input": tool_input,
    }
