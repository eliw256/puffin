<script lang="ts">
  // Review a KiCad import (propose_import_kicad) section by section; apply only on confirm.
  import Diff from "./Diff.svelte";
  import type { Issue } from "./types";

  export type ChangeSet = {
    schematic: string;
    components: { added: { ref: string; part: string; board: string }[]; removed: string[]; updated: { ref: string; field: string; old: string | null; new: string | null }[] };
    nets: { added: { name: string; endpoints: string[] }[]; removed: { name: string; endpoints: string[] }[]; renamed: { old: string; new: string }[] };
    endpoint_changes: { net: string; removed: string[]; added: string[] }[];
    assignment_fixes: { component: string; peripheral: string; signal: string; old_pin: string; new_pin: string; net: string }[];
    ambiguous: { component: string; peripheral: string; signal: string; pin: string; reason: string }[];
    warnings: Issue[];
    unmanaged: Record<string, number>;
  };
  export type ImportProposal = {
    ok: boolean;
    proposal_id: string | null;
    in_sync: boolean;
    schematics: ChangeSet[];
    diff: string;
    introduced_errors: Issue[];
    generated: { board: string; path: string; diff: string }[];
  };

  type Props = {
    proposal: ImportProposal | null;
    applying?: boolean;
    stale?: boolean;
    error?: string;
    onapply: () => void;
    onrepropose: () => void;
    onclose: () => void;
  };
  let { proposal, applying = false, stale = false, error = "", onapply, onrepropose, onclose }: Props = $props();
</script>

<div class="scrim" role="presentation" onclick={onclose}></div>
<aside class="review" aria-label="Review KiCad import">
  <header>
    <p class="eyebrow">Import from KiCad · nothing is applied until you confirm</p>
    <h2>KiCad changes for puffin.yaml</h2>
    <button class="close" aria-label="Close" onclick={onclose}>×</button>
  </header>
  <div class="body">
    {#if stale}
      <div class="stale" role="alert">
        <strong>This import proposal no longer applies</strong>: a file changed since.
        <button onclick={onrepropose}>Propose again</button>
      </div>
    {/if}
    {#if error}<p class="error-box" role="alert">{error}</p>{/if}
    {#if !proposal}
      <p class="status" role="status"><span class="spinner" aria-hidden="true"></span> Exporting the netlist with kicad-cli…</p>
    {:else if proposal.in_sync}
      <p class="status" role="status">✓ puffin.yaml already matches the schematic. Applying records the sync, so drift warnings clear.</p>
    {:else}
      {#each proposal.schematics as set (set.schematic)}
        <h3 class="mono">{set.schematic}</h3>
        {#if set.components.added.length || set.components.removed.length || set.components.updated.length}
          <h4>Components</h4>
          <ul>
            {#each set.components.added as c (c.ref)}<li class="mono">+ {c.ref} {c.part} on {c.board}</li>{/each}
            {#each set.components.removed as ref (ref)}<li class="mono">− {ref}</li>{/each}
            {#each set.components.updated as c (c.ref + c.field)}<li class="mono">~ {c.ref} {c.field}: {c.old} → {c.new}</li>{/each}
          </ul>
        {/if}
        {#if set.nets.added.length || set.nets.removed.length || set.nets.renamed.length}
          <h4>Nets</h4>
          <ul>
            {#each set.nets.added as n (n.name)}<li class="mono">+ {n.name} [{n.endpoints.join(", ")}]</li>{/each}
            {#each set.nets.removed as n (n.name)}<li class="mono">− {n.name} [{n.endpoints.join(", ")}]</li>{/each}
            {#each set.nets.renamed as n (n.old)}<li class="mono">~ {n.old} → {n.new}</li>{/each}
          </ul>
        {/if}
        {#if set.endpoint_changes.length}
          <h4>Endpoint changes</h4>
          <ul>
            {#each set.endpoint_changes as e (e.net)}
              <li class="mono">{e.net}: {[...e.removed.map((x) => `−${x}`), ...e.added.map((x) => `+${x}`)].join(" ")}</li>
            {/each}
          </ul>
        {/if}
        {#if set.assignment_fixes.length}
          <h4>Assignment fixes (applied with the import)</h4>
          <ul>
            {#each set.assignment_fixes as f (f.component + f.peripheral + f.signal)}
              <li class="mono">{f.component} {f.peripheral}.{f.signal}: {f.old_pin} → {f.new_pin} (net {f.net})</li>
            {/each}
          </ul>
        {/if}
        {#if set.ambiguous.length}
          <h4>Ambiguous, left for you</h4>
          <ul class="warn">
            {#each set.ambiguous as a (a.component + a.peripheral + a.signal)}
              <li><span class="mono">{a.component} {a.peripheral}.{a.signal} on {a.pin}</span>: {a.reason}</li>
            {/each}
          </ul>
        {/if}
        {#if set.warnings.length}
          <h4>Warnings</h4>
          <ul class="warn">{#each set.warnings as w (w.code + w.where)}<li><span class="glyph">!</span>{w.message}</li>{/each}</ul>
        {/if}
      {/each}
      <Diff title="puffin.yaml" text={proposal.diff} />
      {#each proposal.generated as file (file.path)}<Diff title="{file.path} (generated)" text={file.diff} />{/each}
    {/if}
    {#if proposal?.introduced_errors.length}
      <h4>Errors the import would introduce</h4>
      <ul class="errors">
        {#each proposal.introduced_errors as e (e.code + e.where)}<li><span class="glyph">✕</span>{e.message}{e.hint ? ` (fix: ${e.hint})` : ""}</li>{/each}
      </ul>
    {/if}
  </div>
  <footer>
    <span class="hint">{proposal && !proposal.ok ? "Fix the errors first; nothing can be applied." : "Applying also regenerates the firmware headers."}</span>
    <button onclick={onclose}>Cancel</button>
    <button class="primary" disabled={!proposal?.ok || applying || stale} onclick={onapply}>
      {applying ? "Applying…" : proposal?.in_sync ? "Record sync" : "Apply import"}
    </button>
  </footer>
</aside>

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: rgb(20 17 19 / 0.55);
    z-index: 10;
  }
  .review {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(760px, 62vw);
    background: var(--surface);
    border-left: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    z-index: 11;
  }
  header {
    position: relative;
    padding: 16px 52px 12px 20px;
    border-bottom: 1px solid var(--line);
  }
  .eyebrow {
    margin: 0 0 4px;
    font-size: 12px;
    color: var(--muted);
  }
  h2 {
    margin: 0;
    font-size: 16px;
  }
  .close {
    position: absolute;
    top: 12px;
    right: 14px;
    background: none;
    border: none;
    font-size: 22px;
    color: var(--bone);
    cursor: pointer;
  }
  .body {
    padding: 16px 20px;
    overflow-y: auto;
    flex: 1;
  }
  h3 {
    font-size: 13px;
    color: var(--cream);
    margin: 0 0 8px;
  }
  h4 {
    font-size: 12px;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin: 14px 0 6px;
  }
  ul {
    list-style: none;
    padding: 0;
    margin: 0 0 8px;
    font-size: 12.5px;
    color: var(--bone);
  }
  li {
    padding: 2px 0;
  }
  .warn li,
  .errors li {
    padding: 6px 10px;
    border-left: 3px solid var(--warning);
    background: var(--bg);
    margin-bottom: 4px;
  }
  .errors li {
    border-left-color: var(--error);
    color: var(--error-text);
  }
  .glyph {
    font-weight: 600;
    margin-right: 6px;
    color: var(--warning);
  }
  .errors .glyph {
    color: var(--error-text);
  }
  .status {
    color: var(--bone);
  }
  .stale,
  .error-box {
    margin: 0 0 14px;
    padding: 10px 12px;
    border: 2px solid var(--warning);
    border-radius: var(--radius);
    background: var(--bg);
    color: var(--cream);
  }
  .error-box {
    border-color: var(--error);
    color: var(--error-text);
  }
  .stale button {
    margin-left: 8px;
    background: var(--cream);
    color: var(--ink);
    border: none;
    border-radius: var(--radius);
    padding: 3px 10px;
    font-weight: 600;
  }
  .spinner {
    display: inline-block;
    width: 11px;
    height: 11px;
    border: 2px solid var(--line);
    border-top-color: var(--cream);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    vertical-align: -1px;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  footer {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 20px;
    border-top: 1px solid var(--line);
  }
  .hint {
    flex: 1;
    font-size: 12.5px;
    color: var(--muted);
  }
  footer button {
    border-radius: var(--radius);
    padding: 7px 14px;
    cursor: pointer;
    border: 1px solid var(--line);
    background: var(--surface-2);
  }
  .primary {
    background: var(--cream);
    color: var(--ink);
    border-color: var(--cream);
    font-weight: 600;
  }
  .primary:disabled {
    background: var(--surface-2);
    color: var(--muted);
    border-color: var(--line);
    cursor: not-allowed;
  }
</style>
