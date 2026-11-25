<script lang="ts">
  import { familyColor, STATUS_GLYPH, tagText } from "./families";
  import { PX_PER_MM } from "./scale";
  import type { DropStatus, PinInfo, SymbolPin, SymbolView } from "./types";

  export type Drag = {
    source: string; // part pin name, e.g. GPIO10
    pointer: [number, number]; // SVG coordinates
    target: string | null;
  };

  type Props = {
    symbol: SymbolView;
    pins: Record<string, PinInfo>;
    pinByNumber: Record<string, string>;
    selected: string | null;
    draggable: string[]; // pins whose assignment can be dragged
    statuses: Record<string, DropStatus> | null; // set while dragging
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
    drag = $bindable(null),
    onselect,
    ondrop,
  }: Props = $props();

  const S = PX_PER_MM;
  const TAG_H = 18;
  const CHAR = 7.0; // approximate mono glyph width at 11px

  let svg: SVGSVGElement | undefined = $state();

  type Row = {
    number: string;
    name: string; // KiCad pin name, e.g. IO10
    info: PinInfo | undefined;
    p: [number, number];
    b: [number, number];
    dir: [number, number];
    tag: string;
    tagBox: { x: number; y: number; w: number };
    netX: number;
    netAnchor: "start" | "end" | "middle";
    netY: number;
  };

  const DIRS: Record<number, [number, number]> = { 0: [1, 0], 90: [0, -1], 180: [-1, 0], 270: [0, 1] };

  function layout(pin: SymbolPin): Row {
    const info = pins[pinByNumber[pin.number]];
    const p: [number, number] = [pin.point[0] * S, pin.point[1] * S];
    const b: [number, number] = [pin.body[0] * S, pin.body[1] * S];
    const dir = DIRS[pin.outward] ?? [1, 0];
    const tag = info ? tagText(info) : "";
    const w = tag ? tag.length * CHAR + 14 : 0;
    let x = p[0] + dir[0] * 6;
    let y = p[1] + dir[1] * 6 - TAG_H / 2;
    if (dir[0] < 0) x -= w;
    if (dir[0] === 0) {
      x = p[0] - w / 2;
      y = dir[1] < 0 ? p[1] - 6 - TAG_H : p[1] + 6;
    }
    const net = info?.net ?? "";
    let netX = dir[0] < 0 ? x - 8 : x + w + 8;
    let netAnchor: Row["netAnchor"] = dir[0] < 0 ? "end" : "start";
    let netY = p[1] + 4;
    if (dir[0] === 0) {
      netX = p[0];
      netAnchor = "middle";
      netY = dir[1] < 0 ? y - 6 : y + TAG_H + 14;
    }
    if (!tag) {
      netX = dir[0] < 0 ? p[0] - 8 : p[0] + 8;
    }
    return { number: pin.number, name: pin.name, info, p, b, dir, tag, tagBox: { x, y, w }, netX, netAnchor, netY };
  }

  const rows = $derived(symbol.pins.filter((pin) => !pin.hidden).map(layout));

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
      xs.push(row.p[0]);
      ys.push(row.p[1]);
    }
    const pad = { x: 230, y: 80 };
    const minX = Math.min(...xs) - pad.x;
    const minY = Math.min(...ys) - pad.y;
    return { x: minX, y: minY, w: Math.max(...xs) - minX + pad.x, h: Math.max(...ys) - minY + pad.y };
  });

  function inside(row: Row): { x: number; y: number; anchor: "start" | "end" | "middle" } {
    const gap = (symbol.pin_name_offset + 0.5) * S;
    if (row.dir[0] < 0) return { x: row.b[0] + gap, y: row.b[1] + 4, anchor: "start" };
    if (row.dir[0] > 0) return { x: row.b[0] - gap, y: row.b[1] + 4, anchor: "end" };
    return { x: row.b[0], y: row.dir[1] < 0 ? row.b[1] + gap + 8 : row.b[1] - gap, anchor: "middle" };
  }

  function rowBox(row: Row): { x: number; y: number; w: number; h: number } {
    const ins = inside(row);
    const xs = [row.p[0], row.b[0], ins.x, row.tagBox.x, row.tagBox.x + row.tagBox.w];
    const ys = [row.p[1] - 11, row.p[1] + 11];
    if (row.dir[0] === 0) ys.push(row.tagBox.y, row.tagBox.y + TAG_H, ins.y);
    if (row.dir[0] < 0) xs.push(ins.x + row.name.length * CHAR);
    if (row.dir[0] > 0) xs.push(ins.x - row.name.length * CHAR);
    const x = Math.min(...xs) - 6;
    const y = Math.min(...ys) - 2;
    return { x, y, w: Math.max(...xs) - x + 6, h: Math.max(...ys) - y + 2 };
  }

  function toSvg(event: PointerEvent): [number, number] {
    const matrix = svg?.getScreenCTM();
    if (!svg || !matrix) return [0, 0];
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return [point.x, point.y];
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

  function startDrag(event: PointerEvent, row: Row) {
    if (!row.info || !draggable.includes(row.info.pin)) return;
    event.preventDefault();
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
    drag = { source: row.info.pin, pointer: toSvg(event), target: null };
  }

  function moveDrag(event: PointerEvent) {
    if (!drag) return;
    const pointer = toSvg(event);
    const target = nearest(pointer);
    drag = { ...drag, pointer, target: target === drag.source ? null : target };
  }

  function endDrag() {
    if (!drag) return;
    const { source, target } = drag;
    drag = null;
    ondrop(source, target);
  }

  function statusOf(row: Row): DropStatus | null {
    if (!statuses || !row.info) return null;
    return statuses[row.info.pin] ?? "illegal";
  }

  const sourceRow = $derived(drag ? rows.find((r) => r.info?.pin === drag?.source) : undefined);

  function polyline(points: number[][]): string {
    return points.map(([x, y]) => `${x * S},${y * S}`).join(" ");
  }
</script>

<svg
  bind:this={svg}
  class="pinmap"
  class:dragging={drag !== null}
  viewBox="{bounds.x} {bounds.y} {bounds.w} {bounds.h}"
  role="application"
  aria-label="Pin map of {symbol.ref}"
  onpointermove={moveDrag}
  onpointerup={endDrag}
  onpointercancel={() => (drag = null)}
>
  <defs>
    <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="6" stroke="var(--ink)" stroke-width="2" stroke-opacity="0.45" />
    </pattern>
    <pattern id="hatch-light" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="6" stroke="var(--bone)" stroke-width="1.5" stroke-opacity="0.7" />
    </pattern>
  </defs>

  <!-- symbol body, drawn from the schematic's embedded library symbol -->
  {#each symbol.shapes as shape, i (i)}
    {#if shape.kind === "rect"}
      <rect class="body" x={shape.x * S} y={shape.y * S} width={shape.w * S} height={shape.h * S} rx="2" />
    {:else if shape.kind === "polyline"}
      <polyline class="body-line" points={polyline(shape.points)} />
    {:else if shape.kind === "circle"}
      <circle class="body-line" cx={shape.center[0] * S} cy={shape.center[1] * S} r={shape.r * S} />
    {:else if shape.kind === "arc"}
      <polyline class="body-line" points={polyline(shape.points)} />
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
  <text class="refdes" x={bounds.x + bounds.w / 2} y={bounds.y + 34} text-anchor="middle"
    >{symbol.ref}</text
  >

  {#each rows as row (row.number)}
    {@const info = row.info}
    {@const status = statusOf(row)}
    {@const flagged = (info?.flags.length ?? 0) > 0}
    {@const unused = !info || info.family === "unused"}
    {@const color = info ? familyColor(info.family) : "var(--fam-unused)"}
    {@const ins = inside(row)}
    {@const box = rowBox(row)}
    {@const isSelected = info !== undefined && info.pin === selected}
    {@const isTarget = info !== undefined && drag?.target === info.pin}
    {@const hasError = info?.issues.some((i) => i.severity === "error") ?? false}
    <g
      class="pin"
      class:illegal={status === "illegal"}
      class:unused
      data-pin={info?.pin}
      data-status={status}
    >
      {#if isSelected && !drag}
        <rect class="selection" x={box.x} y={box.y} width={box.w} height={box.h} rx="5" />
      {/if}
      <!-- click target for the whole row -->
      <rect
        class="hit"
        x={box.x}
        y={box.y}
        width={box.w}
        height={box.h}
        role="button"
        tabindex={info ? 0 : -1}
        aria-label="{info?.pin ?? row.name}, pin {row.number}{info?.net ? `, net ${info.net}` : ''}"
        onclick={() => info && onselect(info.pin)}
        onkeydown={(e) => (e.key === "Enter" || e.key === " ") && info && onselect(info.pin)}
      />
      <line
        class="pin-line"
        x1={row.p[0]}
        y1={row.p[1]}
        x2={row.b[0]}
        y2={row.b[1]}
        stroke={color}
        stroke-width={unused ? 2 : 3.5}
      />
      <text class="pin-name" class:dim={unused} x={ins.x} y={ins.y} text-anchor={ins.anchor}>{row.name}</text>
      <text
        class="pin-number"
        x={(row.p[0] + row.b[0]) / 2 + (row.dir[0] === 0 ? 7 : 0)}
        y={(row.p[1] + row.b[1]) / 2 - (row.dir[0] === 0 ? -3 : 5)}
        text-anchor={row.dir[0] === 0 ? "start" : "middle"}
        >{row.number}{flagged ? " ⚑" : ""}</text
      >

      {#if row.tag}
        <g
          class="tag"
          class:grab={info !== undefined && draggable.includes(info.pin)}
          role="presentation"
          onpointerdown={(e) => startDrag(e, row)}
        >
          <rect
            x={row.tagBox.x}
            y={row.tagBox.y}
            width={row.tagBox.w}
            height={TAG_H}
            rx="9"
            fill={color}
            stroke={hasError ? "var(--error)" : "none"}
            stroke-width="2"
          />
          {#if flagged}
            <rect x={row.tagBox.x} y={row.tagBox.y} width={row.tagBox.w} height={TAG_H} rx="9" fill="url(#hatch)" />
          {/if}
          <text class="tag-text" x={row.tagBox.x + row.tagBox.w / 2} y={row.tagBox.y + 13} text-anchor="middle"
            >{row.tag}</text
          >
        </g>
      {:else if flagged}
        <rect class="flag-mark" x={row.p[0] + row.dir[0] * 12 - 6} y={row.p[1] + row.dir[1] * 12 - 6}
              width="12" height="12" rx="2" fill="url(#hatch-light)" />
      {/if}
      {#if info?.net}
        <text class="net" x={row.netX} y={row.netY} text-anchor={row.netAnchor}>{info.net}</text>
      {/if}
      {#if hasError}
        <text class="error-glyph" x={row.p[0]} y={row.p[1] - 12} text-anchor="middle">!</text>
      {/if}

      {#if status && status !== "illegal"}
        <circle
          class="ring {status}"
          class:target={isTarget}
          cx={row.p[0]}
          cy={row.p[1]}
          r={isTarget ? 11 : 8}
        />
        <text class="ring-glyph" class:big={isTarget} x={row.p[0]} y={row.p[1] + 3.5} text-anchor="middle"
          >{STATUS_GLYPH[status]}</text
        >
      {/if}
    </g>
  {/each}

  {#if drag && sourceRow}
    <line
      class="drag-line"
      x1={sourceRow.p[0]}
      y1={sourceRow.p[1]}
      x2={drag.pointer[0]}
      y2={drag.pointer[1]}
    />
    <g class="ghost" transform="translate({drag.pointer[0] - sourceRow.tagBox.w - 14} {drag.pointer[1] - TAG_H - 14})">
      <rect width={sourceRow.tagBox.w} height={TAG_H} rx="9" fill={familyColor(sourceRow.info?.family ?? "unused")} />
      <text class="tag-text" x={sourceRow.tagBox.w / 2} y="13" text-anchor="middle">{sourceRow.tag}</text>
    </g>
  {/if}
</svg>

<style>
  .pinmap {
    width: 100%;
    height: 100%;
    user-select: none;
    touch-action: none;
  }
  .body {
    fill: var(--surface-2);
    stroke: var(--bone);
    stroke-width: 2;
  }
  .body-line {
    fill: none;
    stroke: var(--muted);
    stroke-width: 1.2;
  }
  .body-text,
  .refdes {
    fill: var(--muted);
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .refdes {
    font-size: 14px;
    fill: var(--bone);
    font-weight: 600;
  }
  .hit {
    fill: transparent;
    cursor: pointer;
  }
  .hit:focus-visible {
    outline: none;
    stroke: var(--cream);
    stroke-width: 1.5;
    stroke-dasharray: 3 2;
  }
  .selection {
    fill: var(--blue);
    fill-opacity: 0.75;
    stroke: var(--cream);
    stroke-width: 1.5;
  }
  .pin-line {
    stroke-linecap: round;
    pointer-events: none;
  }
  .pin-name {
    fill: var(--cream);
    font-family: var(--font-mono);
    font-size: 11px;
    pointer-events: none;
  }
  .pin-name.dim {
    fill: var(--muted);
  }
  .pin-number {
    fill: var(--muted);
    font-family: var(--font-mono);
    font-size: 9px;
    pointer-events: none;
  }
  .tag-text {
    fill: var(--ink);
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    pointer-events: none;
  }
  .tag.grab {
    cursor: grab;
  }
  .tag.grab rect:first-child {
    stroke: var(--cream);
    stroke-width: 1.5;
  }
  .dragging .tag.grab {
    cursor: grabbing;
  }
  .net {
    fill: var(--bone);
    font-family: var(--font-mono);
    font-size: 11px;
    pointer-events: none;
  }
  .flag-mark {
    stroke: var(--bone);
    stroke-width: 1;
    pointer-events: none;
  }
  .error-glyph {
    fill: var(--error-text);
    font-weight: 600;
  }
  .pin.illegal {
    opacity: 0.28;
  }
  .ring {
    fill: var(--bg);
    stroke-width: 2.2;
    pointer-events: none;
  }
  .ring.current {
    stroke: var(--cream);
    stroke-width: 3;
  }
  .ring.free {
    stroke: var(--bone);
  }
  .ring.connected {
    stroke: var(--bone);
    stroke-dasharray: 1.5 3;
  }
  .ring.in_use {
    stroke: var(--bone);
    stroke-dasharray: 5 3;
  }
  .ring.needs_override {
    stroke: var(--bone);
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
    opacity: 0.92;
  }
</style>
