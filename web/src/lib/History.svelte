<script lang="ts">
  // The last applied edits, and undo of the most recent one through the normal review (M4 §h).
  export type Entry = { time: string | null; kind: string | null; summary: string[]; proposal_id: string | null; can_undo: boolean };

  type Props = { entries: Entry[]; onundo: () => void; busy?: boolean };
  let { entries, onundo, busy = false }: Props = $props();

  function when(time: string | null): string {
    if (!time) return "";
    const date = new Date(time);
    return Number.isNaN(date.getTime()) ? time : date.toLocaleString();
  }
</script>

<section class="history" aria-label="History">
  <div class="head">
    <h3>Recent changes</h3>
    <button disabled={busy || !entries[0]?.can_undo} onclick={onundo}>Undo last…</button>
  </div>
  {#if !entries.length}
    <p class="empty">Nothing applied yet.</p>
  {:else}
    <ol>
      {#each entries as entry, i (entry.proposal_id ?? i)}
        <li>
          <div class="summary mono">{entry.summary.join(", ") || "(no changes)"}</div>
          <div class="meta">
            {when(entry.time)} · {entry.kind}{entry.can_undo ? "" : " · can't undo (recorded before undo support)"}
          </div>
        </li>
      {/each}
    </ol>
  {/if}
  <p class="note">Undo proposes the inverse change for review; it never reverts files directly.</p>
</section>

<style>
  .history {
    padding: 14px 16px;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  h3 {
    font-size: 12px;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin: 0;
  }
  button {
    border: 1px solid var(--line);
    background: var(--surface-2);
    border-radius: var(--radius);
    padding: 4px 10px;
    cursor: pointer;
  }
  button:disabled {
    color: var(--muted);
    cursor: not-allowed;
  }
  ol {
    list-style: none;
    padding: 0;
    margin: 10px 0;
  }
  li {
    padding: 6px 0;
    border-bottom: 1px solid var(--line);
  }
  .summary {
    color: var(--cream);
    font-size: 12px;
  }
  .meta,
  .note,
  .empty {
    color: var(--muted);
    font-size: 12px;
  }
</style>
