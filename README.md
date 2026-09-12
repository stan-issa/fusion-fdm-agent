# Fusion FDM Agent

An Autodesk Fusion add-in with two panels in the Design workspace: a **chat**
wired to an external coding agent (Claude Code, or Codex later), and **Rules** —
a set of FDM printability checks that find problems in the open model and fix
the ones you tick.

Status: **working**. The agent reads the open design and can change it — with
your approval for anything that modifies the document. A stub `echo` backend is
kept for testing the plumbing without a model; Codex is detection-only.

## Rules

Three checks, run against the real geometry rather than by asking a model to
guess:

| Rule | Finds | Fixes |
| --- | --- | --- |
| **Chamfer bed-contact edges** | The outline where the part meets the bed | A chamfer, 0.3 mm by default — skipping edges too close to a neighbour to survive it |
| **Add lead-ins to holes** | Plain cylindrical bores with a bare mouth | A 0.5 mm entrance chamfer; the bore diameter below it is untouched |
| **Teardrop horizontal bores** | Bores running across the build direction (within 30° of horizontal), at any diameter, whose flat roof cannot print | A 45° teardrop roof, tangent to the bore, so the original circular clearance is preserved |
| **Slope ledge undersides** | Straight ledges projecting from a wall, whose flat underside is a 90° overhang | A 45° triangular gusset filling the corner beneath it. The top of the ledge is untouched |
| **Rib long bridges** | Flat bridges held at both ends whose span exceeds what the printer bridges cleanly (20 mm by default) | Thin walls standing on the build plate, dividing the span. Separate named bodies, with a top gap and a grip tab so they snap off |

Tick the rules you want at the top of the panel, press **Check model**, then
tick the findings to fix and press **Apply**. That click is the approval — you
have already seen exactly what you selected. Each rule's **Options** holds its
thresholds; both the ticks and the thresholds are remembered.

Two things the panel always tells you, because findings are worthless without
them. **Which way the part builds**, and whether that came from a face you
selected, an inference, or a fallback guess. And **what each rule deliberately
skipped, with the reason** — a 0.8 mm fin that a 0.3 mm chamfer would destroy
is reported as skipped, not quietly dropped. A rule that finds nothing says
what it looked at and why it passed over it, because "nothing to fix" and
"nothing matched my thresholds" are different answers and only one of them
means you are done.

Some exclusions cannot be read off the geometry: a bearing seat and a clearance
hole are the same cylinder. **Ignore** marks one on the model itself, so it
travels with the document.

Supports arrive as separate bodies named `FDM support 1`, `2`… and marked as
scaffolding, so the other rules step over them rather than offering to chamfer
something you are about to snap off.

The rules are Python modules in `addin/FusionFDMAgent/lib/rules/`; adding one
means writing `detect` and `apply` and nothing else.

## What the agent can do

| Tool | Effect | Approval |
| --- | --- | --- |
| `get_design_tree` | Components, bodies (bounding boxes and volumes in mm), sketch names | no |
| `get_parameters` | User and named model parameters, with expressions and units | no |
| `get_selection` | What you have selected, so "this face" means something | no |
| `list_rules` | The printability rules and their settings | no |
| `check_rules` | Run the rules and return findings, each with a stable id | no |
| `set_parameter` | Change one parameter's expression | **yes** |
| `run_fusion_script` | Execute Python against the live Fusion API | **yes** |
| `apply_rule_fix` | Apply the fixes for named findings | **yes** |

The agent and the panel share one session, so *"check this part for
printability"* in the chat fills in the Rules tab beside it, and a fix you
apply yourself is a fix the agent can see.

Approval is a card in the panel showing the request verbatim — the script as
code, not a summary — with Allow and Deny. Denying tells the agent to ask rather
than retry. Nothing that changes your document happens without you clicking.

`run_fusion_script` runs arbitrary code inside Fusion with the full API, so it
can do anything you could. That is the point of it, and the reason it is gated.
Changes go through Fusion's normal timeline, so undo works as usual — but read
the script before allowing it. `claude.autoApprove` turns the gate off; think
before you set it.

## Requirements

- macOS with Autodesk Fusion installed for the current user
- Python 3.10+ on the PATH (used to build the sidecar venv; Fusion's own
  embedded interpreter is never modified)
- A signed-in Claude Code. The Agent SDK bundles its own binary, so a separate
  CLI install is optional, but you must be **logged in**:

  ```bash
  curl -fsSL https://claude.ai/install.sh | bash   # if you do not have it
  claude                                           # then log in, once
  ```

  Without this the panel connects but every turn ends with
  *"Claude Code is not signed in"*.

## Install

```bash
./scripts/bootstrap.sh   # sidecar venv + ~/.fusion-fdm-agent
./scripts/install.sh     # copy the add-in into Fusion
./scripts/doctor.sh      # verify everything above
./scripts/test.sh        # test suite, no Fusion needed
```

Then in Fusion: **Utilities → Add-Ins** (`Shift+S`) → `FusionFDMAgent` → **Run**.
A *FDM Agent* panel appears on the Design toolbar; its button opens the chat,
docked to the right.

`./scripts/uninstall.sh` removes it again.

## How it fits together

```
palette (chat UI) ──► add-in (Fusion's Python) ──► sidecar (venv) ──► agent
```

The add-in is **stdlib-only**, because Fusion's embedded Python has no package
manager. Anything needing dependencies — the Claude Agent SDK above all — lives
in the sidecar, a separate process speaking newline-delimited JSON over stdio.
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains why that split is forced
rather than chosen, and [docs/PROTOCOL.md](docs/PROTOCOL.md) documents the wire
format.

## Configuration

`~/.fusion-fdm-agent/settings.json` is shared by the add-in and the sidecar. It
is written when you change the backend in the panel, and can be edited by hand:

```json
{
  "backend": "claude",
  "claude": {
    "model": null,
    "allowBash": false,
    "systemPromptExtra": "",
    "autoApprove": false
  }
}
```

- `backend` — `claude`, `codex` or `echo`. Also set by the panel's dropdown.
- `claude.model` — a model id, or `null` for whatever your Claude Code install
  defaults to. Opus suits design reasoning; Sonnet is quicker and cheaper for a
  chatty panel.
- `claude.allowBash` — off by default. The agent always has `Read`, `Write`,
  `Edit`, `Glob` and `Grep`. Adding `Bash` makes it far more capable, but note
  that the workspace confines the agent's *working directory*, not what a shell
  command can reach.
- `claude.systemPromptExtra` — appended to the built-in prompt. Put your printer,
  materials and tolerances here.
- `rules` — per-rule `enabled` flag and parameter overrides. The panel's
  Settings writes this; only what you have changed is stored, so a rule gaining
  a parameter needs no migration.
- `claude.autoApprove` — off by default. Skips the approval card for
  `set_parameter` and `run_fusion_script`, which means generated code runs
  against your open document unseen.

Restart the add-in (Stop → Run) after editing.

## Development

After editing anything under `addin/`, run `./scripts/install.sh` to push the
changes, then **Add-Ins → Stop → Run** in Fusion.

> **Do not symlink the add-in into Fusion's AddIns folder.** Fusion resolves the
> symlink and persists the *target* path as a second add-in, so it appears twice
> in the Add-Ins list and, after a restart, runs twice — two instances fighting
> over the same panel and palette IDs. `install.sh` therefore copies. If you hit
> this, quit Fusion and run `./scripts/fix-duplicate-registration.sh`, which
> drops the stale entry from Fusion's registry (backing the file up first).
> `./scripts/doctor.sh` checks for both conditions.

`./scripts/test.sh` runs four suites with only Fusion's UI objects stubbed:

- **round trip** — palette → bridge → sidecar → pump → palette. Fails if the
  reply stops arriving incrementally, which is the regression that matters: a
  pump draining on the producer's thread would still "work" in a naive test and
  deadlock Fusion.
- **tool path** — the add-in's loopback server against the sidecar's real
  client, covering the token check and the hop onto the main thread.
- **approvals** — that a prompt nobody answers, and a turn cancelled mid-prompt,
  both come back as refusals rather than parking the agent forever.
- **rules** — the geometry the rules reason with (teardrop tangency, the
  thin-wall measurement), finding ids staying stable under floating-point
  noise, parameter clamping, and that the two processes expose the same tools
  and gate the same ones. The stubs stop short of a fake B-Rep kernel, which
  would only ever test itself.

Anything needing real topology is verified in Fusion against the part
`scripts/make_test_part.py` builds — run it from **Utilities → Scripts and
Add-Ins**. Every feature in it exists because some rule should react to it in a
particular way, and the script says which.

`FDM_AGENT_BACKEND` overrides which backend the sidecar starts on, which is how
the test pins itself to the stub instead of making billable model calls.

To poke at the sidecar by hand:

```bash
{ echo '{"action":"send","turnId":"t1","text":"hello"}'; sleep 6; } | ~/.fusion-fdm-agent/venv/bin/python -m fdm_sidecar
```

The `sleep` matters: closing stdin ends the session, and the sidecar cancels any
turn still in flight. Without it you see the cancellation instead of the stream.

Logs:

```
~/.fusion-fdm-agent/logs/addin.log
~/.fusion-fdm-agent/logs/sidecar.log
```

For styling work, serve the palette and open it in a browser — the `adsk` bridge
is guarded, so the page degrades instead of throwing:

```bash
python3 -m http.server 8777 --directory addin/FusionFDMAgent/resources/palette
```

Then drive it from the browser console with
`window.fusionJavaScriptHandler.handle('delta', '{"turnId":"t1","text":"hi"}')`.
(`.claude/launch.json` has the same server preconfigured.)

## Layout

```
addin/FusionFDMAgent/   the add-in (stdlib only)
  lib/ui.py             toolbar panel, button, palette lifecycle
  lib/bridge.py         palette ⇄ sidecar message router
  lib/events.py         background thread → main thread pump
  lib/sidecar.py        subprocess supervision
  lib/toolserver.py     loopback server the sidecar calls into
  lib/design_tools.py   the Fusion operations themselves
  lib/selection.py      remembers what you last selected
  lib/rules_controller.py  one rule session, shared by panel and agent
  lib/rules/            the printability rules
    geometry.py         pure maths, no adsk import
    fusion_geom.py      the thin layer that touches Fusion
    session.py          findings, staleness, apply ordering
  resources/palette/    the chat and rules UI
sidecar/fdm_sidecar/    the agent process
  backends/             claude (working), echo (stub), codex (detection only)
  fusion_tools.py       those operations as in-process MCP tools
  fusion_client.py      loopback client
tests/                  round-trip, tool path, approvals + stubbed `adsk`
scripts/                bootstrap, install, uninstall, doctor, test,
                        fix-duplicate-registration, make_test_part
```
