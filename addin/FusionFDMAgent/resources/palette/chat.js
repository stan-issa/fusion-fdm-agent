/*
 * Chat palette front end.
 *
 * Transport (see docs/PROTOCOL.md):
 *   JS  -> Python   adsk.fusionSendData(action, JSON.stringify(payload))
 *   Python -> JS    window.fusionJavaScriptHandler.handle(action, jsonString)
 *
 * The `adsk` global only exists inside a Fusion palette, so every call is
 * guarded -- that lets this page be opened in an ordinary browser while
 * working on the styling.
 */
(function () {
  "use strict";

  var el = {
    backend: document.getElementById("backend"),
    status: document.getElementById("status"),
    statusText: document.getElementById("status-text"),
    detail: document.getElementById("detail"),
    log: document.getElementById("log"),
    empty: document.getElementById("empty"),
    composer: document.getElementById("composer"),
    input: document.getElementById("input"),
    send: document.getElementById("send"),
    cancel: document.getElementById("cancel")
  };

  var activeTurn = null;   // id of the turn currently streaming
  var activeBubble = null; // element collecting that turn's deltas
  var turnCounter = 0;

  /* -- transport ------------------------------------------------------- */

  function toPython(action, payload) {
    if (typeof adsk === "undefined" || !adsk.fusionSendData) {
      console.info("[fdm-agent] not in Fusion; dropped", action, payload);
      return;
    }
    adsk.fusionSendData(action, JSON.stringify(payload || {}));
  }

  // Fusion calls this for every palette.sendInfoToHTML.
  window.fusionJavaScriptHandler = {
    handle: function (action, data) {
      try {
        var payload = data ? JSON.parse(data) : {};
        route(action, payload);
      } catch (err) {
        console.error("[fdm-agent] handle failed", action, err);
        return "FAILED: " + err;
      }
      return "OK";
    }
  };

  function route(action, payload) {
    switch (action) {
      case "state":   applyState(payload); break;
      case "delta":   appendDelta(payload); break;
      case "toolUse": appendTool(payload); break;
      case "turnEnd": endTurn(payload); break;
      case "log":     console.log("[sidecar]", payload.level, payload.message); break;
      default:        console.warn("[fdm-agent] unknown action", action);
    }
  }

  /* -- rendering ------------------------------------------------------- */

  function applyState(state) {
    el.status.dataset.state = state.status || "starting";
    el.statusText.textContent = {
      starting: "Starting…",
      ready: "Ready",
      error: "Error"
    }[state.status] || state.status || "";

    if (state.detail) {
      el.detail.textContent = state.detail;
      el.detail.hidden = false;
    } else {
      el.detail.hidden = true;
    }

    var backends = state.backends || [];
    // Rebuilding wholesale is fine: the list is tiny and changes rarely.
    el.backend.innerHTML = "";
    backends.forEach(function (backend) {
      var option = document.createElement("option");
      option.value = backend.name;
      option.textContent = backend.available
        ? backend.label
        : backend.label + " (unavailable)";
      option.disabled = !backend.available;
      option.title = backend.detail || "";
      el.backend.appendChild(option);
    });
    if (state.backend) {
      el.backend.value = state.backend;
    }
    el.backend.disabled = backends.length === 0;
    el.send.disabled = state.status !== "ready";
  }

  function bubble(kind, text) {
    if (el.empty) {
      el.empty.remove();
      el.empty = null;
    }
    var node = document.createElement("div");
    node.className = "msg " + kind;
    if (text) {
      node.textContent = text;
    }
    el.log.appendChild(node);
    scrollToEnd();
    return node;
  }

  function scrollToEnd() {
    el.log.scrollTop = el.log.scrollHeight;
  }

  function appendDelta(payload) {
    if (payload.turnId !== activeTurn) {
      return; // a stale turn, e.g. deltas arriving after a cancel
    }
    if (!activeBubble) {
      activeBubble = bubble("agent streaming", "");
    }
    activeBubble.textContent += payload.text || "";
    scrollToEnd();
  }

  function appendTool(payload) {
    if (payload.turnId !== activeTurn) {
      return;
    }
    var node = bubble("tool", "");
    var name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = payload.name || "tool";
    node.appendChild(name);
    if (payload.input !== undefined) {
      node.appendChild(document.createTextNode(" " + summarise(payload.input)));
    }
    // A later delta starts a fresh bubble rather than resuming the one before
    // the tool call, so that bubble is finished and must lose its caret.
    if (activeBubble) {
      activeBubble.classList.remove("streaming");
    }
    activeBubble = null;
    scrollToEnd();
  }

  function summarise(value) {
    var text = typeof value === "string" ? value : JSON.stringify(value);
    return text.length > 300 ? text.slice(0, 300) + "…" : text;
  }

  function endTurn(payload) {
    if (payload.turnId !== activeTurn) {
      return;
    }
    // Belt and braces: any bubble still carrying a caret belongs to a turn
    // that is now over.
    el.log.querySelectorAll(".streaming").forEach(function (node) {
      node.classList.remove("streaming");
    });
    if (payload.error) {
      bubble("error", payload.error);
    }
    activeTurn = null;
    activeBubble = null;
    setBusy(false);
  }

  function setBusy(busy) {
    el.send.hidden = busy;
    el.cancel.hidden = !busy;
    el.input.disabled = busy;
    if (!busy) {
      el.input.focus();
    }
  }

  /* -- input ----------------------------------------------------------- */

  function submit() {
    var text = el.input.value.trim();
    if (!text || activeTurn) {
      return;
    }
    turnCounter += 1;
    activeTurn = "t" + turnCounter;
    activeBubble = null;

    bubble("user", text);
    el.input.value = "";
    autoGrow();
    setBusy(true);
    toPython("send", { turnId: activeTurn, text: text });
  }

  function autoGrow() {
    el.input.style.height = "auto";
    el.input.style.height = el.input.scrollHeight + "px";
  }

  el.composer.addEventListener("submit", function (event) {
    event.preventDefault();
    submit();
  });

  el.input.addEventListener("input", autoGrow);

  el.input.addEventListener("keydown", function (event) {
    // Enter sends; Shift+Enter inserts a newline.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  });

  el.cancel.addEventListener("click", function () {
    if (activeTurn) {
      toPython("cancel", { turnId: activeTurn });
    }
  });

  el.backend.addEventListener("change", function () {
    toPython("setBackend", { backend: el.backend.value });
  });

  toPython("ready", {});
  el.input.focus();
}());
