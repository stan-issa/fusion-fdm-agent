# Fusion FDM Agent

An Autodesk Fusion add-in that puts a **chat panel** in the Design workspace and
wires it to an external coding agent (Claude Code, or Codex later).

Status: **skeleton**. The panel, palette, chat UI and sidecar all work end to
end against a stub `echo` backend. Real agent backends and design read/write are
the next two passes — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- macOS with Autodesk Fusion installed for the current user
- Python 3.10+ on the PATH (used to build the sidecar venv; Fusion's own
  embedded interpreter is never modified)
- Optional: the [Claude Code CLI](https://code.claude.com/docs/en/setup) —
  `curl -fsSL https://claude.ai/install.sh | bash`

## Install

```bash
./scripts/bootstrap.sh   # sidecar venv + ~/.fusion-fdm-agent
./scripts/install.sh     # symlink the add-in into Fusion
./scripts/doctor.sh      # verify everything above
./scripts/test.sh        # round-trip test, no Fusion needed
```

Then in Fusion: **Utilities → Add-Ins** (`Shift+S`) → `FusionFDMAgent` → **Run**.
A *FDM Agent* panel appears on the Design toolbar; its button opens the chat,
docked to the right.

`./scripts/uninstall.sh` removes the symlink.

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

## Development

The add-in is installed as a **symlink**, so editing files in this repo is the
whole loop. To pick up changes: **Add-Ins → Stop → Run**.

`./scripts/test.sh` drives the whole chain — palette → bridge → sidecar → pump →
palette — with only Fusion's UI objects stubbed. It fails if the reply stops
arriving incrementally, which is the regression that matters: a pump that drains
on the producer's thread would still "work" in a naive test and deadlock Fusion.

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
  backends/             echo (working), claude + codex (stubs)
tests/                  round-trip test + stubbed `adsk`
scripts/                bootstrap, install, uninstall, doctor, test
```
