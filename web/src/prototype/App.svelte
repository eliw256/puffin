<script lang="ts">
  // Static prototype of the pin map (M4 step 2, revised). Real fixture data, no backend.
  import { untrack } from "svelte";
  import DitherCanvas from "../lib/DitherCanvas.svelte";
  import { FAMILY_LABEL, FAMILY_ORDER, familySwatch, STATUS_GLYPH, STATUS_LABEL } from "../lib/families";
  import PinDetail from "../lib/PinDetail.svelte";
  import PinMap, { type Drag } from "../lib/PinMap.svelte";
  import ReviewPanel from "../lib/ReviewPanel.svelte";
  import { PX_PER_MM } from "../lib/scale";
  import type { DropStatus, Snapshot } from "../lib/types";

  type Props = { snapshot: Snapshot };
  let { snapshot }: Props = $props();

  type Scene = "select" | "drag" | "review";
  let scene = $state<Scene>("select");
  // The starting selection comes from the snapshot once; afterwards it is the user's.
  let selected = $state<string | null>(untrack(() => snapshot.selected));
  let drag = $state<Drag | null>(null);
  let review = $state<{ target: string } | null>(null);
  let toast: { kind: "refused" | "info"; text: string } | null = $state(null);
  let verifying = $state(false);
  let verifyDone = $state(false);

  const RECORDED_TARGET = "GPIO4"; // the only move with a recorded proposal in the snapshot
  const legal = $derived(snapshot.legal);
  const source = $derived(legal.current_pin ?? "");
  const statusMap = $derived.by((): Record<string, DropStatus> => {
    const map: Record<string, DropStatus> = {};
    for (const candidate of legal.candidates) map[candidate.pin] = candidate.status;
    return map;
  });
  const statuses = $derived(drag || scene === "drag" ? statusMap : null);
  const sourceInfo = $derived(snapshot.pins[source]);
  const hasDatasheet = $derived(Object.keys(snapshot.doc).length > 0);
  const draggable = $derived(source ? [source] : []);

  function title(target: string): string {
    return `Move ${legal.component}.${source} → ${target} (net ${sourceInfo?.net}; ${legal.peripheral}.${legal.signal})`;
  }

  function pointNear(pin: string): [number, number] {
    const number = Object.entries(snapshot.pin_by_number).find(([, name]) => name === pin)?.[0];
    const sym = snapshot.symbol.pins.find((p) => p.number === number);
    if (!sym) return [0, 0];
    return [sym.point[0] * PX_PER_MM - 70, sym.point[1] * PX_PER_MM + 18];
  }

  function show(next: Scene) {
    scene = next;
    toast = null;
    review = next === "review" ? { target: RECORDED_TARGET } : null;
    drag = next === "drag" ? { source, pointer: pointNear(RECORDED_TARGET), target: RECORDED_TARGET } : null;
    if (next === "select") selected = snapshot.selected;
  }

  function freeList(): string {
    return legal.candidates.filter((c) => c.status === "free").map((c) => c.pin).slice(0, 6).join(", ");
  }

  function dropped(_from: string, target: string | null) {
    scene = "select";
    if (!target) return;
    const status = statusMap[target] ?? "illegal";
    if (status === "illegal") {
      toast = {
        kind: "refused",
        text: `Refused: ${target} can't carry ${legal.peripheral}.${legal.base}. Free legal pins include ${freeList()}.`,
      };
      return;
    }
    if (status === "current") {
      toast = { kind: "info", text: `${legal.peripheral}.${legal.signal} is already on ${target}.` };
      return;
    }
    toast = null;
    review = { target };
  }
</script>

<div class="app">
  <header class="top">
    <span class="wordmark">puffin</span>
    <span class="project mono">{snapshot.project}</span>
    <nav class="boards" aria-label="Boards">
      {#each snapshot.boards as board (board)}
        <button class:active={board === "main"} disabled={board !== "main"}>{board}</button>
      {/each}
    </nav>
    <span class="component mono">{snapshot.component} · {snapshot.part}{snapshot.variant ? ` [${snapshot.variant}]` : ""}</span>
    <span class="checks" class:bad={snapshot.check.errors > 0}>
      {snapshot.check.errors} errors · {snapshot.check.warnings} warnings
    </span>
    <div class="scenes" role="group" aria-label="Prototype states">
      <span>Prototype state</span>
      <button class:active={scene === "select" && !review} onclick={() => show("select")}>1 · Pin selected</button>
      <button class:active={scene === "drag"} onclick={() => show("drag")}>2 · Drag in progress</button>
      <button class:active={review !== null} onclick={() => show("review")}>3 · Review</button>
    </div>
  </header>

  <main>
    <section class="map" aria-label="Pin map">
      {#if toast}
        <p class="toast {toast.kind}" role="alert">
          {#if toast.kind === "refused"}<span aria-hidden="true">⊘ </span>{/if}{toast.text}
          <button aria-label="Dismiss" onclick={() => (toast = null)}>×</button>
        </p>
      {/if}
      <div class="canvas">
        <PinMap
          symbol={snapshot.symbol}
          pins={snapshot.pins}
          pinByNumber={snapshot.pin_by_number}
          {selected}
          {draggable}
          {statuses}
          bind:drag
          onselect={(pin) => (selected = pin)}
          ondrop={dropped}
        />
      </div>
      <!-- Fixed height: switching between the family key and the drag key never moves the map. -->
      <footer class="legend" aria-label="Legend">
        {#if statuses}
          <div class="legend-row">
            <span class="legend-title">Dropping {legal.peripheral}.{legal.signal}</span>
            <span class="target-caption mono" class:empty={!drag?.target}>
              {#if drag?.target}on {drag.target}: {STATUS_GLYPH[statusMap[drag.target] ?? "illegal"]}
                {STATUS_LABEL[statusMap[drag.target] ?? "illegal"]}{:else}over no pin{/if}
            </span>
            <span class="hint">Esc cancels</span>
          </div>
          <div class="legend-row">
            {#each ["current", "free", "connected", "in_use", "needs_override", "illegal"] as const as s (s)}
              <span class="status"><span class="glyph" aria-hidden="true">{STATUS_GLYPH[s]}</span>{STATUS_LABEL[s]}</span>
            {/each}
          </div>
        {:else}
          <div class="legend-row">
            {#each FAMILY_ORDER as family (family)}
              {@const swatch = familySwatch(family)}
              <span class="swatch">
                {#if swatch}<DitherCanvas {swatch} width={22} height={12} cell={2} />{:else}<span class="idle-chip"></span>{/if}
                {FAMILY_LABEL[family]}
              </span>
            {/each}
          </div>
          <div class="legend-row">
            <span class="swatch"><span class="flag-chip" aria-hidden="true"></span>flagged (strapping, USB, JTAG, PSRAM)</span>
            <span class="hint">Click selects · drag the <span class="mono">{source}</span> tag to move {legal.peripheral}.{legal.signal} · ⌘/Ctrl + scroll zooms · drag empty space to pan</span>
          </div>
        {/if}
      </footer>
    </section>

    <aside class="side">
      {#if selected && snapshot.pins[selected]}
        <PinDetail pin={snapshot.pins[selected]} doc={snapshot.doc[selected]} {hasDatasheet} part={snapshot.part} />
      {:else}
        <p class="empty">Select a pin to see its net, assignment, flags and datasheet section.</p>
      {/if}
    </aside>
  </main>

  {#if review}
    <ReviewPanel
      title={title(review.target)}
      proposal={snapshot.proposal}
      withWriteback={verifyDone ? snapshot.proposal_writeback : null}
      verifying={verifying}
      unavailable={review.target === RECORDED_TARGET
        ? ""
        : `Review preview only exists for ${RECORDED_TARGET} in this prototype. ${review.target} is ${STATUS_LABEL[statusMap[review.target] ?? "illegal"]}; the real UI proposes this move and shows its diffs and checks here.`}
      onchoose={(choice) => {
        if (choice === "writeback" && !verifyDone) {
          verifying = true; // stands in for the kicad-cli run
          setTimeout(() => ((verifying = false), (verifyDone = true)), 900);
        }
      }}
      onclose={() => show("select")}
      onapply={(choice) => {
        review = null;
        scene = "select";
        toast = { kind: "info", text: `Prototype: nothing applied. The real UI would apply to ${choice === "writeback" ? "puffin.yaml and the schematic" : "puffin.yaml"}.` };
      }}
    />
  {/if}
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }
  .top {
    display: flex;
    align-items: center;
    gap: 16px;
    height: 48px;
    padding: 0 16px;
    border-bottom: 1px solid var(--line);
    background: var(--surface);
    white-space: nowrap;
    overflow: hidden;
  }
  .wordmark {
    font-weight: 600;
    font-size: 18px;
    letter-spacing: 0.02em;
    color: var(--cream);
  }
  .project,
  .component {
    color: var(--bone);
    font-size: 13px;
  }
  .boards button,
  .scenes button {
    background: none;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 3px 10px;
    color: var(--bone);
    cursor: pointer;
  }
  .boards button.active,
  .scenes button.active {
    background: var(--blue);
    color: var(--cream);
    border-color: var(--cream);
  }
  .boards button:disabled:not(.active) {
    color: var(--muted);
    cursor: default;
  }
  .boards {
    display: flex;
    gap: 4px;
  }
  .checks {
    font-size: 12.5px;
    color: var(--bone);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 1px 10px;
  }
  .checks.bad {
    color: var(--error-text);
    border-color: var(--error);
  }
  .scenes {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    color: var(--muted);
  }
  main {
    flex: 1;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 380px;
    min-height: 0;
  }
  .map {
    position: relative;
    display: grid;
    grid-template-rows: minmax(0, 1fr) 64px;
    min-height: 0;
  }
  .canvas {
    min-height: 0;
  }
  .side {
    border-left: 1px solid var(--line);
    background: var(--surface);
    overflow-y: auto;
  }
  .empty {
    padding: 20px;
    color: var(--muted);
  }
  .legend {
    height: 64px;
    overflow: hidden;
    padding: 8px 16px;
    border-top: 1px solid var(--line);
    font-size: 12.5px;
    color: var(--bone);
    display: grid;
    grid-template-rows: 22px 22px;
    gap: 4px;
  }
  .legend-row {
    display: flex;
    align-items: center;
    gap: 16px;
    white-space: nowrap;
    overflow: hidden;
  }
  .legend-title {
    color: var(--cream);
    font-weight: 600;
  }
  .swatch,
  .status {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .idle-chip {
    width: 22px;
    height: 2px;
    background: var(--pin-idle);
  }
  .flag-chip {
    width: 12px;
    height: 12px;
    border: 1px solid var(--bone);
    border-radius: 2px;
    background: repeating-linear-gradient(45deg, var(--bone) 0 1.5px, var(--bg) 1.5px 3.5px);
  }
  .glyph {
    color: var(--cream);
  }
  .target-caption {
    min-width: 230px;
    color: var(--cream);
    background: var(--blue);
    border: 1px solid var(--cream);
    border-radius: 10px;
    padding: 0 10px;
  }
  .target-caption.empty {
    background: none;
    border-color: var(--line);
    color: var(--muted);
  }
  .hint {
    margin-left: auto;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .toast {
    position: absolute;
    top: 12px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 4;
    max-width: 80%;
    margin: 0;
    padding: 8px 36px 8px 12px;
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--bone);
    border: 1px solid var(--line);
    border-left: 3px solid var(--bone);
  }
  .toast.refused {
    border-left-color: var(--error);
    color: var(--error-text);
  }
  .toast button {
    position: absolute;
    right: 8px;
    top: 4px;
    background: none;
    border: none;
    font-size: 18px;
    color: var(--bone);
    cursor: pointer;
  }
</style>
