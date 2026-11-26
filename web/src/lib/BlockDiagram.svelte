<script lang="ts">
  // Block diagram (M4 §f): boards as containers, components as blocks, buses grouped by
  // peripheral (from the service's block_view). Laid out by ELK; read-only except selection.
  import ELK from "elkjs/lib/elk.bundled.js";
  import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk-api";

  export type BlockEdge = {
    id: string;
    source: string;
    target: string;
    kind: "bus" | "net" | "rail";
    label: string;
    nets: string[];
    peripherals: string[];
    cross_board: boolean;
  };
  export type Blocks = {
    boards: { id: string; kicad_schematic: string | null; components: string[] }[];
    nodes: { id: string; part: string; board: string; mcu: boolean }[];
    edges: BlockEdge[];
  };

  type Props = {
    blocks: Blocks;
    selected: string | null; // component
    selectedEdge: string | null;
    onselect: (component: string) => void;
    onselectedge: (edge: BlockEdge) => void;
  };
  let { blocks, selected, selectedEdge, onselect, onselectedge }: Props = $props();

  let showRails = $state(false);
  let layout = $state<ElkNode | null>(null);
  let failed = $state("");
  const elk = new ELK();

  const visible = $derived(blocks.edges.filter((e) => showRails || e.kind !== "rail"));

  $effect(() => {
    const graph: ElkNode = {
      id: "root",
      layoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": "RIGHT",
        "elk.hierarchyHandling": "INCLUDE_CHILDREN",
        "elk.edgeRouting": "ORTHOGONAL",
        "elk.spacing.nodeNode": "40",
        "elk.layered.spacing.nodeNodeBetweenLayers": "90",
        "elk.padding": "[top=24,left=24,bottom=24,right=24]",
      },
      children: blocks.boards.map((board) => ({
        id: `board:${board.id}`,
        layoutOptions: { "elk.padding": "[top=34,left=20,bottom=20,right=20]" },
        children: blocks.nodes
          .filter((n) => n.board === board.id)
          .map((n) => ({ id: n.id, width: Math.max(120, n.part.length * 6.6 + 24), height: 56 })),
      })),
      edges: visible.map(
        (e): ElkExtendedEdge => ({
          id: e.id,
          sources: [e.source],
          targets: [e.target],
          labels: [{ text: e.label, width: e.label.length * 7 + 8, height: 14 }],
        }),
      ),
    };
    elk
      .layout(graph)
      .then((result) => ((layout = result), (failed = "")))
      .catch((err: unknown) => (failed = String(err)));
  });

  type Placed = { id: string; x: number; y: number; w: number; h: number };
  const boardsPlaced = $derived(
    (layout?.children ?? []).map((b) => ({ id: b.id.slice(6), x: b.x ?? 0, y: b.y ?? 0, w: b.width ?? 0, h: b.height ?? 0 })),
  );
  const nodesPlaced = $derived(
    (layout?.children ?? []).flatMap((b) =>
      (b.children ?? []).map((n): Placed => ({ id: n.id, x: (b.x ?? 0) + (n.x ?? 0), y: (b.y ?? 0) + (n.y ?? 0), w: n.width ?? 0, h: n.height ?? 0 })),
    ),
  );
  // ELK gives edge coordinates relative to the edge's container: the board when both ends sit
  // on the same board, otherwise the root. Shift same-board edges by their board's position.
  const edgesPlaced = $derived(
    (layout?.edges ?? []).map((e) => {
      const edge = blocks.edges.find((x) => x.id === e.id)!;
      const boardOf = (id: string) => blocks.nodes.find((n) => n.id === id)?.board;
      const container = !edge.cross_board ? boardsPlaced.find((b) => b.id === boardOf(edge.source)) : undefined;
      const dx = container?.x ?? 0;
      const dy = container?.y ?? 0;
      const section = (e as ElkExtendedEdge).sections?.[0];
      const raw = section ? [section.startPoint, ...(section.bendPoints ?? []), section.endPoint] : [];
      const points = raw.map((p) => ({ x: p.x + dx, y: p.y + dy }));
      const elkLabel = (e as ElkExtendedEdge).labels?.[0];
      const label = elkLabel ? { ...elkLabel, x: (elkLabel.x ?? 0) + dx, y: (elkLabel.y ?? 0) + dy } : undefined;
      return { id: e.id, points, label, edge };
    }),
  );
  const size = $derived({ w: layout?.width ?? 400, h: layout?.height ?? 300 });
  const node = (id: string) => blocks.nodes.find((n) => n.id === id);
</script>

<div class="blocks">
  <div class="toolbar">
    <label><input type="checkbox" bind:checked={showRails} /> show power and ground rails</label>
    {#if failed}<span class="failed">layout failed: {failed}</span>{/if}
  </div>
  <svg viewBox="0 0 {size.w} {size.h}" role="img" aria-label="Block diagram">
    {#each boardsPlaced as b (b.id)}
      <g class="board">
        <rect x={b.x} y={b.y} width={b.w} height={b.h} rx="6" />
        <text x={b.x + 12} y={b.y + 20}>board {b.id}</text>
      </g>
    {/each}
    {#each edgesPlaced as e (e.id)}
      {#if e.points.length}
        <g
          class="edge {e.edge.kind}"
          class:cross={e.edge.cross_board}
          class:selected={selectedEdge === e.id}
          role="button"
          tabindex="0"
          aria-label="{e.edge.label}: {e.edge.nets.join(', ')}"
          onclick={() => onselectedge(e.edge)}
          onkeydown={(ev) => ev.key === "Enter" && onselectedge(e.edge)}
        >
          <polyline class="hitline" points={e.points.map((p) => `${p.x},${p.y}`).join(" ")} />
          <polyline class="line" points={e.points.map((p) => `${p.x},${p.y}`).join(" ")} />
          {#if e.label}
            <rect class="label-bg" x={(e.label.x ?? 0) - 4} y={(e.label.y ?? 0) - 1} width={(e.label.width ?? 0)} height="16" rx="3" />
            <text class="label" x={e.label.x ?? 0} y={(e.label.y ?? 0) + 11}>{e.label.text}</text>
          {/if}
        </g>
      {/if}
    {/each}
    {#each nodesPlaced as n (n.id)}
      {@const info = node(n.id)}
      <g
        class="node"
        class:mcu={info?.mcu}
        class:selected={selected === n.id}
        role="button"
        tabindex="0"
        aria-label="{n.id} {info?.part}"
        onclick={() => onselect(n.id)}
        onkeydown={(ev) => ev.key === "Enter" && onselect(n.id)}
      >
        <rect x={n.x} y={n.y} width={n.w} height={n.h} rx="4" />
        <text class="ref" x={n.x + 10} y={n.y + 22}>{n.id}</text>
        <text class="part" x={n.x + 10} y={n.y + 41}>{info?.part}</text>
      </g>
    {/each}
  </svg>
</div>

<style>
  .blocks {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
  .toolbar {
    display: flex;
    gap: 16px;
    padding: 8px 14px;
    font-size: 12.5px;
    color: var(--bone);
  }
  .failed {
    color: var(--error-text);
  }
  svg {
    flex: 1;
    min-height: 0;
    width: 100%;
  }
  .board rect {
    fill: none;
    stroke: var(--line);
    stroke-width: 1.5;
    stroke-dasharray: 6 4;
  }
  .board text {
    fill: var(--muted);
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .node rect {
    fill: var(--surface);
    stroke: var(--bone);
    stroke-width: 1.5;
  }
  .node.mcu rect {
    stroke-width: 2.5;
  }
  .node.selected rect {
    fill: var(--blue);
    stroke: var(--cream);
  }
  .node {
    cursor: pointer;
  }
  .ref {
    fill: var(--cream);
    font-family: var(--font-mono);
    font-size: 14px;
    font-weight: 600;
  }
  .part {
    fill: var(--bone);
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .edge {
    cursor: pointer;
  }
  .hitline {
    fill: none;
    stroke: transparent;
    stroke-width: 12;
  }
  .line {
    fill: none;
    stroke: var(--bone);
    stroke-width: 1.5;
  }
  .edge.bus .line {
    stroke: var(--cream);
    stroke-width: 3;
  }
  .edge.cross .line {
    stroke-dasharray: 8 4;
  }
  .edge.rail .line {
    stroke: var(--pin-idle);
  }
  .edge.selected .line {
    stroke: var(--cream);
    stroke-width: 4;
  }
  .label-bg {
    fill: var(--bg);
  }
  .label {
    fill: var(--cream);
    font-family: var(--font-mono);
    font-size: 11px;
  }
</style>
