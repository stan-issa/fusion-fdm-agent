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
│                  ToHTML  │ tools/  (pass 3)│ │
│                          └─────┬────────▲──┘ │
└────────────────────────────────┼────────┼────┘
             NDJSON over stdio   │        │ loopback TCP + token
                                 ▼        │ (pass 3, main-thread queued)
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
settings.json  persisted backend choice
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

## Planned passes

- **Pass 1 (done).** Structure, install, panel, palette, chat UI, sidecar,
  backend seam, echo backend.
- **Pass 2.** The Claude backend on `ClaudeSDKClient`: streaming, session
  continuity, cancellation. The Codex backend only once its CLI surface has been
  read first-hand — the current stub deliberately does not guess at it.
- **Pass 3.** Design read/write. `tools/` in the add-in (`get_design_tree`,
  `get_parameters`, `set_parameter`, `run_fusion_script`), reached over a
  loopback JSON-RPC server bound to `127.0.0.1` on a random port with a
  per-session token. Every call is queued onto the main thread, and every
  mutating one requires explicit confirmation in the palette first.
