# Puffin

Puffin is an engineer-first schematic, PCB, and firmware toolkit. You design the system at the
block, pin, and net level; Puffin keeps one human-readable project graph (`puffin.yaml`) as the
source of truth and keeps every other representation consistent with it and verified. It does not
ship an AI model. It gives whatever model you bring the project, the docs, and the tools to do the
judgment-heavy work, while deterministic code handles sync and every edit goes through the same
validated API.

Status: milestone M2 (core graph, validation, ESP-IDF pin headers, KiCad import). The MCP
server and the UI come in later milestones.

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

## Learn more

- [PUFFIN_SPEC.md](PUFFIN_SPEC.md): product thesis, design rules, data model, and milestones.
- [docs/DECISIONS.md](docs/DECISIONS.md): resolved design decisions and modelling choices.
- [docs/M2_IMPORT.md](docs/M2_IMPORT.md): how KiCad import matches components and nets.

## License

MIT. See [LICENSE](LICENSE).
