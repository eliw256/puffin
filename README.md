# Puffin

Puffin is an engineer-first schematic, PCB, and firmware toolkit. You design the system at the
block, pin, and net level; Puffin keeps one human-readable project graph (`puffin.yaml`) as the
source of truth and keeps every other representation consistent with it and verified. It does not
ship an AI model. It gives whatever model you bring the project, the docs, and the tools to do the
judgment-heavy work, while deterministic code handles sync and every edit goes through the same
validated API.

Status: milestone M0 (core graph, validation, CLI). Firmware generation, KiCad import, the MCP
server, and the UI come in later milestones.

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

## Learn more

- [PUFFIN_SPEC.md](PUFFIN_SPEC.md): product thesis, design rules, data model, and milestones.
- [docs/DECISIONS.md](docs/DECISIONS.md): resolved design decisions and M0 modelling choices.

## License

MIT. See [LICENSE](LICENSE).
