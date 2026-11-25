<script lang="ts">
  import Diff from "./Diff.svelte";
  import type { Issue, Proposal } from "./types";

  type Props = {
    title: string;
    proposal: Proposal; // Puffin only
    withWriteback: Proposal; // the same edit, proposed with writeback: true
    unavailable?: string; // prototype: no recorded proposal for this target
    verifyDelayMs?: number;
    onapply: (choice: "puffin" | "writeback") => void;
    onclose: () => void;
  };
  let { title, proposal, withWriteback, unavailable = "", verifyDelayMs = 900, onapply, onclose }: Props = $props();

  type Choice = "puffin" | "writeback" | null;
  let choice: Choice = $state(null);
  let verifying = $state(false);

  const overwrite = $derived(proposal.warnings.find((w) => w.code === "kicad-overwrite"));
  const plan = $derived(withWriteback.writeback?.[0]);
  const writebackOk = $derived(withWriteback.ok && plan?.available === true);
  const refusal = $derived(
    plan?.reason ||
      withWriteback.introduced_errors[0]?.message ||
      (overwrite?.hint.startsWith("write-back can't") ? overwrite.hint : ""),
  );
  const shown = $derived(choice === "writeback" ? withWriteback : proposal);
  const notices = $derived(
    shown.warnings.filter((w) => choice !== null || w.code !== "kicad-overwrite"),
  );
  const canApply = $derived(proposal.ok && (!overwrite || (choice !== null && !verifying)));

  function choose(next: Choice) {
    choice = next;
    if (next === "writeback") {
      verifying = true;
      setTimeout(() => (verifying = false), verifyDelayMs); // stands in for the kicad-cli run
    }
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
    {#if unavailable}
      <p class="unavailable" role="status">{unavailable}</p>
    {:else}
    {#if overwrite}
      <fieldset class="choice">
        <legend>This changes a net KiCad manages ({overwrite.message.match(/net[s]? ([^;]+)/)?.[1]}). Apply it to:</legend>
        <label class:selected={choice === "puffin"}>
          <input type="radio" name="scope" value="puffin" checked={choice === "puffin"} onchange={() => choose("puffin")} />
          <span>
            <strong>Puffin only</strong>
            <small>puffin.yaml and the firmware header. The next KiCad import reverts it unless you
              make the same change in KiCad.</small>
          </span>
        </label>
        <label class:selected={choice === "writeback"} class:disabled={!writebackOk}>
          <input
            type="radio"
            name="scope"
            value="writeback"
            disabled={!writebackOk}
            checked={choice === "writeback"}
            onchange={() => choose("writeback")}
          />
          <span>
            <strong>Puffin + schematic</strong>
            {#if writebackOk}
              <small>Also moves the label in {plan?.schematic}, verified with kicad-cli before
                anything is written. You review it in KiCad, then confirm.</small>
            {:else}
              <small class="refused">Write-back can't apply this: {refusal}</small>
            {/if}
          </span>
        </label>
      </fieldset>
    {/if}

    {#if choice === "writeback"}
      {#if verifying}
        <p class="verifying" role="status"><span class="spinner" aria-hidden="true"></span> Verifying the
          schematic edit with kicad-cli…</p>
      {:else if plan}
        <p class="verified" role="status">✓ Verified: kicad-cli sees exactly this change and nothing else.</p>
        <Diff title={plan.schematic} text={plan.diff} />
      {/if}
    {/if}

    <Diff title="puffin.yaml" text={shown.diff} />
    {#each shown.generated as file (file.path)}
      <Diff title="{file.path} (generated)" text={file.diff} />
    {/each}

    {#if shown.introduced_errors.length}
      <ul class="issues errors">
        {#each shown.introduced_errors as issue (issue.code + issue.where)}<li>{issueLine(issue)}</li>{/each}
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
  </div>

  <footer>
    <span class="hint">
      {#if unavailable}Prototype: nothing to apply for this target.{:else if overwrite && choice === null}Choose where to apply it first.{/if}
    </span>
    <button class="secondary" onclick={onclose}>Cancel</button>
    <button class="primary" disabled={!canApply || !!unavailable} onclick={() => choice !== null && onapply(choice)}>
      Apply{choice === "writeback" ? " to puffin.yaml + schematic" : choice === "puffin" ? " to puffin.yaml" : ""}
    </button>
  </footer>
</aside>

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: rgb(20 17 19 / 0.55);
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
