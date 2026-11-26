<script lang="ts">
  // Review a proposal before applying it. Nothing is applied without the Apply click.
  // The parent does the API calls: it proposes, re-proposes with write-back when the engineer
  // chooses it (onchoose), and applies (onapply). This component only shows and asks.
  import Diff from "./Diff.svelte";
  import type { Issue, Proposal } from "./types";

  export type Choice = "puffin" | "writeback";

  type Props = {
    title: string;
    proposal: Proposal | null; // null while proposing
    withWriteback: Proposal | null; // the same edit proposed with writeback: true
    verifying?: boolean; // kicad-cli verification in progress
    stale?: boolean; // puffin.yaml or the schematic changed since the proposal
    applying?: boolean;
    unavailable?: string; // prototype only: no recorded proposal for this target
    error?: string;
    note?: string;
    onchoose: (choice: Choice) => void;
    onapply: (choice: Choice | null) => void;
    onrepropose?: () => void;
    onclose: () => void;
  };
  let {
    title,
    proposal,
    withWriteback,
    verifying = false,
    stale = false,
    applying = false,
    unavailable = "",
    error = "",
    note = "",
    onchoose,
    onapply,
    onrepropose,
    onclose,
  }: Props = $props();

  let choice: Choice | null = $state(null);

  const overwrite = $derived(proposal?.warnings.find((w) => w.code === "kicad-overwrite"));
  const writebackOffered = $derived(overwrite?.hint.startsWith("write-back can apply") ?? false);
  const plan = $derived(withWriteback?.writeback?.[0]);
  const writebackRefused = $derived(
    withWriteback && !withWriteback.ok
      ? (withWriteback as Proposal & { errors?: Issue[] }).errors?.[0]?.message ??
          withWriteback.introduced_errors[0]?.message ??
          "write-back is not available"
      : "",
  );
  const refusal = $derived(
    writebackRefused || (overwrite && !writebackOffered ? overwrite.hint.replace(/^write-back can't apply this /, "") : ""),
  );
  const shown = $derived(choice === "writeback" && withWriteback?.ok ? withWriteback : proposal);
  const notices = $derived(
    (shown?.warnings ?? []).filter((w) => choice !== null || w.code !== "kicad-overwrite"),
  );
  const canApply = $derived(
    !!proposal?.ok &&
      !stale &&
      !applying &&
      !unavailable &&
      (!overwrite || choice === "puffin" || (choice === "writeback" && !!withWriteback?.ok && !verifying)),
  );

  function choose(next: Choice) {
    choice = next;
    onchoose(next);
  }

  function issueLine(issue: Issue): string {
    return `${issue.severity}[${issue.code}] ${issue.message}`;
  }
</script>

<div class="scrim" role="presentation" onclick={onclose}></div>
<aside class="review" aria-label="Review proposal">
  <header>
    <p class="eyebrow">Review proposal · nothing is applied until you confirm</p>
    <h2 class="mono">{title}</h2>
    <button class="close" aria-label="Close" onclick={onclose}>×</button>
  </header>

  <div class="body">
    {#if stale}
      <div class="stale" role="alert">
        <strong>This proposal no longer applies.</strong> puffin.yaml or the schematic changed (in
        an editor, KiCad, or an AI session) since it was made.
        {#if onrepropose}<button onclick={onrepropose}>Propose again</button>{/if}
      </div>
    {/if}
    {#if error}<p class="error-box" role="alert">{error}</p>{/if}
    {#if note}<p class="note">{note}</p>{/if}

    {#if unavailable}
      <p class="unavailable" role="status">{unavailable}</p>
    {:else if !proposal}
      <p class="verifying" role="status"><span class="spinner" aria-hidden="true"></span> Proposing…</p>
    {:else}
      {#if overwrite && proposal.ok}
        <fieldset class="choice" disabled={stale}>
          <legend>This changes a net KiCad manages ({overwrite.message.match(/net[s]? ([^;]+)/)?.[1]}). Apply it to:</legend>
          <label class:selected={choice === "puffin"}>
            <input type="radio" name="scope" value="puffin" checked={choice === "puffin"} onchange={() => choose("puffin")} />
            <span>
              <strong>Puffin only</strong>
              <small>puffin.yaml and the firmware header. The next KiCad import reverts it unless you
                make the same change in KiCad.</small>
            </span>
          </label>
          <label class:selected={choice === "writeback"} class:disabled={!writebackOffered}>
            <input
              type="radio"
              name="scope"
              value="writeback"
              disabled={!writebackOffered}
              checked={choice === "writeback"}
              onchange={() => choose("writeback")}
            />
            <span>
              <strong>Puffin + schematic</strong>
              {#if refusal}
                <small class="refused">Write-back can't apply this: {refusal}</small>
              {:else}
                <small>Also moves the label in the schematic, verified with kicad-cli before anything
                  is written. You review it in KiCad, then confirm.</small>
              {/if}
            </span>
          </label>
        </fieldset>
      {/if}

      {#if choice === "writeback"}
        {#if verifying}
          <p class="verifying" role="status"><span class="spinner" aria-hidden="true"></span> Verifying the
            schematic edit with kicad-cli…</p>
        {:else if plan && withWriteback?.ok}
          <p class="verified" role="status">✓ Verified: kicad-cli sees exactly this change and nothing else.</p>
          <Diff title={plan.schematic} text={plan.diff} />
        {/if}
      {/if}

      {#if shown}
        <Diff title="puffin.yaml" text={shown.diff} />
        {#each shown.generated as file (file.path)}
          <Diff title="{file.path} (generated)" text={file.diff} />
        {/each}
        {#if shown.introduced_errors.length}
          <ul class="issues errors">
            {#each shown.introduced_errors as issue (issue.code + issue.where)}<li>
                <span class="glyph" aria-hidden="true">✕</span>{issueLine(issue)}</li>{/each}
          </ul>
        {/if}
        {#if notices.length}
          <ul class="issues">
            {#each notices as issue (issue.code + issue.where)}
              <li><span class="glyph" aria-hidden="true">!</span>{issueLine(issue)}</li>
            {/each}
          </ul>
        {/if}
      {/if}
    {/if}
  </div>

  <footer>
    <span class="hint">
      {#if unavailable}Prototype: nothing to apply for this target.
      {:else if proposal && !proposal.ok}This edit would introduce errors; it can't be applied.
      {:else if overwrite && choice === null}Choose where to apply it first.
      {:else if proposal?.ok}Applying also regenerates the firmware headers.{/if}
    </span>
    <button class="secondary" onclick={onclose}>Cancel</button>
    <button class="primary" disabled={!canApply} onclick={() => onapply(choice)}>
      {applying ? "Applying…" : "Apply"}{!applying && choice === "writeback"
        ? " to puffin.yaml + schematic"
        : !applying && choice === "puffin"
          ? " to puffin.yaml"
          : ""}
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
    font-weight: 600;
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
  .stale,
  .error-box {
    margin: 0 0 14px;
    padding: 10px 12px;
    border: 2px solid var(--warning);
    border-radius: var(--radius);
    color: var(--cream);
    background: var(--bg);
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
    cursor: pointer;
  }
  .note {
    color: var(--bone);
    margin: 0 0 12px;
  }
  .choice {
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 10px 12px 12px;
    margin: 0 0 16px;
  }
  legend {
    padding: 0 4px;
    color: var(--cream);
    font-weight: 600;
  }
  label {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 10px;
    margin-top: 8px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    cursor: pointer;
  }
  label.selected {
    background: var(--blue);
    border-color: var(--cream);
  }
  label.disabled {
    cursor: not-allowed;
    opacity: 0.85;
  }
  label input {
    margin-top: 3px;
    accent-color: var(--cream);
  }
  label strong {
    display: block;
    color: var(--cream);
  }
  label small {
    display: block;
    color: var(--bone);
    font-size: 12.5px;
  }
  .refused {
    color: var(--error-text);
  }
  .unavailable {
    margin: 0 0 12px;
    padding: 10px 12px;
    border: 1px dashed var(--bone);
    border-radius: var(--radius);
    color: var(--cream);
  }
  .verifying,
  .verified {
    font-size: 13px;
    margin: 0 0 12px;
    color: var(--bone);
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
  .issues {
    list-style: none;
    padding: 0;
    margin: 0 0 12px;
    font-size: 13px;
    color: var(--bone);
  }
  .issues li {
    padding: 6px 10px;
    border-left: 3px solid var(--warning);
    background: var(--bg);
    margin-bottom: 6px;
  }
  .issues.errors li {
    border-left-color: var(--error);
    color: var(--error-text);
  }
  .glyph {
    color: var(--warning);
    font-weight: 600;
    margin-right: 6px;
  }
  .issues.errors .glyph {
    color: var(--error-text);
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
