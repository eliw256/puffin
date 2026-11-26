# Puffin

Puffin is an engineer-first schematic, PCB, and firmware toolkit. You design the system at the
block, pin, and net level; Puffin keeps one human-readable project graph (`puffin.yaml`) as the
source of truth and keeps every other representation consistent with it and verified. It does not
ship an AI model. It gives whatever model you bring the project, the docs, and the tools to do the
judgment-heavy work, while deterministic code handles sync and every edit goes through the same
validated API.

Status: milestone M4 (core graph, validation, ESP-IDF pin headers, KiCad import with write-back,
MCP server, local web UI). The Tauri desktop shell comes next.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.

```bash
git clone https://github.com/eliw256/puffin.git
cd puffin
uv sync
```

## Usage

Validate a project. Pass a `puffin.yaml` file or the directory that contains it:

```bash
uv run puffin check examples/dual-esp32s3-robot
```

```text
robot-mainboard: 0 error(s), 0 warning(s)
```

Each problem is printed with its location and a suggested fix. The exit code is 0 when there are no
errors, 1 when there are, and 2 when the project cannot be read.

Print the project graph, optionally limited to one board:

```bash
uv run puffin show examples/dual-esp32s3-robot --board main
```

Both commands accept `--json` for machine-readable output.

Generate each board's firmware pin header (`--check` reports stale files, `--dry-run` shows the
diff):

```bash
uv run puffin gen examples/dual-esp32s3-robot
```

Bring KiCad schematic changes into the graph. Puffin exports a netlist with `kicad-cli` (found
via `$PUFFIN_KICAD_CLI`, `PATH`, or the macOS app bundle), shows the change set, and applies it
only after you confirm:

```bash
uv run puffin import kicad examples/dual-esp32s3-robot --dry-run
```

Symbols with a `Puffin_Part` field (plus `Puffin_Board`, and optionally `Puffin_Variant`) are
managed; everything else, such as resistors and buttons, stays out of the graph. KiCad decides
components and nets; `puffin.yaml` keeps assignments, constraints, overrides, and intent. A pin
that moves in KiCad carries its assignment along when the move is unambiguous. Pass `--yes` to
apply without a prompt, `--netlist FILE` to use a pre-exported netlist, or `--json`. After an
import that ran `kicad-cli`, `.puffin/state.json` records hashes of the schematic and of the
graph's managed connectivity, so `puffin check` can warn when either side has changed since. A `--netlist` import never updates it, since the file
may not match the schematic.

## Use it in the browser

```bash
uv run puffin ui examples/dual-esp32s3-robot
```

This opens a local UI (127.0.0.1 only, with a per-run access token in the URL).
- **Pin map:** drawn from your KiCad symbol. Drag an assigned pin to another legal pin, review
  the YAML, header and schematic diffs, and choose "Puffin only" or "Puffin + schematic"
  (verified write-back) before applying.
- **Block diagram and issues:** see the boards and buses, and jump from any issue to its pin.
- **Live:** confirm or undo write-backs, import KiCad changes when the schematic drifts, and
  undo the last change. Edits made in KiCad, an editor or by an AI over MCP show up live.

## Use it from an AI model (MCP)

`puffin mcp` serves one project to your own model over MCP (stdio). With Claude Code:

```bash
claude mcp add puffin -- uv run puffin mcp examples/dual-esp32s3-robot
```

The model gets tools to read the graph (`get_project`, `query_pins`, `query_net`,
`legal_pins`), read part data and datasheet extracts (`get_part`, `search_docs`,
`read_doc_section`), run `check`, and change the project only through `propose_edit` or
`propose_import_kicad`, then `apply_edit`, then `generate`. Every proposal comes with the
`puffin.yaml` diff and the firmware header diff, and is refused as stale if the file changed in
between. The server also sends a behavior contract: show the diff before applying, never edit
generated files, and cite a doc section or project data for every electrical claim.
Applied proposals are logged to `.puffin/history.jsonl` (gitignored).

A pin move on a KiCad-managed net can also be written back to the schematic, when the pin
connects through one label (directly or on a short wire stub) and the target pin is free:

```bash
uv run puffin move-pin U1 GPIO10 GPIO4 --writeback --path examples/dual-esp32s3-robot
```

Puffin shows both diffs, verifies the edited schematic with `kicad-cli` before anything is
written, and refuses while KiCad has the schematic open. Review the result in KiCad, then run
`puffin writeback confirm` (or `puffin writeback restore` to undo every unconfirmed write-back).
See [docs/WRITEBACK.md](docs/WRITEBACK.md). See
[docs/M3_MCP.md](docs/M3_MCP.md) for the full tool surface.

## Learn more

- [PUFFIN_SPEC.md](PUFFIN_SPEC.md): product thesis, design rules, data model, and milestones.
- [docs/DECISIONS.md](docs/DECISIONS.md): resolved design decisions and modelling choices.
- [docs/M2_IMPORT.md](docs/M2_IMPORT.md): how KiCad import matches components and nets.

## License

MIT. See [LICENSE](LICENSE). The built web UI also ships third-party code and fonts under their
own licences (elkjs under EPL-2.0, Svelte under MIT, Inter and JetBrains Mono under the SIL Open
Font License); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
