# Backlog

Not scheduled. Each item has one line of context. Move an item into a milestone note when it is
taken on, and mark it done here when it lands.

## Write-back
- ~~**Present write-back as a choice, not a warning.**~~ Done in M4: the review panel's
  required "Puffin only" / "Puffin + schematic" choice.
- **`writeback: auto` project setting.** Consider after about 20 write-backs with no restore or
  layout fix, as evidence that verified write-back is safe to apply by default.
- **Auto-confirm, per user, opt-in, off by default.** Confirming needs a human look at the layout
  today; some engineers may accept kicad-cli verification alone.
- **90° moves and mirrored symbols.** Add real fixtures that use them first, then support them.
- **Write-back beyond pin moves:** net labels and symbol/pin changes, the v1 scope in
  DECISIONS 11.4.

## Validation
- **Driver conflict: two driving signals on one net** (TX wired to TX). Needs signal directions
  on more peripherals than the UARTs.
- **SPI directions depend on master/slave role.** Model the role so SPI links can get
  directions too.

## Import
- **Series passives as pass-through.** Detection already warns; the graph still sees two
  half-nets.

## Generation
- **An error on a component touching only one board shouldn't block generating other boards.**
  Today any validation error stops every board.
- **Next firmware target after ESP-IDF** (DECISIONS 11.3).

## Core
- **Cascade removal for `remove_component`,** if it turns out to be needed. Removal is refused
  while references remain.

## Parts
- **Replace `example/spi-imu` with a real, datasheet-backed IMU,** so doc citations are tested on
  a second part.
- **A documented procedure (later an MCP tool) for adding a part from a PDF datasheet**
  (spec §5.2).

## Process
- **After the M4 prototype is approved: watch one other engineer use it without explanation.**
