# Fusion FDM Agent

An Autodesk Fusion add-in that puts a **chat panel** in the Design workspace and
wires it to an external coding agent (Claude Code, or Codex later).

Status: the **Claude Code backend works** — streaming replies, tool-use display
and cancellation, in a persistent session. Design read/write (letting the agent
inspect and modify the open model) is the next pass; see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). A stub `echo` backend is kept for
testing the plumbing without a model, and Codex is detection-only.

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
./scripts/test.sh        # round-trip test, no Fusion needed
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
    "systemPromptExtra": ""
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

`./scripts/test.sh` drives the whole chain — palette → bridge → sidecar → pump →
palette — with only Fusion's UI objects stubbed. It fails if the reply stops
arriving incrementally, which is the regression that matters: a pump that drains
on the producer's thread would still "work" in a naive test and deadlock Fusion.

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
  resources/palette/    the chat UI
sidecar/fdm_sidecar/    the agent process
  backends/             claude (working), echo (stub), codex (detection only)
tests/                  round-trip test + stubbed `adsk`
scripts/                bootstrap, install, uninstall, doctor, test,
                        fix-duplicate-registration
```
