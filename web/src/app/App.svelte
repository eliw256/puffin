<script lang="ts">
  // The Puffin UI (M4). Every action goes through the API (PuffinService): nothing here edits
  // files or decides validity; the UI shows proposals and asks before applying them.
  import { onMount } from "svelte";
  import { api, ApiError, hasToken, listen, type ChangeEvent } from "../lib/api";
  import type { BlockEdge, Blocks } from "../lib/BlockDiagram.svelte";
  import Diagnostics, { type Target } from "../lib/Diagnostics.svelte";
  import DitherCanvas from "../lib/DitherCanvas.svelte";
  import { FAMILY_LABEL, FAMILY_ORDER, familySwatch, STATUS_GLYPH, STATUS_LABEL } from "../lib/families";
  import History, { type Entry } from "../lib/History.svelte";
  import ImportReview, { type ImportProposal } from "../lib/ImportReview.svelte";
  import PinDetail from "../lib/PinDetail.svelte";
  import PinMap, { type Drag } from "../lib/PinMap.svelte";
  import ReviewPanel, { type Choice } from "../lib/ReviewPanel.svelte";
  import type { DocSection, DropStatus, Issue, LegalPins, PinInfo, Proposal, SymbolView } from "../lib/types";

  type ProjectView = {
    project: string;
    boards: Record<string, { kicad_schematic?: string; firmware?: { mcu: string } }>;
    components: Record<string, { part: string; board: string; variant?: string }>;
    nets: Record<string, string[]>;
  };
  type Fingerprint = { project: string; schematics: Record<string, string | null> };
  type Review = {
    title: string;
    note?: string;
    propose: (writeback: boolean) => Promise<Proposal>;
    proposal: Proposal | null;
    withWriteback: Proposal | null;
    fingerprint: Fingerprint | null;
    verifying: boolean;
    stale: boolean;
    applying: boolean;
    error: string;
    attempt: number;
  };

  let project = $state<ProjectView | null>(null);
  let board = $state("");
  let component = $state("");
  let symbol = $state<SymbolView | null>(null);
  let pinByNumber = $state<Record<string, string>>({});
  let pins = $state<Record<string, PinInfo>>({});
  let hasDatasheet = $state(false);
  let part = $state("");
  let selected = $state<string | null>(null);
  let doc = $state<DocSection | undefined>(undefined);
  let legal = $state<LegalPins | null>(null);
  let drag = $state<Drag | null>(null);
  let issues = $state<Issue[]>([]);
  let targets = $state<Target[]>([]);
  let errors = $state(0);
  let warnings = $state(0);
  let pending = $state(0);
  let history = $state<Entry[]>([]);
  let blocks = $state<Blocks | null>(null);
  // Loaded on first use: ELK is large, and the pin map should not wait for it.
  let BlockDiagram = $state<typeof import("../lib/BlockDiagram.svelte").default | null>(null);
  let view = $state<"pins" | "blocks">("pins");
  let side = $state<"pin" | "issues" | "history">("pin");
  let highlight = $state<string[]>([]);
  let selectedEdge = $state<string | null>(null);
  let review = $state<Review | null>(null);
  let importReview: { proposal: ImportProposal | null; applying: boolean; stale: boolean; error: string; base: string } | null = $state(null);
  let toast: { kind: "refused" | "info" | "error"; text: string } | null = $state(null);
  let live = $state(false);
  let busy = $state(false);
  let fatal = $state("");

  const drift = $derived(issues.filter((i) => ["schematic-drift", "graph-drift", "schematic-unsynced"].includes(i.code)));
  const draggable = $derived(Object.values(pins).filter((p) => p.assignments.length).map((p) => p.pin));
  const statusMap = $derived.by((): Record<string, DropStatus> => {
    const map: Record<string, DropStatus> = {};
    for (const c of legal?.candidates ?? []) map[c.pin] = c.status;
    return map;
  });
  const statuses = $derived(drag && legal && legal.component === component && drag.source === legal.current_pin ? statusMap : null);
  const components = $derived(project ? Object.keys(project.components).filter((r) => project!.components[r].board === board) : []);

  function message(err: unknown): string {
    return err instanceof ApiError ? err.issues.map((i) => i.message + (i.hint ? ` (fix: ${i.hint})` : "")).join("; ") : String(err);
  }

  async function loadProject() {
    project = await api.get<ProjectView>("/api/project");
    if (!board || !(board in project.boards)) board = Object.keys(project.boards)[0] ?? "";
    if (!component || !(component in project.components)) {
      component = project.boards[board]?.firmware?.mcu ?? Object.keys(project.components)[0] ?? "";
    }
  }

  async function loadComponent() {
    if (!component) return;
    const [sym, pinData] = await Promise.all([
      api.get<{ symbol: SymbolView; pin_by_number: Record<string, string>; part: string }>(`/api/symbol?component=${encodeURIComponent(component)}`),
      api.get<{ pins: PinInfo[]; part: string }>(`/api/pins?component=${encodeURIComponent(component)}`),
    ]);
    symbol = sym.symbol;
    pinByNumber = sym.pin_by_number;
    pins = Object.fromEntries(pinData.pins.map((p) => [p.pin, p]));
    if (part !== pinData.part) {
      part = pinData.part;
      hasDatasheet = (await api.get<{ has_datasheet: boolean }>(`/api/part?part=${encodeURIComponent(part)}`)).has_datasheet;
    }
    if (selected && !pins[selected]) selected = null;
  }

  async function loadDiagnostics() {
    const [d, w, h] = await Promise.all([
      api.get<{ issues: Issue[]; targets: Target[]; errors: number; warnings: number }>("/api/diagnostics"),
      api.get<{ pending: number }>("/api/writeback"),
      api.get<{ entries: Entry[] }>("/api/history?limit=10"),
    ]);
    issues = d.issues;
    targets = d.targets;
    errors = d.errors;
    warnings = d.warnings;
    pending = w.pending;
    history = h.entries;
  }

  async function loadBlocks() {
    const [data, module] = await Promise.all([
      api.get<Blocks>("/api/blocks"),
      BlockDiagram ? null : import("../lib/BlockDiagram.svelte"),
    ]);
    if (module) BlockDiagram = module.default;
    blocks = data;
  }

  async function refresh() {
    try {
      await loadProject();
      await Promise.all([loadComponent(), loadDiagnostics(), view === "blocks" ? loadBlocks() : null]);
      fatal = "";
    } catch (err) {
      fatal = message(err);
    }
  }

  // ---- selection, docs and legal pins ----

  async function select(pin: string) {
    selected = pin;
    side = "pin";
    const info = pins[pin];
    doc = undefined;
    const assignment = info?.assignments[0];
    legal = null;
    if (assignment) {
      const params = new URLSearchParams({ component, peripheral: assignment.peripheral, signal: assignment.signal });
      api.get<LegalPins>(`/api/legal?${params}`).then((l) => (selected === pin ? (legal = l) : null)).catch(() => null);
    }
    if (hasDatasheet && info?.datasheet_name) {
      try {
        const params = new URLSearchParams({ q: `${info.datasheet_name} ${info.pin}`, part, limit: "1" });
        const found = await api.get<{ results: { section_id: string }[] }>(`/api/docs/search?${params}`);
        if (found.results[0] && selected === pin) {
          doc = await api.get<DocSection>(`/api/docs/section?id=${encodeURIComponent(found.results[0].section_id)}`);
        }
      } catch {
        doc = undefined;
      }
    }
  }

  async function choose(next: string) {
    component = next;
    selected = null;
    highlight = [];
    await loadComponent();
  }

  async function goTo(target: Target) {
    if (target.kind === "pin" && target.component) {
      if (target.component !== component) {
        board = project?.components[target.component]?.board ?? board;
        await choose(target.component);
      }
      view = "pins";
      if (target.pin) await select(target.pin);
    } else if (target.kind === "component" && target.component) {
      board = project?.components[target.component]?.board ?? board;
      await choose(target.component);
      view = "pins";
    } else if (target.kind === "net" && target.net && project) {
      const endpoint = project.nets[target.net]?.find((e) => e.startsWith(`${component}.`)) ?? project.nets[target.net]?.[0];
      if (endpoint) {
        const [ref, pin] = endpoint.split(".");
        await goTo({ kind: "pin", component: ref, pin });
      }
    } else if (target.kind === "board" && target.board) {
      board = target.board;
      await choose(project?.boards[board]?.firmware?.mcu ?? component);
    }
  }

  // ---- proposals ----

  async function openReview(title: string, propose: (writeback: boolean) => Promise<Proposal>, note = "") {
    review = { title, note, propose, proposal: null, withWriteback: null, fingerprint: null, verifying: false, stale: false, applying: false, error: "", attempt: (review?.attempt ?? 0) + 1 };
    await runProposal();
  }

  async function runProposal() {
    if (!review) return;
    const r = review;
    r.proposal = null;
    r.withWriteback = null;
    r.stale = false;
    r.error = "";
    try {
      const [proposal, fingerprint] = await Promise.all([r.propose(false), api.get<Fingerprint>("/api/fingerprint")]);
      if (review === r) Object.assign(r, { proposal, fingerprint });
    } catch (err) {
      if (review === r) r.error = message(err);
    }
  }

  async function chooseScope(choice: Choice) {
    const r = review;
    if (!r || choice !== "writeback" || r.withWriteback || r.verifying) return;
    r.verifying = true;
    try {
      r.withWriteback = await r.propose(true);
    } catch (err) {
      r.error = message(err);
    } finally {
      r.verifying = false;
    }
  }

  async function apply(choice: Choice | null) {
    const r = review;
    if (!r?.proposal) return;
    const id = choice === "writeback" ? r.withWriteback?.proposal_id : r.proposal.proposal_id;
    if (!id) return;
    r.applying = true;
    try {
      await api.post("/api/apply", { proposal_id: id });
      const generated = await api.post<{ ok: boolean; files: { path: string; action: string }[] }>("/api/generate", {});
      const header = generated.files.filter((f) => f.action !== "unchanged").map((f) => f.path).join(", ");
      review = null;
      toast = {
        kind: "info",
        text: `Applied${choice === "writeback" ? " to puffin.yaml and the schematic" : ""}. ${header ? `Regenerated ${header}.` : "Firmware headers unchanged."}${choice === "writeback" ? " Review the schematic in KiCad, then confirm in Issues." : ""}`,
      };
      await refresh();
    } catch (err) {
      if (err instanceof ApiError && err.issues.some((i) => i.code === "stale-proposal")) r.stale = true;
      else r.error = message(err);
    } finally {
      r.applying = false;
    }
  }

  function dropped(source: string, target: string | null) {
    if (!target) return;
    const status = statusMap[target] ?? "illegal";
    const assignment = pins[source]?.assignments[0];
    if (!assignment) return;
    if (status === "illegal") {
      const legalPins = legal?.candidates.filter((c) => c.status === "free").map((c) => c.pin).slice(0, 6).join(", ");
      toast = { kind: "refused", text: `Refused: ${target} can't carry ${assignment.peripheral}.${assignment.signal.split(".")[0]}. Free legal pins include ${legalPins || "none"}.` };
      return;
    }
    if (status === "current") return;
    const net = pins[source]?.net;
    const ops = [{ op: "move_pin", component, from_pin: source, to_pin: target }];
    void openReview(
      `Move ${component}.${source} → ${target} (${net ? `net ${net}; ` : ""}${assignment.peripheral}.${assignment.signal})`,
      (writeback) => api.post<Proposal>("/api/propose", { ops, writeback }),
    );
  }

  async function undo() {
    const last = history[0];
    await openReview(
      `Undo: ${last?.summary.join(", ") ?? "last change"}`,
      (writeback) => api.post<Proposal>("/api/propose-undo", { writeback }),
      "Undo is a normal proposal of the inverse change, with the same checks.",
    );
  }

  // ---- KiCad import and write-back confirmation ----

  async function openImport() {
    importReview = { proposal: null, applying: false, stale: false, error: "", base: "" };
    try {
      const proposal = await api.post<ImportProposal & { base_sha256: string }>("/api/propose-import", {});
      if (importReview) Object.assign(importReview, { proposal, base: proposal.base_sha256 });
    } catch (err) {
      if (importReview) importReview.error = message(err);
    }
  }

  async function applyImport() {
    const r = importReview;
    if (!r?.proposal?.proposal_id) return;
    r.applying = true;
    try {
      await api.post("/api/apply", { proposal_id: r.proposal.proposal_id });
      await api.post("/api/generate", {});
      importReview = null;
      toast = { kind: "info", text: "Imported from KiCad; firmware headers regenerated." };
      await refresh();
    } catch (err) {
      if (err instanceof ApiError && err.issues.some((i) => i.code === "stale-proposal")) r.stale = true;
      else r.error = message(err);
    } finally {
      r.applying = false;
    }
  }

  async function confirmWriteback() {
    busy = true;
    try {
      const result = await api.post<{ confirmed: number }>("/api/writeback/confirm", {});
      toast = { kind: "info", text: `Confirmed ${result.confirmed} write-back(s).` };
      await refresh();
    } finally {
      busy = false;
    }
  }

  async function restoreWriteback() {
    busy = true;
    try {
      const result = await api.post<{ restored: number }>("/api/writeback/restore", {});
      await api.post("/api/generate", {});
      toast = { kind: "info", text: `Restored the checkpoint, undoing ${result.restored} write-back(s).` };
      await refresh();
    } finally {
      busy = false;
    }
  }

  // ---- live updates ----

  async function changed(event: ChangeEvent) {
    await refresh();
    if (event.type === "project" || event.type === "schematic") {
      const fingerprint = await api.get<Fingerprint>("/api/fingerprint").catch(() => null);
      if (fingerprint && review?.proposal && !review.applying) {
        const schematicMoved = review.fingerprint && JSON.stringify(fingerprint.schematics) !== JSON.stringify(review.fingerprint.schematics);
        if (fingerprint.project !== review.proposal.base_sha256 || (review.withWriteback && schematicMoved)) review.stale = true;
      }
      if (fingerprint && importReview?.proposal && !importReview.applying && fingerprint.project !== importReview.base) importReview.stale = true;
    }
  }

  async function selectEdge(edge: BlockEdge) {
    selectedEdge = edge.id;
    const mcu = blocks?.nodes.find((n) => n.mcu && (n.id === edge.source || n.id === edge.target))?.id ?? edge.source;
    if (mcu !== component) {
      board = project?.components[mcu]?.board ?? board;
      await choose(mcu);
    }
    highlight = Object.values(pins).filter((p) => p.net && edge.nets.includes(p.net)).map((p) => p.pin);
  }

  onMount(() => {
    if (!hasToken) {
      fatal = "No access token. Open the URL that `puffin ui` printed (it ends in #token=...).";
      return;
    }
    void refresh();
    return listen((event) => void changed(event), (state) => (live = state));
  });

  $effect(() => {
    if (view === "blocks") void loadBlocks();
  });
</script>

<div class="app">
  <header class="top">
    <span class="wordmark">puffin</span>
    <span class="project mono">{project?.project ?? "…"}</span>
    <nav class="boards" aria-label="Boards">
      {#each Object.keys(project?.boards ?? {}) as b (b)}
        <button class:active={b === board} onclick={() => ((board = b), choose(project?.boards[b]?.firmware?.mcu ?? Object.keys(project!.components).find((r) => project!.components[r].board === b) ?? component))}>{b}</button>
      {/each}
    </nav>
    <select class="mono" aria-label="Component" value={component} onchange={(e) => choose((e.target as HTMLSelectElement).value)}>
      {#each components as ref (ref)}<option value={ref}>{ref} · {project?.components[ref].part}</option>{/each}
    </select>
    <nav class="views" aria-label="View">
      <button class:active={view === "pins"} onclick={() => (view = "pins")}>Pin map</button>
      <button class:active={view === "blocks"} onclick={() => (view = "blocks")}>Block diagram</button>
    </nav>
    <button class="checks" class:bad={errors > 0} onclick={() => (side = "issues")}>
      {errors} errors · {warnings} warnings{pending ? ` · ${pending} write-back${pending === 1 ? "" : "s"} to confirm` : ""}
    </button>
    <span class="live" class:on={live} title={live ? "live: external edits appear automatically" : "reconnecting"}>{live ? "● live" : "○ offline"}</span>
  </header>

  {#if fatal}<p class="fatal" role="alert">{fatal}</p>{/if}

  <main>
    <section class="map" aria-label={view === "pins" ? "Pin map" : "Block diagram"}>
      {#if drift.length}
        <div class="drift" role="alert">
          <span class="glyph" aria-hidden="true">!</span>
          <span>{drift.map((d) => d.message).join(" · ")}</span>
          <button onclick={openImport}>Import from KiCad…</button>
        </div>
      {/if}
      {#if toast}
        <p class="toast {toast.kind}" role="alert">
          {#if toast.kind === "refused"}<span aria-hidden="true">⊘ </span>{/if}{toast.text}
          <button aria-label="Dismiss" onclick={() => (toast = null)}>×</button>
        </p>
      {/if}
      <div class="canvas">
        {#if view === "blocks"}
          {#if blocks && BlockDiagram}
            <BlockDiagram {blocks} selected={component} {selectedEdge} onselect={(ref) => ((board = project?.components[ref]?.board ?? board), choose(ref), (view = "pins"))} onselectedge={selectEdge} />
          {/if}
        {:else if symbol}
          {#key component}
            <PinMap {symbol} {pins} {pinByNumber} {selected} {draggable} {statuses} {highlight} bind:drag onselect={select} ondrop={dropped} />
          {/key}
        {/if}
      </div>
      <footer class="legend" aria-label="Legend">
        {#if statuses && legal}
          <div class="legend-row">
            <span class="legend-title">Dropping {legal.peripheral}.{legal.signal}</span>
            <span class="target-caption mono" class:empty={!drag?.target}>
              {#if drag?.target}on {drag.target}: {STATUS_GLYPH[statusMap[drag.target] ?? "illegal"]} {STATUS_LABEL[statusMap[drag.target] ?? "illegal"]}{:else}over no pin{/if}
            </span>
            <span class="hint">Esc cancels</span>
          </div>
          <div class="legend-row">
            {#each ["current", "free", "connected", "in_use", "needs_override", "illegal"] as const as s (s)}
              <span class="status"><span class="glyph-s" aria-hidden="true">{STATUS_GLYPH[s]}</span>{STATUS_LABEL[s]}</span>
            {/each}
          </div>
        {:else}
          <div class="legend-row">
            {#each FAMILY_ORDER as family (family)}
              {@const swatch = familySwatch(family)}
              <span class="swatch">{#if swatch}<DitherCanvas {swatch} width={22} height={12} cell={2} />{:else}<span class="idle-chip"></span>{/if}{FAMILY_LABEL[family]}</span>
            {/each}
          </div>
          <div class="legend-row">
            <span class="swatch"><span class="flag-chip" aria-hidden="true"></span>flagged</span>
            <span class="hint">Click selects · drag an assigned pin's tag to move it · ⌘/Ctrl + scroll zooms · drag empty space to pan</span>
          </div>
        {/if}
      </footer>
    </section>

    <aside class="side">
      <nav class="tabs" aria-label="Side panel">
        <button class:active={side === "pin"} onclick={() => (side = "pin")}>Pin</button>
        <button class:active={side === "issues"} onclick={() => (side = "issues")}>Issues{errors + warnings ? ` (${errors + warnings})` : ""}</button>
        <button class:active={side === "history"} onclick={() => (side = "history")}>History</button>
      </nav>
      <div class="side-body">
        {#if side === "pin"}
          {#if selected && pins[selected]}
            <PinDetail pin={pins[selected]} {doc} {hasDatasheet} {part} />
          {:else}
            <p class="empty">Select a pin to see its net, assignment, flags and datasheet section.</p>
          {/if}
        {:else if side === "issues"}
          <Diagnostics {issues} {targets} {pending} {busy} onselect={goTo} onconfirm={confirmWriteback} onrestore={restoreWriteback} />
        {:else}
          <History entries={history} onundo={undo} busy={review !== null} />
        {/if}
      </div>
    </aside>
  </main>

  {#if review}
    {#key review.attempt}
      <ReviewPanel
        title={review.title}
        note={review.note}
        proposal={review.proposal}
        withWriteback={review.withWriteback}
        verifying={review.verifying}
        stale={review.stale}
        applying={review.applying}
        error={review.error}
        onchoose={chooseScope}
        onapply={apply}
        onrepropose={() => ((review!.attempt += 1), runProposal())}
        onclose={() => (review = null)}
      />
    {/key}
  {/if}
  {#if importReview}
    <ImportReview
      proposal={importReview.proposal}
      applying={importReview.applying}
      stale={importReview.stale}
      error={importReview.error}
      onapply={applyImport}
      onrepropose={openImport}
      onclose={() => (importReview = null)}
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
    gap: 14px;
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
    color: var(--cream);
  }
  .project {
    color: var(--bone);
    font-size: 13px;
  }
  .boards,
  .views {
    display: flex;
    gap: 4px;
  }
  .boards button,
  .views button,
  .tabs button {
    background: none;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 3px 10px;
    color: var(--bone);
    cursor: pointer;
  }
  .boards button.active,
  .views button.active,
  .tabs button.active {
    background: var(--blue);
    color: var(--cream);
    border-color: var(--cream);
  }
  select {
    background: var(--surface-2);
    color: var(--cream);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 3px 6px;
    font-size: 12.5px;
  }
  .checks {
    margin-left: auto;
    font-size: 12.5px;
    color: var(--bone);
    background: none;
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 1px 10px;
    cursor: pointer;
  }
  .checks.bad {
    color: var(--error-text);
    border-color: var(--error);
  }
  .live {
    font-size: 12px;
    color: var(--muted);
  }
  .live.on {
    color: var(--bone);
  }
  .fatal {
    margin: 0;
    padding: 8px 16px;
    background: var(--bg);
    color: var(--error-text);
    border-bottom: 2px solid var(--error);
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
  .drift {
    position: absolute;
    z-index: 5;
    top: 10px;
    left: 12px;
    right: 150px;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    background: var(--surface);
    border: 2px solid var(--warning);
    border-radius: var(--radius);
    color: var(--bone);
    font-size: 13px;
  }
  .drift .glyph {
    color: var(--warning);
    font-weight: 600;
  }
  .drift span:nth-child(2) {
    flex: 1;
  }
  .drift button {
    background: var(--cream);
    color: var(--ink);
    border: none;
    border-radius: var(--radius);
    padding: 4px 12px;
    font-weight: 600;
    cursor: pointer;
  }
  .side {
    border-left: 1px solid var(--line);
    background: var(--surface);
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .tabs {
    display: flex;
    gap: 4px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--line);
  }
  .side-body {
    overflow-y: auto;
    flex: 1;
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
  .glyph-s {
    color: var(--cream);
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
    top: 60px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 6;
    max-width: 80%;
    margin: 0;
    padding: 8px 36px 8px 12px;
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--bone);
    border: 1px solid var(--line);
    border-left: 3px solid var(--bone);
  }
  .toast.refused,
  .toast.error {
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
