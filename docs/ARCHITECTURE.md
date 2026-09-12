# Architecture

## The constraints that decide everything

Three facts about the host shape this design more than any preference does.

1. **Fusion's embedded Python has no package manager.** There is no supported way
   to install dependencies into it, so the add-in is stdlib-only: `subprocess`,
   `threading`, `queue`, `json`.
2. **Fusion's Python is the UI thread.** Anything that blocks it freezes the
   application, and Fusion API objects may only be touched from that thread.
3. **The Claude CLI has no persistent conversation mode.** `claude -p` is
   one-shot; multi-turn continuity means a new process with `--resume` each time.
   Holding one long-lived streaming session *and* exposing host functions as
   tools is what the Agent SDK's `ClaudeSDKClient` is for.

Constraint 3 collides with constraint 1: the SDK is a pip package needing
asyncio and Python ≥3.10, so it cannot live in the add-in. That is the whole
reason for a separate sidecar process — not layering for its own sake.

The collision turns out to be a gift. Because the SDK runs in a normal Python
process, the agent's tools can be ordinary in-process SDK tools created with
`create_sdk_mcp_server`. No separate MCP bridge process is needed; the tools
simply call back into Fusion over a loopback socket.

## Shape

```
┌─ Fusion process ─────────────────────────────┐
│  Palette (Chromium)   Add-in (embedded 3.14) │
│  ┌──────────────┐     ┌────────────────────┐ │
│  │ chat UI      │◄───►│ ui.py    panel/btn │ │
│  │ index.html   │fusion│ bridge.py  router │ │      stdlib only —
│  │ chat.js      │SendData sidecar.py proc  │ │      no pip packages
│  └──────────────┘sendInfo│ events.py  pump │ │
│                  ToHTML  │ toolserver.py   │ │
│                          └─────┬────────▲──┘ │
└────────────────────────────────┼────────┼────┘
             NDJSON over stdio   │        │ loopback TCP + token
                                 ▼        │ (main-thread queued)
        ┌────────────────────────────────────────┐
        │ sidecar  (~/.fusion-fdm-agent/venv)    │
        │  asyncio + claude-agent-sdk            │
        │  ClaudeSDKClient  ·  SDK MCP tools ────┘
        │  backends/{claude,codex,echo}          │
        └────────────────────────────────────────┘
```

## Threading, and the one trick worth knowing

Four threads exist inside Fusion: the main thread, the sidecar's stdout reader,
its stderr drain, and whatever Fusion does internally. Only the main thread may
call into the Fusion API.

`Application.fireCustomEvent` is the sanctioned way back to the main thread, but
firing it once per streamed token does not work — Fusion coalesces rapid custom
events and deltas vanish. So `lib/events.py` **separates payload from signal**:
producers push onto a `queue.Queue` and fire a contentless wake-up, and the
handler drains the *entire* queue on each wake-up. A coalesced wake-up then costs
nothing, because the next drain collects everything that piled up.

This is the piece most likely to be "simplified" by someone who has not seen it
fail. It should not be.

The stderr drain is the other non-obvious thread. An unread pipe fills its OS
buffer and then blocks the child process forever, so the sidecar's stderr is
consumed even though nothing but the log file cares about it.

## Teardown

Fusion keeps panels, command definitions, palettes and custom events alive across
an add-in stop. Anything left behind makes the next start fail on a duplicate ID,
and the failure surfaces far from its cause. So:

- `stop()` removes everything it can and never raises.
- `start()` *also* clears leftovers before creating its own objects, which
  recovers an install that was interrupted mid-teardown.
- Event handler objects are held in a module-level list. Fusion holds handlers
  only weakly; a local goes out of scope, gets collected, and the event silently
  stops firing. This is the classic Fusion add-in bug.

## Why the backend seam sits in the sidecar

Backends are async, talk to subprocesses, and will eventually own MCP tool
definitions. All of that is natural in a normal Python process and awkward in
Fusion's. The add-in therefore knows only *that* there are backends, never how
any of them works — it forwards a name and renders a list.

## Where state lives

Nothing mutable lives in the installed add-in directory: `install.sh` rsyncs over
it with `--delete`, so anything written there is destroyed on the next sync.
User-level state is under `~/.fusion-fdm-agent/`:

```
venv/          sidecar virtualenv
workspace/     the agent's working directory
logs/          addin.log, sidecar.log
settings.json  backend choice and per-backend options
```

## Installation is a copy, not a symlink

A symlinked add-in folder looks like the obvious dev loop, and it is a trap.
Fusion canonicalizes the link and records the *resolved* path in its registry
(`JSLoadedScriptsinfo`) as a separate add-in, with `runOnStartup` inherited from
the manifest. The Add-Ins list then shows two rows, and after the next restart
both run — two instances racing to create the same command definition, toolbar
panel and palette IDs, each one's `start()` deleting the other's UI.

So `install.sh` copies, `doctor.sh` fails loudly on a symlinked install or a
duplicate registry entry, and `fix-duplicate-registration.sh` cleans up a
registry that already has one.

## The Claude backend

Three choices in `backends/claude_code.py` are load-bearing.

**One long-lived `ClaudeSDKClient`, not `claude -p`.** Print mode is one-shot:
continuing a conversation means a fresh process with `--resume` every turn. A
persistent client *is* the session, and it is also the only route to in-process
tools — which is what pass 3 needs.

**`include_partial_messages=True`.** This turns on token-level `StreamEvent`
deltas. Without it the reply arrives only as complete blocks and the palette's
streaming is decorative. The backend still keeps the complete `TextBlock`s as a
fallback and emits them if no deltas ever arrived, so a change in SDK behaviour
degrades to a slow reply rather than a silent one.

**`permission_mode="dontAsk"`.** Pre-approved tools run; anything else is
denied. The tempting `"default"` blocks waiting for an approval that, in a
headless sidecar, nobody can give — the turn would hang with no error at all.

A backend that probes as available can still fail to connect, most obviously
when Claude Code is not signed in. The sidecar catches that, marks the backend
unavailable with the reason, and falls back to the stub, so the panel stays
usable and explains itself instead of dying.

## Design access

The agent runs in the sidecar; only the add-in can touch Fusion, and only on its
main thread. So there are three hops, and the third is the interesting one.

```
model ──► SDK tool (in the sidecar) ──► loopback TCP ──► add-in ──► pump ──► main thread
```

Because the SDK runs in a normal Python process, the Fusion operations are
registered with `create_sdk_mcp_server` as ordinary in-process tools. There is
no MCP server process to launch, supervise or authenticate — the whole category
of problem disappears. This is the payoff for the sidecar split that constraint
1 forced on us.

**The loopback hop needs a reply**, unlike the stdio protocol, which is
fire-and-forget in both directions. So the tool server posts a `ToolCall` onto
the same pump and blocks its socket thread on an `Event` until the main thread
fills in the result. Two kinds of traffic therefore share the pump, and
`_on_pumped` routes on type.

**The socket is the security boundary.** It binds `127.0.0.1` on an ephemeral
port, but a localhost port is reachable by every process running as the user,
so a per-session token — generated at add-in startup, handed to the sidecar in
its environment, compared with `secrets.compare_digest` — is what actually
separates the user's CAD model from anything else on the machine.

**Calls time out.** A wedged or modal Fusion would otherwise hold the agent
indefinitely; after 120s the caller gets an error saying Fusion may be busy.

### Approval

`set_parameter` and `run_fusion_script` change the user's document, so the
backend's `can_use_tool` callback gates exactly those two — reads are allowed
outright, since prompting for them would only train the user to click through.

The prompt travels sidecar → add-in → palette → user → back, with the callback
parked on a Future. The failure modes are the point: a prompt nobody answers
denies after five minutes, and cancelling the turn denies everything
outstanding. Either way the agent gets a refusal it can report, rather than a
turn that never ends.

The card shows the request **verbatim** — the script as code, never a summary.
A paraphrase the user approves is not an approval of what actually runs.

`run_fusion_script` executes arbitrary Python inside the add-in's process with
the full API. That is deliberate: it is what makes the agent able to do real
work rather than only nudge parameters. The mitigations are the approval gate,
the script being shown in full, and Fusion's own timeline and undo.

## The rules

`run_fusion_script` can do anything, which makes it the wrong tool for a check
you want to run twice. The model would write fresh BRep Python for every part,
and the user would read a wall of generated code to find out whether their
part has an elephant's-foot problem. So the printability checks are ordinary
Python modules under `lib/rules/`, and the agent orchestrates them rather than
reimplementing them.

A rule is two functions and a few constants: `detect(context, params)` returns
findings, `apply(context, findings, params)` fixes them. Nothing more
ceremonious, because a rule library only grows if adding one is cheap.

### The split that makes them testable

`geometry.py` and `signature.py` import nothing from Fusion and reason about
plain numbers; `fusion_geom.py` does nothing *but* turn Fusion objects into
those numbers. Every subtle thing — teardrop tangency, the thin-wall
measurement, id stability — lives on the pure side and is unit-tested there.
The impure side is thin enough to read.

### Findings are identified by their geometry

A finding's id is `<rule>:<hash of a quantised description of the geometry>`.
Numbering findings in discovery order would have been simpler and wrong: a
re-check reorders them, and the box the user ticked silently becomes a
different edge.

Hashing the geometry buys three things at once. Ticks survive a re-check.
Re-resolution after a fix needs no separate mechanism — re-run detection and
look the id up, and an id that no longer appears describes geometry that has
genuinely gone. And two rules aimed at the same feature produce the same
suffix, which is how the session notices that a teardrop and a lead-in are
fighting over one bore.

### Applying, and the order it happens in

Every feature regenerates the body and invalidates every other edge reference
held from detection. Two things follow.

**Batching is correctness, not tidiness.** Chamfering forty footprint edges as
forty features would leave thirty-nine of them pointing at edges that no longer
exist. One feature over an `ObjectCollection` of all of them sidesteps the
problem; one timeline node and one undo step are a bonus. When Fusion rejects
the batch — and it will not say which edge caused it — the fallback retries one
finding at a time, re-resolving between each, so the fixable ones still land.

**`RULES` is ordered most-destructive first**, and that order is the order
fixes are applied in. Teardropping a bore destroys the entrance ring a lead-in
would have chamfered; chamfering the footprint rewrites every edge of the
bottom face. Going the other way round would leave the later rule aiming at
geometry the earlier one had already rewritten.

Afterwards the session re-checks and returns *that*, rather than crossing items
off a list it can no longer vouch for.

### A feature that was created is not a feature that worked

Fusion returns an errored feature rather than raising when a chamfer cannot be
built, and leaves a red mark in the browser. Everything the rules create is
checked against `healthState` and rolled back if it is broken. Reporting
"applied" for a failed fix is the one outcome worse than reporting the failure.

### Which way is up

Two of the three rules are meaningless without a build direction. It comes
from the user's selected face if there is one, otherwise from the largest
downward-facing planar face at the bottom of the body, otherwise from +Z. The
answer always carries *which* of those it was, because a finding is only as
good as the orientation behind it, and an assumption the user can see is an
assumption they can correct.

Outward normals are the trap here. Neither `face.geometry.normal` nor the
face's evaluator answers "which way is out of the solid" — both describe the
underlying surface, whose parameterisation may run opposite to the face using
it. `isParamReversed` reconciles them and `pointContainment` confirms it.

### Selection has to be captured, not read

Reading `ui.activeSelections` when a tool runs does not work: clicking into the
palette to type moves focus, and Fusion clears the selection on the way. By the
time "chamfer this face" reaches a tool, the face is no longer selected. So
`lib/selection.py` watches `activeSelectionChanged` and keeps the last
*non-empty* selection, and tools read that. Emptying the selection is not
recorded — it is almost always the side effect of clicking elsewhere, not an
instruction.

### Two ways to apply, one session

The Rules tab and the agent's tools are two front ends onto the same
`RuleSession`, reached through `lib/rules_controller.py`. If each kept its own
state they would disagree the moment either was used.

Ticking findings in the panel and pressing Apply *is* the approval; there is no
card, because the user has already seen exactly what they selected and asking
twice only teaches people to click through. The agent's path still raises one —
and because the request on the wire is a list of opaque ids, `bridge.py`
enriches it from the session so the card can say "Chamfer 0.3 mm on 7
bed-contact edges of Body1". The ids stay in the payload: the card explains the
request, it does not replace it.

### What the rules refuse to do

Conservative by design, because a wrong fix is worse than a missing one. A bore
that is not a complete cylinder is reported, not teardropped — something
crosses it and where the roof belongs is ambiguous. A bore sharing its axis
with another is treated as a seat. Threaded faces are excluded outright. And
because no amount of geometry can distinguish a bearing seat from a clearance
hole, `Ignore` writes a Fusion attribute on the entity, which travels with the
document rather than living in a settings file that knows nothing about which
model it describes.

Every skipped edge carries its reason to the panel, and a rule that finds
nothing says what it examined. A guard that drops geometry silently is
indistinguishable from a bug — which is not a hypothetical: teardrop shipped
with a 6 mm minimum diameter that excluded every fastener clearance hole on a
normal part, and said nothing at all about doing so.

A finding that cannot be fixed still carries geometry to *show*. It has no
`entities`, because there is no fix for them to act on, but its `reveal` list
points at the bore or the face anyway — "which hole do you mean?" is the first
thing anyone reads an unfixable finding and wonders.

Rules also have to cope with each other's output. A hole that has been given
lead-ins no longer opens onto a flat face at either end; it opens into a
chamfer cone, and the flat face is one step beyond. Teardrop walks across the
cone to find it, and measures its cut from that plane rather than from the
bore's end ring. Treating the cone as a dead end made every hole the other
rule had already improved look impossible to fix.

## Planned passes

- **Pass 1 (done).** Structure, install, panel, palette, chat UI, sidecar,
  backend seam, echo backend.
- **Pass 2 (done).** The Claude backend on `ClaudeSDKClient`: streaming,
  session continuity, cancellation via `interrupt()`, tool-use display.
- **Pass 3 (done).** Design read/write over the loopback tool server, with
  per-call approval for anything that mutates the document.
- **Pass 4 (done).** Selection awareness, the printability rules engine, and
  the Rules tab: `bed_chamfer`, `hole_lead_in`, `teardrop_bore`.
- **Codex.** Only once its CLI surface has been read first-hand; the current
  stub deliberately does not guess at it.
- **Next, most likely.** More rules — overhang angles, wall thickness against
  nozzle diameter, unsupported bridges. Bores split into several faces by a
  STEP import, which all three rules currently pass over. Export for slicing.
  And letting the agent read back the result of its own script rather than
  assuming it worked.
