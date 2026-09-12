/*
 * Chat palette front end, and the transport both views share.
 *
 * Transport (see docs/PROTOCOL.md):
 *   JS  -> Python   adsk.fusionSendData(action, JSON.stringify(payload))
 *   Python -> JS    window.fusionJavaScriptHandler.handle(action, jsonString)
 *
 * The `adsk` global only exists inside a Fusion palette, so every call is
 * guarded -- that lets this page be opened in an ordinary browser while
 * working on the styling.
 *
 * `window.fdm` is the seam between the two views: this file owns the pipe and
 * the chat, rules.js registers its own verbs on it. Keeping the router a
 * table rather than a switch is what lets a view be added without editing the
 * transport.
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

  /*
   * Fusion injects the `adsk` bridge into the page on its own schedule, which
   * can land after this script runs. Sending once and giving up loses the
   * opening `ready` and leaves the palette stuck on "Starting..." forever, so
   * outbound messages queue until the bridge shows up.
   */
  var bridgeReady = false;
  var pending = [];
  var BRIDGE_POLL_MS = 100;
  var BRIDGE_ATTEMPTS = 100; // ~10s

  function bridgeAvailable() {
    return typeof adsk !== "undefined" && adsk && typeof adsk.fusionSendData === "function";
  }

  function toPython(action, payload) {
    var data = JSON.stringify(payload || {});
    if (bridgeReady) {
      adsk.fusionSendData(action, data);
      return;
    }
    pending.push([action, data]);
  }

  function waitForBridge(attemptsLeft) {
    if (bridgeAvailable()) {
      bridgeReady = true;
      while (pending.length) {
        var message = pending.shift();
        adsk.fusionSendData(message[0], message[1]);
      }
      return;
    }
    if (attemptsLeft <= 0) {
      // Either the page is open outside Fusion, or the bridge genuinely failed.
      // Say so rather than sitting on "Starting..." indefinitely.
      applyState({
        status: "error",
        detail: "No connection to Fusion. If you opened this page in a browser, "
              + "that is expected; inside Fusion, stop and re-run the add-in.",
        backends: []
      });
      return;
    }
    setTimeout(function () {
      waitForBridge(attemptsLeft - 1);
    }, BRIDGE_POLL_MS);
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

  var routes = {};

  function route(action, payload) {
    var handler = routes[action];
    if (handler) {
      handler(payload);
      return;
    }
    // Unknown verbs are logged and ignored, never fatal: the sidecar and the
    // palette are versioned by deployment, not by handshake.
    console.warn("[fdm-agent] unknown action", action);
  }

  function on(action, handler) {
    routes[action] = handler;
  }

  on("state", applyState);
  on("delta", appendDelta);
  on("toolUse", appendTool);
  on("approval", appendApproval);
  on("turnEnd", endTurn);
  on("log", function (payload) {
    console.log("[sidecar]", payload.level, payload.message);
  });

  // The seam rules.js hangs off. Registered before it loads, because script
  // tags run in order and it would otherwise have nothing to register on.
  window.fdm = { send: toPython, on: on, onReady: onReady };

  var readyHandlers = [];

  function onReady(handler) {
    readyHandlers.push(handler);
  }

  function announceReady() {
    readyHandlers.forEach(function (handler) {
      try {
        handler();
      } catch (err) {
        console.error("[fdm-agent] ready handler failed", err);
      }
    });
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

  /*
   * An approval card. This is the one place the user decides whether the
   * agent may change their document, so it shows the request verbatim --
   * scripts as code, never summarised -- rather than a reassuring paraphrase.
   */
  function appendApproval(payload) {
    var node = bubble("approval", "");

    var title = document.createElement("div");
    title.className = "approval-title";
    title.textContent = approvalTitle(payload);
    node.appendChild(title);

    var body = document.createElement("pre");
    body.className = "approval-body";
    body.textContent = approvalBody(payload);
    node.appendChild(body);

    var actions = document.createElement("div");
    actions.className = "approval-actions";

    var allow = document.createElement("button");
    allow.type = "button";
    allow.textContent = "Allow";

    var deny = document.createElement("button");
    deny.type = "button";
    deny.className = "secondary";
    deny.textContent = "Deny";

    function decide(allowed) {
      toPython("approvalReply", { id: payload.id, allow: allowed });
      actions.remove();
      var outcome = document.createElement("div");
      outcome.className = "approval-outcome";
      outcome.textContent = allowed ? "Allowed" : "Denied";
      node.appendChild(outcome);
      node.dataset.resolved = "1";
    }

    allow.addEventListener("click", function () { decide(true); });
    deny.addEventListener("click", function () { decide(false); });
    actions.appendChild(allow);
    actions.appendChild(deny);
    node.appendChild(actions);

    // A later delta belongs in its own bubble, not appended to this card.
    if (activeBubble) {
      activeBubble.classList.remove("streaming");
    }
    activeBubble = null;
    scrollToEnd();
  }

  function approvalTitle(payload) {
    var name = (payload.tool || "").replace(/^mcp__fusion__/, "");
    if (name === "run_fusion_script") {
      return "Run this script in Fusion?";
    }
    if (name === "set_parameter") {
      return "Change a parameter?";
    }
    if (name === "apply_rule_fix") {
      var count = (payload.findings || []).length;
      return count === 1 ? "Apply this fix?" : "Apply " + count + " fixes?";
    }
    return "Allow " + name + "?";
  }

  /*
   * What the request actually is, never a reassuring summary of it. For a
   * rule fix that means the human-readable line the add-in resolved for each
   * finding *and* the id it resolved it from, so the card describes the
   * request rather than replacing it.
   */
  function approvalBody(payload) {
    var input = payload.input || {};
    if (typeof input.code === "string") {
      return input.code;
    }
    if (typeof input.name === "string" && input.expression !== undefined) {
      return input.name + " = " + input.expression;
    }
    if (payload.findings && payload.findings.length) {
      return payload.findings.map(function (finding) {
        if (finding.unknown) {
          return finding.id + "  (no longer in the current results)";
        }
        return [finding.title, finding.fix ? "    " + finding.fix : "",
                "    " + finding.id].filter(Boolean).join("\n");
      }).join("\n\n");
    }
    return JSON.stringify(input, null, 2);
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
    // The sidecar denies anything still outstanding when a turn ends, so a
    // card left with live buttons would be a lie.
    el.log.querySelectorAll(".msg.approval:not([data-resolved])").forEach(
      function (node) {
        var actions = node.querySelector(".approval-actions");
        if (actions) {
          actions.remove();
        }
        node.dataset.resolved = "1";
        var outcome = document.createElement("div");
        outcome.className = "approval-outcome";
        outcome.textContent = "Expired";
        node.appendChild(outcome);
      }
    );
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
  waitForBridge(BRIDGE_ATTEMPTS);
  announceReady();
  el.input.focus();
}());
