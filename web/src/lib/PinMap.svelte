<script lang="ts">
  import { cellColor, type Swatch } from "./dither";
  import { doing, familySwatch, FAMILY_LABEL, STATUS_GLYPH, tagText } from "./families";
  import { PX_PER_MM } from "./scale";
  import type { DropStatus, Family, PinInfo, SymbolPin, SymbolView } from "./types";

  export type Drag = {
    source: string; // part pin name, e.g. GPIO10
    pointer: [number, number]; // SVG user coordinates
    target: string | null;
  };

  type Props = {
    symbol: SymbolView;
    pins: Record<string, PinInfo>;
    pinByNumber: Record<string, string>;
    selected: string | null;
    draggable: string[];
    statuses: Record<string, DropStatus> | null;
    highlight?: string[]; // pins to outline, e.g. the pins of a bus selected in the block diagram
    drag?: Drag | null;
    onselect: (pin: string) => void;
    ondrop: (source: string, target: string | null) => void;
  };

  let {
    symbol,
    pins,
    pinByNumber,
    selected,
    draggable,
    statuses,
    highlight = [],
    drag = $bindable(null),
    onselect,
    ondrop,
  }: Props = $props();

  const S = PX_PER_MM;
  const TAG_H = 18;
  const CHAR = 7.0; // mono glyph width at 11px, user units
  const STRIP = 10; // dithered strip at the pin end of a tag
  const STUB = 6; // pin stub thickness
  const DRAG_THRESHOLD = 5; // CSS px
  const HOVER_DELAY = 300; // ms
  const DITHER_CELL = 2; // CSS px per dither cell, rounded to whole device pixels

  let wrap = $state<HTMLDivElement | undefined>();
  let svg = $state<SVGSVGElement | undefined>();

  // ---- layout (computed once from the symbol; nothing here changes during interaction) ----

  type Row = {
    number: string;
    name: string;
    info: PinInfo | undefined;
    p: [number, number];
    b: [number, number];
    dir: [number, number];
    tag: string;
    tagBox: { x: number; y: number; w: number };
    netX: number;
    netAnchor: "start" | "end" | "middle";
    netY: number;
    flagX: number;
  };

  const DIRS: Record<number, [number, number]> = { 0: [1, 0], 90: [0, -1], 180: [-1, 0], 270: [0, 1] };

  function layout(pin: SymbolPin): Row {
    const info = pins[pinByNumber[pin.number]];
    const p: [number, number] = [pin.point[0] * S, pin.point[1] * S];
    const b: [number, number] = [pin.body[0] * S, pin.body[1] * S];
    const dir = DIRS[pin.outward] ?? [1, 0];
    const tag = info ? tagText(info) : "";
    const w = tag ? tag.length * CHAR + 14 + STRIP : 0;
    const flagged = (info?.flags.length ?? 0) > 0;
    let x = p[0] + dir[0] * 6;
    let y = p[1] - TAG_H / 2;
    if (dir[0] < 0) x -= w;
    if (dir[0] === 0) {
      x = p[0] - w / 2;
      y = dir[1] < 0 ? p[1] - 6 - TAG_H : p[1] + 6;
    }
    const outerEdge = dir[0] < 0 ? x : x + w;
    const flagX = tag ? outerEdge + dir[0] * 10 : p[0] + dir[0] * 12;
    const netStart = (tag || flagged ? (flagged ? flagX : outerEdge) : p[0]) + dir[0] * (flagged ? 12 : 8);
    let netX = netStart;
    let netAnchor: Row["netAnchor"] = dir[0] < 0 ? "end" : "start";
    let netY = p[1] + 4;
    if (dir[0] === 0) {
      netX = p[0];
      netAnchor = "middle";
      netY = dir[1] < 0 ? y - 6 : y + TAG_H + 14;
    }
    return { number: pin.number, name: pin.name, info, p, b, dir, tag, tagBox: { x, y, w }, netX, netAnchor, netY, flagX };
  }

  const rows = $derived(symbol.pins.filter((pin) => !pin.hidden).map(layout));

  function inside(row: Row): { x: number; y: number; anchor: "start" | "end" | "middle" } {
    const gap = (symbol.pin_name_offset + 0.5) * S;
    if (row.dir[0] < 0) return { x: row.b[0] + gap, y: row.b[1] + 4, anchor: "start" };
    if (row.dir[0] > 0) return { x: row.b[0] - gap, y: row.b[1] + 4, anchor: "end" };
    return { x: row.b[0], y: row.dir[1] < 0 ? row.b[1] + gap + 8 : row.b[1] - gap, anchor: "middle" };
  }

  function rowBox(row: Row): { x: number; y: number; w: number; h: number } {
    const ins = inside(row);
    const xs = [row.p[0], row.b[0], ins.x, row.tagBox.x, row.tagBox.x + row.tagBox.w, row.flagX];
    const ys = [row.p[1] - 11, row.p[1] + 11];
    if (row.dir[0] === 0) ys.push(row.tagBox.y, row.tagBox.y + TAG_H, ins.y);
    if (row.dir[0] < 0) xs.push(ins.x + row.name.length * CHAR);
    if (row.dir[0] > 0) xs.push(ins.x - row.name.length * CHAR);
    const x = Math.min(...xs) - 6;
    const y = Math.min(...ys) - 2;
    return { x, y, w: Math.max(...xs) - x + 6, h: Math.max(...ys) - y + 2 };
  }

  const bounds = $derived.by(() => {
    const xs: number[] = [];
    const ys: number[] = [];
    for (const shape of symbol.shapes) {
      if (shape.kind === "rect") {
        xs.push(shape.x * S, (shape.x + shape.w) * S);
        ys.push(shape.y * S, (shape.y + shape.h) * S);
      }
    }
    for (const row of rows) {
      const box = rowBox(row);
      xs.push(box.x - 140, box.x + box.w + 140); // room for net labels
      ys.push(box.y, box.y + box.h);
    }
    const pad = 30;
    const minX = Math.min(...xs) - pad;
    const minY = Math.min(...ys) - pad - 20;
    return { x: minX, y: minY, w: Math.max(...xs) - minX + pad, h: Math.max(...ys) - minY + pad };
  });

  // ---- view: changes only by explicit zoom or pan ----

  let zoom = $state(1);
  let center = $state<[number, number] | null>(null);
  const c = $derived(center ?? [bounds.x + bounds.w / 2, bounds.y + bounds.h / 2]);
  const viewBox = $derived.by(() => {
    const w = bounds.w / zoom;
    const h = bounds.h / zoom;
    return { x: c[0] - w / 2, y: c[1] - h / 2, w, h };
  });

  function zoomBy(factor: number, at?: [number, number]) {
    const next = Math.min(6, Math.max(0.6, zoom * factor));
    const anchor = at ?? c;
    const k = zoom / next;
    center = [anchor[0] + (c[0] - anchor[0]) * k, anchor[1] + (c[1] - anchor[1]) * k];
    zoom = next;
  }

  function fit() {
    zoom = 1;
    center = null;
  }

  // ---- screen-pixel alignment for dither patterns ----

  let dpr = $state(typeof window === "undefined" ? 1 : window.devicePixelRatio || 1);
  let align = $state({ a: 1, e: 0, f: 0 });

  function measure() {
    const ctm = svg?.getScreenCTM();
    if (!ctm) return;
    dpr = window.devicePixelRatio || 1;
    if (ctm.a !== align.a || ctm.e !== align.e || ctm.f !== align.f) {
      align = { a: ctm.a, e: ctm.e, f: ctm.f };
    }
  }

  $effect(() => {
    void viewBox; // effects run after the DOM update, so the new viewBox is already applied
    measure();
  });

  $effect(() => {
    if (!svg) return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(svg);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  });

  const cellCss = $derived(Math.max(1, Math.round(DITHER_CELL * dpr)) / dpr);
  const pattern = $derived({
    cell: cellCss / align.a, // user units per dither cell = whole device pixels on screen
    x: -align.e / align.a, // pattern origin = the viewport's origin, so cells sit on the pixel grid
    y: -align.f / align.a,
  });

  const FAMILIES = ["gpio", "spi", "i2c", "uart", "adc", "usb", "power", "ground"] as const;
  const cells = Array.from({ length: 16 }, (_, i) => [i % 4, Math.floor(i / 4)] as const);

  function patternId(family: Family): string {
    return `dither-${family}`;
  }

  // ---- hover tooltip ----

  let hoverTimer: ReturnType<typeof setTimeout> | null = null;
  let tooltip = $state<{ row: Row } | null>(null);

  function hoverStart(row: Row) {
    if (drag || pending || !row.info) return;
    clearHover();
    hoverTimer = setTimeout(() => (tooltip = { row }), HOVER_DELAY);
  }

  function clearHover() {
    if (hoverTimer) clearTimeout(hoverTimer);
    hoverTimer = null;
    tooltip = null;
  }

  function clientOf(x: number, y: number): [number, number] {
    const wrapRect = wrap?.getBoundingClientRect();
    return [align.e + align.a * x - (wrapRect?.left ?? 0), align.f + align.a * y - (wrapRect?.top ?? 0)];
  }

  const tip = $derived.by(() => {
    if (!tooltip) return null;
    const box = rowBox(tooltip.row);
    const [left, top] = clientOf(box.x, box.y);
    const [right, bottom] = clientOf(box.x + box.w, box.y + box.h);
    const above = top > 130;
    const leftSide = tooltip.row.dir[0] <= 0;
    return {
      info: tooltip.row.info!,
      name: tooltip.row.name,
      x: leftSide ? left : right,
      y: above ? top - 8 : bottom + 8,
      transform: `translate(${leftSide ? "0" : "-100%"}, ${above ? "-100%" : "0"})`,
    };
  });

  // ---- pointer: select on down, drag only past a threshold, Escape cancels ----

  let pending: { pin: string; x: number; y: number; id: number } | null = null;
  let panning: { x: number; y: number; center: [number, number] } | null = null;

  function toSvg(event: { clientX: number; clientY: number }): [number, number] {
    return [(event.clientX - align.e) / align.a, (event.clientY - align.f) / align.a];
  }

  function nearest(at: [number, number]): string | null {
    let best: { pin: string; d: number } | null = null;
    for (const row of rows) {
      if (!row.info) continue;
      const box = rowBox(row);
      const inBox = at[0] >= box.x && at[0] <= box.x + box.w && at[1] >= box.y && at[1] <= box.y + box.h;
      const d = Math.hypot(at[0] - row.p[0], at[1] - row.p[1]);
      if ((inBox || d < 16) && (!best || d < best.d)) best = { pin: row.info.pin, d };
    }
    return best?.pin ?? null;
  }

  function pinDown(event: PointerEvent, row: Row) {
    if (!row.info || event.button !== 0) return;
    event.stopPropagation();
    clearHover();
    onselect(row.info.pin);
    if (draggable.includes(row.info.pin)) {
      pending = { pin: row.info.pin, x: event.clientX, y: event.clientY, id: event.pointerId };
    }
  }

  function backgroundDown(event: PointerEvent) {
    if (event.button !== 0) return;
    clearHover();
    panning = { x: event.clientX, y: event.clientY, center: [c[0], c[1]] };
  }

  function move(event: PointerEvent) {
    if (panning) {
      const dx = (event.clientX - panning.x) / align.a;
      const dy = (event.clientY - panning.y) / align.a;
      center = [panning.center[0] - dx, panning.center[1] - dy];
      return;
    }
    if (pending && !drag) {
      if (Math.hypot(event.clientX - pending.x, event.clientY - pending.y) < DRAG_THRESHOLD) return;
      svg?.setPointerCapture(pending.id);
      drag = { source: pending.pin, pointer: toSvg(event), target: null };
      clearHover();
    }
    if (drag) {
      const pointer = toSvg(event);
      const target = nearest(pointer);
      drag = { ...drag, pointer, target: target === drag.source ? null : target };
    }
  }

  function up() {
    panning = null;
    pending = null;
    if (!drag) return;
    const { source, target } = drag;
    drag = null;
    ondrop(source, target);
  }

  function cancel() {
    pending = null;
    panning = null;
    drag = null;
  }

  $effect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && (drag || pending)) cancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function wheel(event: WheelEvent) {
    event.preventDefault();
    clearHover();
    if (event.ctrlKey || event.metaKey) {
      zoomBy(Math.exp(-event.deltaY * 0.01), toSvg(event));
    } else {
      center = [c[0] + event.deltaX / align.a, c[1] + event.deltaY / align.a];
    }
  }

  function statusOf(row: Row): DropStatus | null {
    if (!statuses || !row.info) return null;
    return statuses[row.info.pin] ?? "illegal";
  }

  function swatchOf(info: PinInfo | undefined): Swatch | null {
    return info ? familySwatch(info.family) : null;
  }

  function stub(row: Row): { x: number; y: number; w: number; h: number } {
    if (row.dir[1] === 0) {
      return { x: Math.min(row.p[0], row.b[0]), y: row.p[1] - STUB / 2, w: Math.abs(row.p[0] - row.b[0]), h: STUB };
    }
    return { x: row.p[0] - STUB / 2, y: Math.min(row.p[1], row.b[1]), w: STUB, h: Math.abs(row.p[1] - row.b[1]) };
  }

  function strip(row: Row): number {
    // the strip sits at the tag's pin end, so stub and strip read as one swatch
    return row.dir[0] < 0 ? row.tagBox.x + row.tagBox.w - STRIP : row.tagBox.x;
  }

  function textX(row: Row): number {
    const start = row.dir[0] < 0 ? row.tagBox.x : row.tagBox.x + STRIP;
    return start + (row.tagBox.w - STRIP) / 2;
  }

  const sourceRow = $derived(drag ? rows.find((r) => r.info?.pin === drag?.source) : undefined);

  function polyline(points: number[][]): string {
    return points.map(([x, y]) => `${x * S},${y * S}`).join(" ");
  }
</script>

<div class="wrap" bind:this={wrap}>
  <svg
    bind:this={svg}
    class="pinmap"
    class:dragging={drag !== null}
    viewBox="{viewBox.x} {viewBox.y} {viewBox.w} {viewBox.h}"
    role="application"
    aria-label="Pin map of {symbol.ref}"
    onpointerdown={backgroundDown}
    onpointermove={move}
    onpointerup={up}
    onpointercancel={cancel}
    onwheel={wheel}
  >
    <defs>
      {#each FAMILIES as family (family)}
        {@const swatch = familySwatch(family)}
        {#if swatch}
          <pattern
            id={patternId(family)}
            patternUnits="userSpaceOnUse"
            x={pattern.x}
            y={pattern.y}
            width={pattern.cell * 4}
            height={pattern.cell * 4}
          >
            {#each cells as [cx, cy] (cx * 4 + cy)}
              <rect
                x={cx * pattern.cell}
                y={cy * pattern.cell}
                width={pattern.cell}
                height={pattern.cell}
                fill={cellColor(swatch, cx, cy)}
                shape-rendering="crispEdges"
              />
            {/each}
          </pattern>
        {/if}
      {/each}
      <pattern id="flag-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <line x1="0" y1="0" x2="0" y2="5" stroke="var(--bone)" stroke-width="1.5" />
      </pattern>
    </defs>

    {#each symbol.shapes as shape, i (i)}
      {#if shape.kind === "rect"}
        <rect class="body" x={shape.x * S} y={shape.y * S} width={shape.w * S} height={shape.h * S} rx="2" />
      {:else if shape.kind === "polyline" || shape.kind === "arc"}
        <polyline class="body-line" points={polyline(shape.points)} />
      {:else if shape.kind === "circle"}
        <circle class="body-line" cx={shape.center[0] * S} cy={shape.center[1] * S} r={shape.r * S} />
      {:else if shape.kind === "text"}
        <text
          class="body-text"
          x={shape.at[0] * S}
          y={shape.at[1] * S}
          transform="rotate({-shape.angle} {shape.at[0] * S} {shape.at[1] * S})"
          text-anchor="middle">{shape.text}</text
        >
      {/if}
    {/each}
    <text class="refdes" x={bounds.x + bounds.w / 2} y={bounds.y + 26} text-anchor="middle">{symbol.ref}</text>

    {#each rows as row (row.number)}
      {@const info = row.info}
      {@const status = statusOf(row)}
      {@const flagged = (info?.flags.length ?? 0) > 0}
      {@const swatch = swatchOf(info)}
      {@const ins = inside(row)}
      {@const box = rowBox(row)}
      {@const isSelected = info !== undefined && info.pin === selected && !drag}
      {@const isTarget = info !== undefined && drag?.target === info.pin}
      {@const hasError = info?.issues.some((i) => i.severity === "error") ?? false}
      {@const hasWarning = !hasError && (info?.issues.some((i) => i.severity === "warning") ?? false)}
      {@const grab = info !== undefined && draggable.includes(info.pin)}
      <g class="pin" class:illegal={status === "illegal"} data-pin={info?.pin} data-status={status}>
        {#if isSelected}
          <rect class="selection" x={box.x} y={box.y} width={box.w} height={box.h} rx="5" />
        {:else if info && highlight.includes(info.pin) && !drag}
          <rect class="highlight" x={box.x} y={box.y} width={box.w} height={box.h} rx="5" />
        {/if}
        <rect
          class="hit"
          class:grab
          x={box.x}
          y={box.y}
          width={box.w}
          height={box.h}
          role="button"
          tabindex={info ? 0 : -1}
          aria-label="{info?.pin ?? row.name}, pin {row.number}{info?.net ? `, net ${info.net}` : ''}"
          onpointerdown={(e) => pinDown(e, row)}
          onpointerenter={() => hoverStart(row)}
          onpointerleave={clearHover}
          onkeydown={(e) => (e.key === "Enter" || e.key === " ") && info && onselect(info.pin)}
        />
        {#if swatch && info}
          {@const s = stub(row)}
          <rect class="stub" x={s.x} y={s.y} width={s.w} height={s.h} fill="url(#{patternId(info.family)})" />
        {:else}
          <line class="idle" x1={row.p[0]} y1={row.p[1]} x2={row.b[0]} y2={row.b[1]} />
        {/if}
        <text class="pin-name" class:dim={!swatch} x={ins.x} y={ins.y} text-anchor={ins.anchor}>{row.name}</text>
        <text
          class="pin-number"
          x={(row.p[0] + row.b[0]) / 2 + (row.dir[0] === 0 ? 8 : 0)}
          y={(row.p[1] + row.b[1]) / 2 - (row.dir[0] === 0 ? -3 : 6)}
          text-anchor={row.dir[0] === 0 ? "start" : "middle"}>{row.number}</text
        >

        {#if row.tag && info}
          <g class="tag">
            <rect class="tag-bg" class:error={hasError} class:warning={hasWarning} x={row.tagBox.x} y={row.tagBox.y} width={row.tagBox.w} height={TAG_H} rx="3" />
            {#if swatch}
              <rect x={strip(row)} y={row.tagBox.y + 1} width={STRIP} height={TAG_H - 2} fill="url(#{patternId(info.family)})" />
            {/if}
            <text class="tag-text" x={textX(row)} y={row.tagBox.y + 13} text-anchor="middle">{row.tag}</text>
          </g>
        {/if}
        {#if hasError || hasWarning}
          <!-- errors and warnings always carry a glyph as well as the outline (spec §7) -->
          <text
            class="state-glyph"
            class:error={hasError}
            x={row.tag ? (row.dir[0] < 0 ? row.tagBox.x : row.tagBox.x + row.tagBox.w) : row.p[0]}
            y={row.tagBox.y - 3}
            text-anchor="middle">{hasError ? "✕" : "!"}</text
          >
        {/if}
        {#if flagged}
          <g class="flag" aria-label="flagged: {info?.flags.join(', ')}">
            <rect x={row.flagX - 6} y={row.p[1] - 6} width="12" height="12" rx="2" class="flag-box" />
            <rect x={row.flagX - 6} y={row.p[1] - 6} width="12" height="12" rx="2" fill="url(#flag-hatch)" />
          </g>
        {/if}
        {#if info?.net}
          <text class="net" x={row.netX} y={row.netY} text-anchor={row.netAnchor}>{info.net}</text>
        {/if}

        {#if status && status !== "illegal"}
          <circle class="ring {status}" class:target={isTarget} cx={row.p[0]} cy={row.p[1]} r={isTarget ? 11 : 8} />
          <text class="ring-glyph" class:big={isTarget} x={row.p[0]} y={row.p[1] + 3.5} text-anchor="middle"
            >{STATUS_GLYPH[status]}</text
          >
        {/if}
      </g>
    {/each}

    {#if drag && sourceRow && sourceRow.info}
      <line class="drag-line" x1={sourceRow.p[0]} y1={sourceRow.p[1]} x2={drag.pointer[0]} y2={drag.pointer[1]} />
      <g class="ghost" transform="translate({drag.pointer[0] - sourceRow.tagBox.w - 14} {drag.pointer[1] - TAG_H - 14})">
        <rect class="tag-bg" width={sourceRow.tagBox.w} height={TAG_H} rx="3" />
        <rect x={sourceRow.tagBox.w - STRIP} y="1" width={STRIP} height={TAG_H - 2} fill="url(#{patternId(sourceRow.info.family)})" />
        <text class="tag-text" x={(sourceRow.tagBox.w - STRIP) / 2} y="13" text-anchor="middle">{sourceRow.tag}</text>
      </g>
    {/if}
  </svg>

  <div class="zoom" role="group" aria-label="Zoom">
    <button aria-label="Zoom in" onclick={() => zoomBy(1.25)}>+</button>
    <button aria-label="Zoom out" onclick={() => zoomBy(0.8)}>−</button>
    <button aria-label="Fit" onclick={fit}>fit</button>
    <span class="zoom-level mono">{Math.round(zoom * 100)}%</span>
  </div>

  {#if tip && !drag}
    <div class="tooltip" role="tooltip" style="left: {tip.x}px; top: {tip.y}px; transform: {tip.transform}">
      <div class="tip-head mono"><strong>{tip.info.pin}</strong> · {tip.name} · pin {tip.info.numbers.join(", ")}</div>
      <div class="tip-doing">{doing(tip.info)}</div>
      <div class="tip-family">{FAMILY_LABEL[tip.info.family]}{tip.info.flags.length ? ` · ⚑ ${tip.info.flags.join(", ")}` : ""}</div>
      {#if tip.info.description}
        <div class="tip-desc mono">{tip.info.description}</div>
        <div class="tip-cite">Datasheet: {tip.info.description_source}</div>
      {:else}
        <div class="tip-cite">No datasheet description for this part.</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .wrap {
    position: relative;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }
  .pinmap {
    display: block;
    width: 100%;
    height: 100%;
    user-select: none;
    touch-action: none;
    cursor: default;
  }
  .body {
    fill: var(--surface);
    stroke: var(--bone);
    stroke-width: 2;
  }
  .body-line {
    fill: none;
    stroke: var(--muted);
    stroke-width: 1.2;
  }
  .body-text {
    fill: var(--muted);
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .refdes {
    fill: var(--bone);
    font-family: var(--font-mono);
    font-size: 14px;
    font-weight: 600;
  }
  .hit {
    fill: transparent;
    cursor: pointer;
  }
  .hit.grab {
    cursor: grab;
  }
  .dragging .hit {
    cursor: grabbing;
  }
  .hit:focus-visible {
    outline: none;
    stroke: var(--cream);
    stroke-width: 1.5;
    stroke-dasharray: 3 2;
  }
  .highlight {
    fill: none;
    stroke: var(--cream);
    stroke-width: 1.5;
    stroke-dasharray: 4 3;
  }
  .selection {
    fill: var(--blue);
    stroke: var(--cream);
    stroke-width: 1.5;
  }
  .stub,
  .idle,
  .tag,
  .flag,
  .net,
  .pin-name,
  .pin-number {
    pointer-events: none;
  }
  .idle {
    stroke: var(--pin-idle);
    stroke-width: 1.5;
  }
  .pin-name {
    fill: var(--cream);
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .pin-name.dim {
    fill: var(--muted);
  }
  .pin-number {
    fill: var(--muted);
    font-family: var(--font-mono);
    font-size: 9px;
  }
  .tag-bg {
    fill: var(--surface);
    stroke: var(--bone);
    stroke-width: 1;
  }
  .tag-bg.error {
    stroke: var(--error);
    stroke-width: 2;
  }
  .tag-bg.warning {
    stroke: var(--warning);
    stroke-width: 2;
  }
  .state-glyph {
    fill: var(--warning);
    font-size: 12px;
    font-weight: 600;
    pointer-events: none;
  }
  .state-glyph.error {
    fill: var(--error-text);
  }
  .tag-text {
    fill: var(--cream);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
  }
  .flag-box {
    fill: var(--bg);
    stroke: var(--bone);
    stroke-width: 1;
  }
  .net {
    fill: var(--bone);
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .pin.illegal {
    opacity: 0.28;
  }
  .ring {
    fill: var(--bg);
    stroke: var(--bone);
    stroke-width: 2.2;
    pointer-events: none;
  }
  .ring.current {
    stroke: var(--cream);
    stroke-width: 3;
  }
  .ring.connected {
    stroke-dasharray: 1.5 3;
  }
  .ring.in_use {
    stroke-dasharray: 5 3;
  }
  .ring.needs_override {
    stroke-dasharray: 9 3;
  }
  .ring.target {
    stroke: var(--cream);
    stroke-width: 3.5;
  }
  .ring-glyph {
    fill: var(--cream);
    font-size: 9px;
    pointer-events: none;
  }
  .ring-glyph.big {
    font-size: 12px;
    font-weight: 600;
  }
  .drag-line {
    stroke: var(--cream);
    stroke-width: 1.5;
    stroke-dasharray: 4 4;
    pointer-events: none;
  }
  .ghost {
    pointer-events: none;
  }
  .zoom {
    position: absolute;
    right: 10px;
    top: 10px;
    display: flex;
    gap: 4px;
    align-items: center;
  }
  .zoom button {
    min-width: 28px;
    height: 26px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--bone);
    cursor: pointer;
  }
  .zoom-level {
    width: 44px;
    text-align: right;
    font-size: 12px;
    color: var(--muted);
  }
  .tooltip {
    position: absolute;
    z-index: 3;
    max-width: 340px;
    padding: 8px 10px;
    background: var(--surface);
    border: 1px solid var(--bone);
    border-radius: var(--radius);
    color: var(--bone);
    font-size: 12.5px;
    pointer-events: none;
    box-shadow: 0 6px 18px rgb(0 0 0 / 0.35);
  }
  .tip-head {
    color: var(--cream);
    font-size: 12px;
  }
  .tip-doing {
    color: var(--cream);
    margin: 2px 0;
  }
  .tip-family {
    color: var(--bone);
  }
  .tip-desc {
    margin-top: 6px;
    font-size: 11.5px;
    color: var(--bone);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tip-cite {
    margin-top: 2px;
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>
