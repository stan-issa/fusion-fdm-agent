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

## Planned passes

- **Pass 1 (done).** Structure, install, panel, palette, chat UI, sidecar,
  backend seam, echo backend.
- **Pass 2 (done).** The Claude backend on `ClaudeSDKClient`: streaming,
  session continuity, cancellation via `interrupt()`, tool-use display.
- **Pass 3 (done).** Design read/write over the loopback tool server, with
  per-call approval for anything that mutates the document.
- **Codex.** Only once its CLI surface has been read first-hand; the current
  stub deliberately does not guess at it.
- **Next, most likely.** Selection awareness (`get_selection`, so "this face"
  means something), export for slicing, and letting the agent read back the
  result of its own script rather than assuming it worked.
