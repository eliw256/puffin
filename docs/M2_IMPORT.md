# M2: KiCad import, design note

Status: approved 2026-11-25, with the resolutions at the end of this note.

Format facts below were checked against `kicad-cli` 9.0.0 (`sch export netlist`, format
`kicadsexpr`, netlist version `"E"`). Anything marked *confirm in Step 1* is checked again
against the example board's own netlist.

## Flow

```
kicad-cli ──► netlist (s-expr) ──► parse (pure) ──► pin mapping (pure)
                                                        │
graph (puffin.yaml) ───────────────────────────────► change-set builder (pure)
                                                        │
                       review (CLI now, MCP propose/apply in M3)
                                                        │
                                          Project.batch(ops) ──► validate + checkers ──► save
```

Import runs once per distinct `boards.*.kicad_schematic`, not once per board. The example's two
boards share one schematic, so one import covers both.

## Decisions a to i

**a. Input: accepted, with one addition.** The executable is found from `$PUFFIN_KICAD_CLI`, then
`PATH`, then `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`. On this machine KiCad is
only in the app bundle, so the bundle fallback is the path actually used. The command is
`kicad-cli sch export netlist --format kicadsexpr --output <temp> <schematic>`, run with a
timeout. kicad-cli prints harmless Fontconfig warnings on stderr, so only the exit status
counts. *Addition:* `--netlist FILE` imports a pre-exported netlist. Tests and CI use this
route, so they never need kicad-cli. Tests that run kicad-cli are skipped when it is missing.
I'll write the s-expression parser myself (about 60 lines) rather than add `kiutils` as a
dependency.

**b. Managed components: accepted, with a guard.** Custom symbol fields appear in the netlist
under `(fields (field (name "Puffin_Part") "…"))`. The trouble with matching on an existing
reference designator alone is re-annotation in KiCad: if U3 and U4 swap numbers, the graph would
silently bind U3 to the wrong symbol. Proposal:
- A symbol whose `Puffin_Part` field is present is managed. If the field differs from the graph
  part, the change is shown as an update.
- A symbol matched only by reference designator is still managed, but gives a warning asking
  for a `Puffin_Part` field. Pin mapping (c) fails loudly if the symbol is the wrong part.
- New managed components take their board from `Puffin_Board`. If it's missing, that's an error.
  An optional `Puffin_Variant` field sets the variant. An empty field counts as absent, and an
  absent `Puffin_Variant` (or `Puffin_Board`) on an existing component keeps the graph's value.
- The change set lists how many unmanaged symbols were excluded, grouped by library part (for
  example "Device:R ×6").

**c. Pin mapping: accepted.** Nodes carry both the pin number (`(pin "27")`) and the pin name
(`(pinfunction "IO0")`). Mapping tries the number against the part's `numbers` first, then the
name against the part pin key and `datasheet_name`. Several KiCad pins can map to one part pin,
such as GND on pads 1, 40 and 41; they collapse to one endpoint. Pin numbers can be
alphanumeric (`A6` on a USB-C connector), so they're compared as strings. The placeholder
`example/spi-imu` has no `numbers`, so it maps by name.

**d. Net identity: accepted, with a refined split of auto-named nets.** KiCad produces three
kinds of net:
- **Labelled** (`IMU_SCK`, `GND`, `VCC_3V3`): matched by name, then by endpoint overlap.
- **`Net-(…)`**: a real connection with no label. It imports under a stable derived name,
  proposed format `AUTO(U1.GPIO4,U3.INT)` with endpoints sorted. If it touches an assigned MCU
  pin, a warning asks for a label.
- **`unconnected-(…)`**: a single pin with nothing attached. This is *not a net*, so the pin is
  treated as unconnected. If the pin is assigned, validation then reports
  `assignment-unconnected`, which is the right outcome.

The overlap match uses managed endpoints only. Each unmatched imported net pairs with the
unmatched graph net it shares the most endpoints with, deterministically; a tie is reported as a
remove plus an add, never guessed. A rename carries along the constraints on that net, and any
`intent` entry whose key is the old net name. A leading `/` (root-sheet hierarchical prefix) is
stripped; deeper sheet paths are kept as-is and warned about (*confirm in Step 1*: the KiCad 9
sample here had no `/` prefixes).

**e. Authority: accepted.** KiCad wins for managed components and nets. The graph stays
authoritative for assignments, constraints, overrides and intent. Removals always appear in the
change set.

**f. Assignment sync: accepted.** For each matched net and each MCU, compare that MCU's pins on
the net before and after. If exactly one pin left and exactly one arrived, the assignments on
the old pin move to the new one, inside the same change set. Any other shape is reported as
ambiguous and left alone: two pins arriving, the net now touching none of the MCU's pins, or a
pin that moved to a net which doesn't match.

Because all fixes are applied together and judged once (g), swapping two signals in KiCad, such
as SCK and MOSI trading pins, resolves cleanly. Applied one at a time, that swap would fail
part-way through on a double assignment.

**g. Transactions: accepted.** `Project.batch(ops, dry_run=False) -> EditResult`. `ops` is a list
of serializable operation objects that mirror the edit API, plus what import needs:
- `set_net(name, endpoints)`
- `rename_net(old, new)`, which also renames constraint references and an intent key
- `remove_net`
- `set_component(ref, part, board, variant)`
- `set_assignment_pin`

The batch is applied to a copy of the document in order, then judged once with the existing
"no new errors" rule plus checkers. The existing single edits become one-operation batches, so
there is a single code path.

**h. Review flow: accepted, plus `--json` and `--netlist`.** The command is
`puffin import kicad [path] [--netlist FILE] [--dry-run] [--yes] [--json]`. Without `--yes` and
with no terminal attached, it refuses instead of hanging. The change set prints in fixed
sections:
1. components added, removed and updated
2. nets added, removed and renamed
3. endpoint changes
4. proposed assignment fixes
5. ambiguous fixes
6. warnings
7. **errors the change would introduce**

An import that would introduce errors is rejected as a whole, the same rule as any edit. Listing
those errors in the preview tells you what to fix on the graph side first, typically adding an
override for a strapping pin you've just wired up in KiCad.

**i. Drift: changed.** Every netlist embeds the export time and the absolute schematic path, so
its hash changes on every export. Hashing it would also force `puffin check` to run kicad-cli.
Instead, `.puffin/state.json` records a SHA-256 of each schematic file: the root sheet plus its
sub-sheets, with line endings normalized to LF. `puffin check` compares those hashes. That is
cheap, needs no KiCad, and gives a warning, never an error.
- **When it's written:** an applied import records the hash. An import that finds nothing to
  change also records it, as "in sync". A `--dry-run` never writes it. *M2 review:* only a
  netlist exported by kicad-cli in the same run counts; a `--netlist` import leaves the state
  untouched and says so, because the file may come from another revision.
- **Commit it:** I recommend committing the state file. The schematic is in git, so the hash is
  the same on every checkout, and the file then records which schematic revision the YAML was
  last synced with. A gitignored file would make every fresh clone report drift.
- **False positives:** moving a symbol changes the file without changing connectivity. The
  warning says "may have changed" and points to `puffin import kicad --dry-run`.

## Other decisions

- **Series passives break nets: detected in M2, pass-through is v1.** If IMU_SCK runs through a
  series resistor, KiCad reports two nets, and with the resistor unmanaged the graph sees two
  half-nets. They import as-is: the labelled side keeps its name and an unlabelled far side
  becomes `AUTO(…)`. The change set warns whenever an unmanaged symbol with exactly two connected
  pins bridges two nets that each have at least one managed endpoint, naming the symbol, its
  library part, and both nets. For example: `IMU_SCK is split by R5 (Device:R); the far side
  imports as AUTO(U3.SCK)`. If a constraint is on one of the halves, the warning says which half
  it applies to. "Connected" excludes `unconnected-(…)` pins. *Refined in Step 4:* a bridge is
  skipped when either net is a power rail (it has a `power_in` or `power_out` pin in the
  netlist). Without this, every pull-up, EN RC part, button and decoupling cap in the example
  schematic (R1–R3, C1–C4, SW1) would warn.
- **Ownership by schematic.** A graph component belongs to schematic S if its board's
  `kicad_schematic` is S. Only those components can be removed by importing S. A reference
  designator in S that belongs to a board with a different schematic is an error.
- **Removing a component that still has assignments** is shown in the change set, but the batch
  is rejected with the blocking references from `Project.references()`. There's no cascade,
  consistent with M0.
- **Ignored fields:** value, footprint, DNP and libsource are ignored. DNP parts still carry
  connectivity.
- **Ordering:** every list in the change set is sorted, so the same inputs always produce
  byte-identical `--json` output.
- **Example project, Step 1:** the schematic's power nets will probably be named differently
  (`+3V3` or `VCC_3V3` rather than `3V3`), and buttons and resistors become unmanaged. I'll align
  `puffin.yaml` to the schematic and show you the diff before committing.

- **Endpoint order:** when a pin moves, the new pin takes the old one's place in the net's list,
  so the YAML diff is one token. Other additions are appended, sorted.

## Resolutions

1. `AUTO(U1.GPIO4,U3.INT)` names for unlabelled nets: approved.
2. No `--allow-errors`. An import that would introduce errors is rejected whole, and the preview
   lists those errors so the graph side can be fixed first.
3. `.puffin/state.json` is committed.
4. Added to scope: series-passive detection (see Other decisions). Its test uses a hand-edited
   variant of the netlist fixture.
