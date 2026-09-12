# Wire protocol

Two hops, one vocabulary. A message from the sidecar usually reaches the palette
with only its envelope changed, which keeps the add-in's router thin.

```
palette  ──adsk.fusionSendData(action, json)──►  add-in  ──NDJSON stdin──►  sidecar
palette  ◄──sendInfoToHTML(action, json)──────  add-in  ◄──NDJSON stdout──  sidecar
```

Both hops carry `(action, payload)`. Over stdio the action rides inside the
object as `"action"`; across the palette boundary it is a separate argument,
because that is the shape Fusion's API imposes.

## Palette → add-in

| action | payload | meaning |
| --- | --- | --- |
| `ready` | `{}` | The page loaded and wants current state. Starts the sidecar if it is not already running. |
| `send` | `{turnId, text}` | Begin a turn. `turnId` is minted by the palette and is unique per page load. |
| `cancel` | `{turnId}` | Abort the named turn. |
| `setBackend` | `{backend}` | Switch backends and persist the choice. |
| `approvalReply` | `{id, allow}` | The user's answer to an approval card. |
| `restart` | `{}` | Kill and respawn the sidecar. |
| `rulesList` | `{}` | Ask for the rule catalogue and its current settings. |
| `rulesCheck` | `{requestId, rules?, params?}` | Run the rules over the open design. The panel always names them explicitly — its ticks are what the user is looking at, so they decide what runs rather than the stored defaults. Omitting `rules`, as the agent does, runs every enabled one. |
| `rulesApply` | `{requestId, findingIds[]}` | Apply the ticked findings. |
| `rulesReveal` | `{findingId}` | Select that finding's geometry in the canvas. |
| `rulesIgnore` | `{findingId, ignore}` | Mark the geometry so future checks pass over it. |
| `rulesSetParams` | `{rule, enabled?, params?}` | Persist a rule's settings. |

The `rules*` verbs never reach the sidecar: the rules run inside the add-in, so
the palette is talking to Fusion directly. They also carry no `turnId` --
they are not agent turns, and borrowing one would break the "exactly one
`turnEnd`" invariant the composer depends on.

Every call returns `{"ok": true}` through `returnData`. The palette does not act
on it — real answers come back asynchronously — but a malformed response is a
useful signal while debugging.

## Add-in → palette

| action | payload | meaning |
| --- | --- | --- |
| `state` | `{backend, backends[], status, detail, workspace}` | Full state; replaces whatever the page was showing. `status` is `starting`, `ready` or `error`. |
| `delta` | `{turnId, text}` | A fragment of the reply. Append it. |
| `toolUse` | `{turnId, name, input}` | The agent invoked a tool. Rendered now, populated in pass 3. |
| `turnEnd` | `{turnId, error?}` | The turn is over. Exactly one per turn, including on failure — the composer stays disabled until it arrives. |
| `approval` | `{id, tool, input}` | The agent wants to change the document. Render a card with Allow and Deny. |
| `log` | `{level, message}` | Diagnostic, forwarded to the browser console. |
| `rules` | `{rules[]}` | The rule catalogue. Answers `rulesList` and `rulesSetParams`. |
| `rulesResult` | `{checkId, revision, buildDirection, findings[], notes[], errors[], state}` | What a check found. Replaces the panel's list wholesale. |
| `rulesApplied` | `{...rulesResult, outcomes[], appliedCount}` | What an apply did, *plus* the re-checked model. |
| `rulesRevealed` | `{id, selected}` | A finding was selected in the canvas. |

`backends[]` entries are `{name, label, available, version, detail}`.

`findings[]` entries are `{id, rule, title, detail?, body?, fix?, fixable,
skipped?: [{what, why}]}`. A finding's `id` is `<rule>:<hash of its geometry>`,
so the same edge keeps the same id across checks — which is what lets a ticked
box survive a re-check, and what lets two rules aimed at the same feature
recognise each other by their shared suffix.

`buildDirection` is `{vector, source, label, body}` where `source` is
`selection`, `inferred` or `default`. Every finding from a rule that needs an
orientation is only as good as this, so it travels with them rather than being
assumed.

`outcomes[]` entries are `{id, status, message?}` with `status` one of
`applied`, `failed` or `skipped`.

## Add-in ↔ sidecar

The sidecar accepts `send`, `cancel` and `setBackend` with the same payloads as
above, and emits `delta`, `toolUse`, `turnEnd` and `log` unchanged. Two messages
exist only on this hop:

| action | payload | meaning |
| --- | --- | --- |
| `ready` | `{backend, backends[]}` | Sidecar finished probing backends and is accepting turns. |
| `state` | `{backend, backends[]}` | Backend selection changed. |
| `approvalRequest` | `{id, tool, input}` | Reaches the palette as `approval`. |
| `approvalResponse` | `{id, allow}` | Inbound; resolves the waiting call. |

`sidecarExit` is synthesised by the add-in, not sent by the sidecar — it is how
an unexpected death becomes a visible error in the palette.

## Invariants

- **Exactly one `turnEnd` per `turnId`.** A backend that returns without emitting
  one leaves the palette's composer disabled for good.
- **`turnId` is opaque to everything but the palette.** It is only ever compared
  for equality, which is what lets the palette discard deltas from a turn the
  user already cancelled.
- **Every approval request gets exactly one answer.** The sidecar denies on
  timeout and on turn cancellation, so a tool call never waits forever; the
  palette marks any card still open at `turnEnd` as expired, so its buttons
  cannot lie about being live.
- **Exactly one reply per `requestId`**, including on failure — `rulesResult`
  for a check, `rulesApplied` for an apply. The panel disables its buttons
  while a request is out, so a request that never comes back disables them for
  good. This is the same reasoning as `turnEnd`.
- **An apply answers with the re-checked model, not a diff.** Every feature
  regenerates the body and invalidates the findings around it, so the only
  honest answer is a fresh look.
- **Unknown actions are logged and ignored**, never fatal. Both ends are versioned
  by deployment, not by handshake, so tolerating an unrecognised verb is what
  keeps a newer sidecar usable with an older palette.

## Add-in ↔ Fusion tool server

A third hop, on a loopback TCP socket rather than stdio, because it needs a
*reply*: the sidecar's tool call blocks until Fusion has run it.

Request (one JSON object per line, one call per connection):

```json
{"token": "<per-session hex>", "tool": "get_parameters", "input": {}}
```

Response:

```json
{"ok": true, "result": {...}}
{"ok": false, "error": "No parameter named 'wall'."}
```

The token is generated when the add-in starts and passed to the sidecar in
`FDM_AGENT_TOOL_TOKEN`, alongside `FDM_AGENT_TOOL_PORT`. It is compared with
`secrets.compare_digest`. A port on localhost is reachable by every process on
the machine, so the token is the only thing separating the user's CAD model
from anything else running as them.
