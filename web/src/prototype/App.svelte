<script lang="ts">
  // Static prototype of the pin map (M4 step 2). Real fixture data, no backend.
  import { untrack } from "svelte";
  import { FAMILY_LABEL, FAMILY_ORDER, familyColor, STATUS_GLYPH, STATUS_LABEL } from "../lib/families";
  import PinDetail from "../lib/PinDetail.svelte";
  import PinMap, { type Drag } from "../lib/PinMap.svelte";
  import ReviewPanel from "../lib/ReviewPanel.svelte";
  import { PX_PER_MM } from "../lib/scale";
  import type { DropStatus, Snapshot } from "../lib/types";

  type Props = { snapshot: Snapshot };
  let { snapshot }: Props = $props();

  type Scene = "select" | "drag" | "review";
  let scene: Scene = $state("select");
  // The starting selection comes from the snapshot once; afterwards it is the user's.
  let selected: string | null = $state(untrack(() => snapshot.selected));
  let drag: Drag | null = $state(null);
  let reviewing = $state(false);
  let toast: { kind: "refused" | "info"; text: string } | null = $state(null);
  let applied: string | null = $state(null);

  const legal = $derived(snapshot.legal);
  const source = $derived(legal.current_pin ?? "");
  const statuses = $derived.by((): Record<string, DropStatus> | null => {
    if (!drag && scene !== "drag") return null;
    const map: Record<string, DropStatus> = {};
    for (const candidate of legal.candidates) map[candidate.pin] = candidate.status;
    return map;
  });
  const sourceInfo = $derived(snapshot.pins[source]);
  const reviewTitle = $derived(
    `Move ${legal.component}.${source} → GPIO4 (net ${sourceInfo?.net}; ${legal.peripheral}.${legal.signal})`,
  );
  const hasDatasheet = $derived(Object.keys(snapshot.doc).length > 0);

  function pointNear(pin: string): [number, number] {
    const number = Object.entries(snapshot.pin_by_number).find(([, name]) => name === pin)?.[0];
    const sym = snapshot.symbol.pins.find((p) => p.number === number);
    if (!sym) return [0, 0];
    return [sym.point[0] * PX_PER_MM - 70, sym.point[1] * PX_PER_MM + 18];
  }

  function show(next: Scene) {
    scene = next;
    toast = null;
    applied = null;
    reviewing = next === "review";
    drag = next === "drag" ? { source, pointer: pointNear("GPIO4"), target: "GPIO4" } : null;
    if (next === "select") selected = snapshot.selected;
  }

  function freeList(): string {
    return legal.candidates.filter((c) => c.status === "free").map((c) => c.pin).slice(0, 6).join(", ");
  }

  function dropped(from: string, target: string | null) {
    scene = "select";
    if (!target) return;
    const status = statuses?.[target] ?? legal.candidates.find((c) => c.pin === target)?.status ?? "illegal";
    if (status === "illegal") {
      toast = {
        kind: "refused",
        text: `Refused: ${target} can't carry ${legal.peripheral}.${legal.base}. Free legal pins include ${freeList()}.`,
      };
    } else if (status === "in_use") {
      const by = legal.candidates.find((c) => c.pin === target)?.assigned[0];
      toast = {
        kind: "refused",
        text: `${target} is in use by ${by?.peripheral}.${by?.signal}. The proposal would fail validation; move that signal first.`,
      };
    } else if (target === "GPIO4") {
      toast = null;
      reviewing = true;
    } else {
      toast = { kind: "info", text: `Prototype: only the move ${from} → GPIO4 has a recorded proposal.` };
    }
  }

  const draggable = $derived(source ? [source] : []);
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
      <button class:active={scene === "select" && !reviewing} onclick={() => show("select")}>1 · Pin selected</button>
      <button class:active={scene === "drag"} onclick={() => show("drag")}>2 · Drag in progress</button>
      <button class:active={reviewing} onclick={() => show("review")}>3 · Review</button>
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
      {#if applied}
        <p class="toast info" role="status">{applied} <button aria-label="Dismiss" onclick={() => (applied = null)}>×</button></p>
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
      <footer class="legend" aria-label="Legend">
        {#if statuses}
          <span class="legend-title">Dropping {legal.peripheral}.{legal.signal}:</span>
          {#if drag?.target}
            {@const s = statuses[drag.target] ?? "illegal"}
            <span class="target-caption mono">on {drag.target}: {STATUS_GLYPH[s]} {STATUS_LABEL[s]}</span>
          {/if}
          {#each ["current", "free", "connected", "in_use", "needs_override", "illegal"] as const as s (s)}
            <span class="status"><span class="glyph" aria-hidden="true">{STATUS_GLYPH[s]}</span>{STATUS_LABEL[s]}</span>
          {/each}
        {:else}
          {#each FAMILY_ORDER as family (family)}
            <span class="swatch"><span class="chip" style="--chip: {familyColor(family)}"></span>{FAMILY_LABEL[family]}</span>
          {/each}
          <span class="swatch"><span class="chip hatched"></span>⚑ flagged (strapping, USB, JTAG, PSRAM)</span>
          <span class="drag-hint">Drag the <span class="mono">{source}</span> tag to move {legal.peripheral}.{legal.signal}</span>
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

  {#if reviewing}
    <ReviewPanel
      title={reviewTitle}
      proposal={snapshot.proposal}
      withWriteback={snapshot.proposal_writeback}
      onclose={() => show("select")}
      onapply={(choice) => {
        reviewing = false;
        scene = "select";
        applied = `Prototype: nothing applied. The real UI would apply to ${choice === "writeback" ? "puffin.yaml and the schematic" : "puffin.yaml"}.`;
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
    padding: 8px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--surface);
    flex-wrap: wrap;
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
    grid-template-columns: 1fr 380px;
    min-height: 0;
  }
  .map {
    position: relative;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .canvas {
    flex: 1;
    min-height: 0;
    padding: 8px;
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
    display: flex;
    flex-wrap: wrap;
    gap: 6px 16px;
    align-items: center;
    padding: 8px 16px;
    border-top: 1px solid var(--line);
    font-size: 12.5px;
    color: var(--bone);
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
  .chip {
    width: 22px;
    height: 12px;
    border-radius: 6px;
    background: var(--chip);
  }
  .chip.hatched {
    background: repeating-linear-gradient(45deg, var(--bone) 0 2px, transparent 2px 5px);
    border: 1px solid var(--bone);
  }
  .glyph {
    color: var(--cream);
  }
  .target-caption {
    color: var(--cream);
    background: var(--blue);
    border: 1px solid var(--cream);
    border-radius: 10px;
    padding: 0 10px;
  }
  .drag-hint {
    margin-left: auto;
    color: var(--muted);
  }
  .toast {
    position: absolute;
    top: 12px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 2;
    max-width: 80%;
    margin: 0;
    padding: 8px 36px 8px 12px;
    border-radius: var(--radius);
    background: var(--surface-2);
    color: var(--bone);
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
