# Scoped KiCad write-back, design note

Status: implemented 2026-11-25 (`puffin/kicad/schematic.py`, `writeback.py`, `checkpoint.py`),
with the resolutions at the end and the as-built notes below.

**Goal:** a pin move done in Puffin (`move_pin` on a KiCad-managed net) can also be applied to the
`.kicad_sch`, so the round trip doesn't need hand-rewiring. KiCad stays the authority for
connectivity. Write-back is a narrow, verified convenience, not a schematic editor (spec §2.7,
§3: "v1: write-back of net/label changes").

Facts below come from the frozen fixture (`tests/fixtures/dual-esp32s3-robot`), KiCad 9.0.0
format, and from the real GPIO10 to GPIO14 move you made by hand in the example. That move is
the model for what write-back must reproduce.

## What KiCad itself did for your move

Your edit changed exactly four things in `robot.kicad_sch`:
1. The `IMU_CS` label moved from `(at 48.26 100.33 180)` to `(at 48.26 110.49 180)`.
2. The **wire stub** from the pin end to the label moved with it, from `48.26 100.33 → 49.53
   100.33` to `48.26 110.49 → 49.53 110.49`.
3. GPIO14's no-connect at `49.53 110.49` was removed.
4. A no-connect was added at GPIO10's pin end, `49.53 100.33`.

(KiCad also deleted and re-created the wire under a new uuid and re-sorted items by uuid. That's
churn from saving, not part of the change.)

## Decisions

### a. Supported cases: accepted, widened by one shape

Your default only supports a label attached *directly* to the pin endpoint, with no wires. That
would refuse the edit you just made: every signal label in the example sits on a short stub, the
usual KiCad style. I propose supporting exactly two shapes at the **source pin endpoint S**:

- **A. Direct label:** exactly one local `label` at S, and nothing else touching S.
- **B. Label on a stub:** exactly one `wire` with one end at S. Its other end Q carries exactly
  one local `label`. Nothing else touches S or Q, and nothing touches the stub's interior: no
  other wire ends, junctions, labels or pins on the segment.

At the **target pin endpoint T**: nothing touches T except, optionally, one `no_connect`. That
also means no wire passing through T.

Write-back then moves the label (and in shape B the stub) to T, moves the target's
`no_connect` to S, or adds one at S if the target had none, and changes nothing else.

**Refused, each with a specific reason ending "rewire in KiCad":** any other shape at S, meaning
two labels, a wire to another part, a junction, a global or hierarchical label, a power
symbol, or a pin-to-pin connection. Also refused: anything at T beyond a no-connect; a part pin
mapped to more than one pad (GND on 1/40/41); and a symbol on a sub-sheet.

**Orientation:** the label (and stub) keep their position relative to the pin.
- **Same outward direction** for S and T: a pure translation by T − S. That covers all of IO4–8,
  IO15, IO16 on the left of the ESP32-S3 symbol.
- **Opposite direction** (left side to right side): rotate 180° about the pin endpoint and
  swap the label's `justify left`/`right`.
- **90° apart:** refused in v1. It doesn't occur on this symbol, and rotated label text has
  more layout cases than it's worth now.

**Ops covered:** a proposal qualifies when its only change to managed connectivity is one or
more `move_pin`s: each replaces one endpoint of a component with another pin of the same
component, on the same net. Several independent moves are fine. A move whose target is another
move's source (a swap) is refused. Every other op that changes managed connectivity (`set_net`,
`connect`, `rename_net`, components) is refused for write-back. It still applies to the graph,
with the `kicad-overwrite` warning.

### b. Pin coordinates: accepted, mirror refused in v1

The endpoint of a pin comes from the schematic's embedded `lib_symbols`, never from the
installed KiCad libraries:
- **Find the pin:** graph pin to part pin `numbers` (exactly one) to the lib pin with that
  `number`. The pin is searched for in sub-symbols `<lib_id>_<unit>_<style>` and the shared
  `_0_` units, for the instance's `(unit n)` and body style 1.
- **Transform:** apply the instance's rotation (0/90/180/270), flip y (lib y points up,
  schematic y points down), then add the instance `(at x y)`.
  - Checked against the fixture: U1 is at `(64.77 92.71 0)` and IO10 is at `(-15.24 -7.62)`,
    giving `(49.53, 100.33)`, the pin end in your diff.
  - The pin's outward direction is the pin angle plus 180°, rotated the same way.
- **Tests use the fixture's real symbols:**
  - Computed endpoints must coincide with the wire ends kicad-cli connects. This covers every
    U1, U2, U3, R, C and SW pin, and the power symbols at 90° and 270°, so all four rotations
    are exercised on real data.
  - A table test pins IO10 to `(49.53, 100.33)` and IO14 to `(49.53, 110.49)`.
- **Mirrored instances (`(mirror x|y)`) are refused in v1.** No real symbol in the example is
  mirrored, so there's nothing honest to test against. Adding mirror support later means adding
  a real mirrored fixture.
- **Arithmetic** uses `Decimal`, parsed from the file text, so 100.33 + 10.16 is exactly
  110.49. Numbers are written the way KiCad writes them: up to 4 decimals, no trailing zeros.

### c. Closed-loop verification: accepted as written

The modified schematic is written to a temp directory, next to copies of `sym-lib-table` and
the project-local symbol libraries, so kicad-cli resolves symbols the same way. kicad-cli then
exports its netlist.
- **What must match:** the result must equal the original netlist's connectivity with exactly
  the expected change, compared as `{net name: set of (ref, pad)}` over **all** parts, managed
  or not:
  - net N loses (U1, src pad) and gains (U1, tgt pad);
  - the target's `unconnected-(…)` net disappears;
  - the source pad becomes `unconnected-(…)`;
  - every other net is identical.
- **Graph check:** `build_changes(graph after the edit, verification netlist)` must also come
  back empty, meaning in sync.
- **On mismatch, or with no kicad-cli:** write-back is unavailable, nothing changes, and the
  reason shows the first differing net. The graph edit can still be proposed without it.
- **Timing:** verification runs at **propose** time, so the engineer only ever sees a verified
  schematic diff. Apply writes exactly the verified bytes, guarded by hashes (see e).

### d. Safety: accepted, with these specifics

- **Lock file.** Refuse while `~<name>.kicad_sch.lck` exists next to the schematic (KiCad 9
  naming; the example has one whenever KiCad has it open). The message says: "close the
  schematic in KiCad, or KiCad will overwrite the change on its next save". It's checked at
  propose time and again at apply time.
- **Minimal textual edit.** Only these change:
  - the label's `(at …)`;
  - the stub wire's `(xy …) (xy …)` line;
  - the no-connect's `(at …)`, or a new `(no_connect …)` block inserted before the first
    existing `(no_connect`;
  - `justify` when the label crosses sides.

  Everything else is byte for byte, including uuids, order and whitespace. A test asserts that
  the diff touches only those lines.
- **Deterministic uuid.** A new no-connect's uuid is `uuid5` of the schematic hash plus the pin,
  not random, so the same proposal always gives the same bytes.
- **Backup.**
  - The original is copied to `.puffin/backup/<name>.kicad_sch.<first 12 hex of its sha256>`
    before replacing.
  - `.puffin/backup/` is gitignored.
  - `puffin check` shows a `writeback-unconfirmed` warning while a backup exists, so you are
    reminded to look at the change in KiCad.
  - `puffin writeback confirm` deletes the backup. `puffin writeback restore` puts the original
    back and restores the YAML and `state.json` from the same transaction record. MCP gets no
    tool for either: confirming a schematic change is yours to do, in KiCad.
  - Only one unconfirmed write-back may exist at a time; a second is refused until you confirm
    or restore.
- **Sub-sheets:** v1 handles symbols on the root sheet file only.

### e. Flow: accepted, opt-in per proposal

- **Opt-in.** `propose_edit(ops, writeback=false)` stays exactly as it is now. With
  `writeback: true`:
  - the proposal adds a `writeback` section:
    `{"available": bool, "reason": str, "schematic": path, "diff": unified diff of the
    .kicad_sch, "verified": true}`;
  - the `kicad-overwrite` warning is replaced by a `kicad-writeback` note listing what will be
    written.

  Write-back is opt-in rather than automatic because it writes a file you edit by hand in
  another tool. It should always be a deliberate choice.
- **The warning points to it.** Once this lands, `kicad-overwrite`'s hint says "write-back can
  apply this to robot.kicad_sch: propose again with writeback" when the change qualifies, and
  gives the refusal reason otherwise. Core still produces the warning. The CLI and MCP layers,
  which can read the schematic, fill in the hint.
- **Apply is all or nothing.** `apply_edit` checks that `puffin.yaml` *and* the schematic still
  have the hashes recorded at propose time, and that there's no lock file. Then:
  1. back up the schematic;
  2. replace the schematic (atomic rename);
  3. replace `puffin.yaml`;
  4. write `state.json` (both hashes);
  5. append to history (`kind: "edit+writeback"`).

  If any step fails, the earlier steps are rolled back from the backup and the in-memory copy.
- **CLI.** There's no CLI edit command today. I propose
  `puffin move-pin COMPONENT FROM TO [--writeback] [--dry-run] [--yes] [--json]`, with the same
  review flow as `puffin import kicad`: print both diffs, then confirm. It refuses without
  `--yes` when there's no terminal.

### f. No drift afterwards: accepted

After a successful write-back, `state.json` records the new schematic hash and the new graph
hash. That's legitimate, and not a fake sync, because kicad-cli verified exactly those bytes in
this transaction. `puffin check` is then clean, with no import needed. A test applies a
write-back on a fixture copy, then runs `check` (no drift) and a real `puffin import kicad
--dry-run` via kicad-cli (in sync).

## Tests (planned)

- **Geometry:** pin endpoints for every fixture symbol checked against kicad-cli wire
  connectivity; the IO10 and IO14 table.
- **Shapes:** stub (the fixture's real IMU_CS) moved to IO4 (same side) and IO21 (opposite
  side), each verified by kicad-cli. Each refusal case gets one hand-edited copy of the fixture
  schematic in a temp dir: two labels, a junction, a target with a wire, a sub-sheet, a mirrored
  instance, a lock file present.
- **Minimal diff:** the byte diff touches only the expected lines; the uuid is the same across
  runs.
- **Verification:** a monkeypatched exporter returning wrong connectivity, and a missing
  kicad-cli, both make write-back unavailable and leave the files unchanged.
- **Transactions:** a forced failure at each apply step leaves the YAML, schematic and state as
  they were; a stale schematic hash refuses.
- **End to end over MCP and CLI:** `legal_pins`, then `propose_edit(writeback=true)` shows the
  YAML and schematic diffs, then `apply_edit`; `check` is clean; kicad-cli import is in sync.
  Then `writeback restore` brings back the originals.
- Tests that run kicad-cli are skipped when it's missing, but the CI notes say how many.

## Resolutions (review, 2026-11-25)

1. Shape B (label on a stub): accepted.
2. Opposite-side moves: accepted. 90° moves and mirrored instances: refused in v1.
3. Opt-in per proposal (`writeback: true`); the `kicad-overwrite` hint offers write-back when the
   change qualifies.
4. CLI surface approved: `puffin move-pin`, `puffin writeback confirm|restore`.
5. The backup is deleted only by `puffin writeback confirm`. A later import does not confirm:
   kicad-cli proves connectivity, but only the engineer can judge layout (label overlaps, a stub
   landing on text). `writeback-unconfirmed` stays until confirm.
6. **Changed: write-backs stack on one checkpoint.** "Only one unconfirmed write-back" is
   dropped. The first write-back after a confirm saves a *checkpoint*: the last confirmed
   schematic(s), `puffin.yaml` and `state.json`, in `.puffin/backup/`. Further write-backs stack
   on top without new backups. `confirm` accepts every pending write-back and deletes the
   checkpoint; `restore` returns schematic(s), YAML and state to the checkpoint, undoing all of
   them. The warning says how many are pending. This replaces the per-file backup naming in d.

## As built

- **Geometry** is checked on the fixture's real symbols: all 112 netlist pins land on a wire end,
  label, pin or no-connect, every unconnected pin on a no-connect marker, and the rotated power
  symbols (90° and 270°) on their wires. The fixture has no 180° instance; the rotation code is
  one formula for all four.
- **Verification is two-layered**, and each layer has a test that only it catches: the full
  netlist comparison (every part, managed or not; a tampered export that drops C4 from GND
  must fail) and the graph check (`build_changes` in sync).
- **The proposal is refused, not downgraded,** when `writeback: true` was asked for and cannot
  apply: `ok: false` with `writeback-unavailable` and the reason, and no proposal id. The model
  then proposes without write-back if the engineer wants the graph-only edit.
- **Checkpoint files** live in `.puffin/backup/` (gitignored) with a `checkpoint.json` manifest
  listing each saved file and the pending count. `restore` also removes files that did not
  exist at the checkpoint (a `state.json` created by the first write-back). The history log is
  not rolled back; it records what happened.
