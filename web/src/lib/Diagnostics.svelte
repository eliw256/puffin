<script lang="ts">
  // Live check results, errors first; each issue selects what it is about (M4 §g).
  import type { Issue } from "./types";

  export type Target = { kind: string; component?: string; pin?: string; net?: string; board?: string };

  type Props = {
    issues: Issue[];
    targets: Target[];
    pending: number; // unconfirmed write-backs
    busy?: boolean;
    onselect: (target: Target) => void;
    onconfirm: () => void;
    onrestore: () => void;
  };
  let { issues, targets, pending, busy = false, onselect, onconfirm, onrestore }: Props = $props();

  let restoreArmed = $state(false);

  const rows = $derived(issues.map((issue, i) => ({ issue, target: targets[i] ?? { kind: "none" } })));
  const errors = $derived(rows.filter((r) => r.issue.severity === "error"));
  const warnings = $derived(rows.filter((r) => r.issue.severity === "warning"));
</script>

<section class="diagnostics" aria-label="Diagnostics">
  {#if pending > 0}
    <div class="writeback" role="region" aria-label="Unconfirmed write-backs">
      <p>
        <strong>{pending} unconfirmed write-back{pending === 1 ? "" : "s"}</strong> to the schematic.
        Open it in KiCad and look at the moved labels first.
      </p>
      <div class="actions">
        <button class="primary" disabled={busy} onclick={onconfirm}>Confirm</button>
        {#if restoreArmed}
          <button class="danger" disabled={busy} onclick={() => ((restoreArmed = false), onrestore())}
            >Really restore all {pending}?</button
          >
          <button disabled={busy} onclick={() => (restoreArmed = false)}>Keep</button>
        {:else}
          <button disabled={busy} onclick={() => (restoreArmed = true)}>Restore all {pending}</button>
        {/if}
      </div>
    </div>
  {/if}

  {#if !issues.length}
    <p class="clean">✓ No errors or warnings.</p>
  {/if}
  {#each [{ title: "Errors", list: errors, kind: "error" }, { title: "Warnings", list: warnings, kind: "warning" }] as group (group.kind)}
    {#if group.list.length}
      <h3>{group.title} · {group.list.length}</h3>
      <ul>
        {#each group.list as row (row.issue.code + row.issue.where + row.issue.message)}
          <li class={group.kind}>
            <button
              class="issue"
              disabled={row.target.kind === "none"}
              onclick={() => onselect(row.target)}
              aria-label="{group.kind}: {row.issue.message}"
            >
              <span class="glyph" aria-hidden="true">{group.kind === "error" ? "✕" : "!"}</span>
              <span class="text">
                <span class="code mono">{row.issue.code}</span>
                <span class="where mono">{row.issue.where}</span>
                <span class="message">{row.issue.message}</span>
                {#if row.issue.hint}<span class="hint">fix: {row.issue.hint}</span>{/if}
              </span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  {/each}
</section>

<style>
  .diagnostics {
    padding: 14px 16px;
  }
  .writeback {
    border: 2px solid var(--warning);
    border-radius: var(--radius);
    padding: 10px 12px;
    margin-bottom: 14px;
    background: var(--bg);
  }
  .writeback p {
    margin: 0 0 8px;
    color: var(--bone);
  }
  .writeback strong {
    color: var(--cream);
  }
  .actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .actions button {
    border: 1px solid var(--line);
    background: var(--surface-2);
    border-radius: var(--radius);
    padding: 4px 10px;
    cursor: pointer;
  }
  .actions .primary {
    background: var(--cream);
    color: var(--ink);
    border-color: var(--cream);
    font-weight: 600;
  }
  .actions .danger {
    border: 2px solid var(--error);
    color: var(--error-text);
  }
  .clean {
    color: var(--bone);
  }
  h3 {
    font-size: 12px;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin: 12px 0 6px;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  li {
    margin-bottom: 6px;
    border-left: 3px solid var(--warning);
    background: var(--bg);
  }
  li.error {
    border-left-color: var(--error);
  }
  .issue {
    display: flex;
    gap: 8px;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    padding: 6px 8px;
    cursor: pointer;
  }
  .issue:disabled {
    cursor: default;
  }
  .glyph {
    color: var(--warning);
    font-weight: 600;
  }
  .error .glyph,
  .error .message {
    color: var(--error-text);
  }
  .text {
    display: flex;
    flex-direction: column;
    gap: 1px;
    font-size: 12.5px;
  }
  .code {
    color: var(--cream);
    font-size: 11.5px;
  }
  .where {
    color: var(--muted);
    font-size: 11px;
  }
  .message {
    color: var(--bone);
  }
  .hint {
    color: var(--muted);
    font-size: 12px;
  }
</style>
