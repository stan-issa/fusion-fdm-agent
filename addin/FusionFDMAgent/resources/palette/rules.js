/*
 * The Rules tab: check the open design against the printability rules, then
 * apply the fixes you want.
 *
 * This view owns the tab strip as well as its own panel, because the tabs
 * exist only to make room for it -- chat.js owns the transport and the
 * conversation, and nothing else needs to know a second view arrived.
 *
 * Ticking findings and pressing Apply *is* the approval. There is no card:
 * the card exists for the agent's path, where the user has not already seen
 * what is about to change. Asking twice would only teach people to click
 * through.
 */
(function () {
  "use strict";

  var bridge = window.fdm;
  if (!bridge) {
    return;
  }

  var el = {
    tabs: Array.prototype.slice.call(document.querySelectorAll(".tab")),
    views: {
      chat: document.getElementById("view-chat"),
      rules: document.getElementById("view-rules")
    },
    check: document.getElementById("rules-check"),
    list: document.getElementById("rules-list"),
    toggleAll: document.getElementById("findings-toggle-all"),
    selected: document.getElementById("rules-selected"),
    buildDir: document.getElementById("build-dir"),
    notes: document.getElementById("rules-notes"),
    findings: document.getElementById("rules-findings"),
    apply: document.getElementById("rules-apply"),
    count: document.getElementById("rules-count")
  };

  var catalogue = [];
  // Which rules have their parameters open. Kept here rather than read off
  // the DOM, because the list is rebuilt whenever a setting is saved.
  var expanded = {};
  var findings = [];
  // Finding ids are hashes of geometry, not positions in a list, so a tick
  // survives a re-check: the same edge keeps the same id. That is the whole
  // reason the ids are built the way they are.
  var ticked = {};
  var pending = null;
  var requestCounter = 0;
  var loaded = false;

  /* -- tabs ------------------------------------------------------------- */

  function show(name) {
    el.tabs.forEach(function (tab) {
      var active = tab.dataset.view === name;
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    Object.keys(el.views).forEach(function (key) {
      if (el.views[key]) {
        el.views[key].hidden = key !== name;
      }
    });
    if (name === "rules" && !loaded) {
      loaded = true;
      bridge.send("rulesList", {});
    }
  }

  el.tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      show(tab.dataset.view);
    });
  });

  /* -- requests --------------------------------------------------------- */

  function request(action, payload) {
    if (pending) {
      return;
    }
    requestCounter += 1;
    pending = "r" + requestCounter;
    setBusy(true);
    payload = payload || {};
    payload.requestId = pending;
    bridge.send(action, payload);
  }

  function settle() {
    pending = null;
    setBusy(false);
  }

  function setBusy(busy) {
    el.check.textContent = busy ? "Checking…" : "Check model";
    updateCheck();
    updateApply();
  }

  /* -- incoming --------------------------------------------------------- */

  bridge.on("rules", function (payload) {
    if (payload.error) {
      settle();
      renderError(payload.error);
      return;
    }
    catalogue = payload.rules || [];
    renderRules();
  });

  bridge.on("rulesResult", function (payload) {
    settle();
    if (payload.error) {
      renderError(payload.error);
      return;
    }
    absorb(payload);
    render();
  });

  bridge.on("rulesApplied", function (payload) {
    settle();
    if (payload.error) {
      renderError(payload.error);
      return;
    }
    // Applying returns the *re-checked* model, not a diff, because every fix
    // invalidates the findings around it.
    absorb(payload);
    render(payload.outcomes || []);
  });

  bridge.on("rulesRevealed", function (payload) {
    if (payload.error) {
      renderError(payload.error);
    }
  });

  function absorb(payload) {
    findings = payload.findings || [];
    renderBuildDirection(payload.buildDirection, payload.state);
    renderNotes(payload.notes || []);
    // Drop ticks for findings that no longer exist, so the count cannot claim
    // more than the list shows.
    var live = {};
    findings.forEach(function (finding) {
      if (ticked[finding.id]) {
        live[finding.id] = true;
      }
    });
    ticked = live;
  }

  /* -- rendering -------------------------------------------------------- */

  function renderBuildDirection(direction, state) {
    if (!direction) {
      el.buildDir.hidden = true;
      return;
    }
    el.buildDir.textContent = "Building along " + direction.label;
    el.buildDir.dataset.source = direction.source;
    el.buildDir.hidden = false;

    if (state && state.timelineAtEnd === false) {
      note(el.buildDir, "The timeline marker is not at the end; fixes cannot be applied.");
    }
  }

  function note(node, text) {
    var extra = document.createElement("span");
    extra.className = "warn";
    extra.textContent = " " + text;
    node.appendChild(extra);
  }

  function renderNotes(notes) {
    el.notes.innerHTML = "";
    el.notes.hidden = notes.length === 0;
    notes.forEach(function (text) {
      var line = document.createElement("p");
      line.textContent = text;
      el.notes.appendChild(line);
    });
  }

  function renderError(message) {
    el.findings.innerHTML = "";
    var box = document.createElement("div");
    box.className = "msg error";
    box.textContent = message;
    el.findings.appendChild(box);
    updateApply();
  }

  function render(outcomes) {
    el.findings.innerHTML = "";

    if (outcomes && outcomes.length) {
      el.findings.appendChild(summarise(outcomes));
    }

    if (!findings.length) {
      var empty = document.createElement("div");
      empty.className = "empty";
      empty.innerHTML = "<p>Nothing to fix.</p>";
      el.findings.appendChild(empty);
      updateApply();
      return;
    }

    byRule().forEach(function (group) {
      el.findings.appendChild(renderGroup(group));
    });
    updateApply();
  }

  function byRule() {
    var groups = [];
    var index = {};
    findings.forEach(function (finding) {
      if (!index[finding.rule]) {
        index[finding.rule] = { rule: finding.rule, items: [] };
        groups.push(index[finding.rule]);
      }
      index[finding.rule].items.push(finding);
    });
    return groups;
  }

  function titleOf(ruleId) {
    for (var i = 0; i < catalogue.length; i += 1) {
      if (catalogue[i].id === ruleId) {
        return catalogue[i].title;
      }
    }
    return ruleId;
  }

  function renderGroup(group) {
    var section = document.createElement("section");
    section.className = "finding-group";

    var heading = document.createElement("h2");
    heading.textContent = titleOf(group.rule);
    section.appendChild(heading);

    group.items.forEach(function (finding) {
      section.appendChild(renderFinding(finding));
    });
    return section;
  }

  function renderFinding(finding) {
    var row = document.createElement("div");
    row.className = "finding";
    if (!finding.fixable) {
      row.classList.add("not-fixable");
    }

    var head = document.createElement("div");
    head.className = "finding-head";

    if (finding.fixable) {
      var box = document.createElement("input");
      box.type = "checkbox";
      box.checked = Boolean(ticked[finding.id]);
      box.id = "f-" + finding.id;
      box.addEventListener("change", function () {
        ticked[finding.id] = box.checked;
        updateApply();
      });
      head.appendChild(box);
    }

    var label = document.createElement(finding.fixable ? "label" : "span");
    label.className = "finding-title";
    if (finding.fixable) {
      label.setAttribute("for", "f-" + finding.id);
    }
    label.textContent = finding.title;
    head.appendChild(label);
    row.appendChild(head);

    if (finding.detail) {
      row.appendChild(line("finding-detail", finding.detail));
    }
    if (finding.fix) {
      row.appendChild(line("finding-fix", finding.fix));
    }
    if (!finding.fixable) {
      row.appendChild(line("finding-detail muted", "Needs doing by hand."));
    }
    if (finding.skipped && finding.skipped.length) {
      row.appendChild(renderSkipped(finding.skipped));
    }
    row.appendChild(renderActions(finding));
    return row;
  }

  function line(className, text) {
    var node = document.createElement("p");
    node.className = className;
    node.textContent = text;
    return node;
  }

  /*
   * Why an edge was left alone matters more than that it was: a guard that
   * drops geometry silently is indistinguishable from a bug, and this is
   * where someone checks whether the thresholds suit their printer.
   */
  function renderSkipped(skipped) {
    var details = document.createElement("details");
    details.className = "skipped";

    var summary = document.createElement("summary");
    summary.textContent = skipped.length === 1
      ? "1 skipped"
      : skipped.length + " skipped";
    details.appendChild(summary);

    var list = document.createElement("ul");
    skipped.forEach(function (entry) {
      var item = document.createElement("li");
      item.textContent = entry.what + " — " + entry.why;
      list.appendChild(item);
    });
    details.appendChild(list);
    return details;
  }

  function renderActions(finding) {
    var actions = document.createElement("div");
    actions.className = "finding-actions";

    var show = document.createElement("button");
    show.type = "button";
    show.className = "linkish";
    show.textContent = "Show";
    show.title = "Select this in the Fusion canvas";
    show.addEventListener("click", function () {
      bridge.send("rulesReveal", { findingId: finding.id });
    });
    actions.appendChild(show);

    var ignore = document.createElement("button");
    ignore.type = "button";
    ignore.className = "linkish";
    ignore.textContent = "Ignore";
    ignore.title = "Never flag this again; remembered with the document";
    ignore.addEventListener("click", function () {
      request("rulesIgnore", { findingId: finding.id, ignore: true });
    });
    actions.appendChild(ignore);

    return actions;
  }

  function summarise(outcomes) {
    var counts = { applied: 0, failed: 0, skipped: 0 };
    outcomes.forEach(function (outcome) {
      if (counts[outcome.status] !== undefined) {
        counts[outcome.status] += 1;
      }
    });

    var box = document.createElement("div");
    box.className = "outcome" + (counts.failed ? " has-failures" : "");
    box.appendChild(line("outcome-head", [
      counts.applied + " applied",
      counts.failed ? counts.failed + " failed" : "",
      counts.skipped ? counts.skipped + " skipped" : ""
    ].filter(Boolean).join(", ")));

    outcomes.forEach(function (outcome) {
      if (outcome.status === "applied" || !outcome.message) {
        return;
      }
      box.appendChild(line("outcome-detail", outcome.message));
    });
    return box;
  }

  function fixable() {
    return findings.filter(function (finding) {
      return finding.fixable;
    });
  }

  function updateApply() {
    var available = fixable();
    var chosen = available.filter(function (finding) {
      return ticked[finding.id];
    });
    el.apply.disabled = chosen.length === 0 || Boolean(pending);
    el.count.textContent = available.length
      ? chosen.length + " of " + available.length + " selected"
      : "";
    el.toggleAll.disabled = available.length === 0 || Boolean(pending);
    el.toggleAll.textContent =
      chosen.length === available.length && available.length
        ? "Select none"
        : "Select all";
  }

  /* -- the rule list ---------------------------------------------------- */

  function picked() {
    return catalogue.filter(function (rule) {
      return rule.enabled !== false;
    });
  }

  function renderRules() {
    el.list.innerHTML = "";
    catalogue.forEach(function (rule) {
      el.list.appendChild(renderRule(rule));
    });
    updateCheck();
  }

  function renderRule(rule) {
    var row = document.createElement("div");
    row.className = "rule";

    var head = document.createElement("div");
    head.className = "rule-head";

    var pick = document.createElement("label");
    pick.className = "rule-pick";

    var box = document.createElement("input");
    box.type = "checkbox";
    box.checked = rule.enabled !== false;
    box.addEventListener("change", function () {
      rule.enabled = box.checked;
      updateCheck();
      // Persisted, so the selection is still there next time the panel opens.
      bridge.send("rulesSetParams", { rule: rule.id, enabled: box.checked });
    });
    pick.appendChild(box);

    var name = document.createElement("span");
    name.className = "rule-name";
    name.textContent = rule.title;
    name.title = rule.description || "";
    pick.appendChild(name);
    head.appendChild(pick);

    var params = document.createElement("div");
    params.className = "rule-params";
    params.hidden = !expanded[rule.id];

    if ((rule.params || []).length) {
      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "linkish rule-options";
      toggle.textContent = "Options";
      toggle.setAttribute("aria-expanded", params.hidden ? "false" : "true");
      toggle.addEventListener("click", function () {
        params.hidden = !params.hidden;
        expanded[rule.id] = !params.hidden;
        toggle.setAttribute("aria-expanded", params.hidden ? "false" : "true");
      });
      head.appendChild(toggle);
    }
    row.appendChild(head);

    if (rule.description) {
      params.appendChild(line("muted", rule.description));
    }
    (rule.params || []).forEach(function (param) {
      params.appendChild(renderParam(rule, param));
    });
    row.appendChild(params);
    return row;
  }

  function renderParam(rule, param) {
    var wrapper = document.createElement("label");
    wrapper.className = "rule-param";

    var name = document.createElement("span");
    name.textContent = param.label + (param.unit ? " (" + param.unit + ")" : "");
    wrapper.appendChild(name);

    var field;
    if (param.choices) {
      field = document.createElement("select");
      param.choices.forEach(function (choice) {
        var option = document.createElement("option");
        option.value = choice;
        option.textContent = choice;
        field.appendChild(option);
      });
      field.value = param.value;
    } else {
      field = document.createElement("input");
      field.type = "number";
      field.step = "0.05";
      if (param.min !== null && param.min !== undefined) { field.min = param.min; }
      if (param.max !== null && param.max !== undefined) { field.max = param.max; }
      field.value = param.value;
    }

    field.addEventListener("change", function () {
      var params = {};
      params[param.name] = param.choices ? field.value : Number(field.value);
      bridge.send("rulesSetParams", { rule: rule.id, params: params });
    });
    wrapper.appendChild(field);
    return wrapper;
  }

  function updateCheck() {
    var chosen = picked().length;
    el.check.disabled = chosen === 0 || Boolean(pending);
    el.selected.textContent = catalogue.length
      ? chosen + " of " + catalogue.length + " rules"
      : "";
  }

  /* -- input ------------------------------------------------------------ */

  el.check.addEventListener("click", function () {
    var chosen = picked().map(function (rule) {
      return rule.id;
    });
    if (chosen.length) {
      // Always explicit. The panel's ticks are what the user is looking at,
      // so they decide what runs rather than the stored defaults.
      request("rulesCheck", { rules: chosen });
    }
  });

  el.toggleAll.addEventListener("click", function () {
    var available = fixable();
    var wanted = available.some(function (finding) {
      return !ticked[finding.id];
    });
    available.forEach(function (finding) {
      ticked[finding.id] = wanted;
      // Set the boxes directly rather than redrawing the list: a re-render
      // would fold away any skip reasons the user had opened to read.
      var box = document.getElementById("f-" + finding.id);
      if (box) {
        box.checked = wanted;
      }
    });
    updateApply();
  });

  el.apply.addEventListener("click", function () {
    var chosen = Object.keys(ticked).filter(function (key) {
      return ticked[key];
    });
    if (chosen.length) {
      request("rulesApply", { findingIds: chosen });
    }
  });

  show("chat");
}());
