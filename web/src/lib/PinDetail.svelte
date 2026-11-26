<script lang="ts">
  import DitherCanvas from "./DitherCanvas.svelte";
  import { FAMILY_LABEL, familySwatch } from "./families";
  import { blocks } from "./markdown";
  import type { DocSection, PinInfo } from "./types";

  type Props = { pin: PinInfo; doc: DocSection | undefined; hasDatasheet: boolean; part: string };
  let { pin, doc, hasDatasheet, part }: Props = $props();
</script>

<section class="detail" aria-label="Pin {pin.pin}">
  <header>
    <h2 class="mono">{pin.pin}</h2>
    <span class="sub mono">pin {pin.numbers.join(", ")} · {pin.datasheet_name ?? "—"} · {pin.type ?? "?"}</span>
    <span class="family">
      {#if familySwatch(pin.family)}<DitherCanvas swatch={familySwatch(pin.family)!} width={16} height={12} cell={2} />{:else}<span
          class="line-chip" class:connected={pin.family === "connected"}></span>{/if}
      {FAMILY_LABEL[pin.family]}
    </span>
  </header>

  <section class="desc" aria-label="Datasheet description">
    {#if pin.description}
      <p class="mono">{pin.description}</p>
      <p class="cite">Datasheet: {pin.description_source}</p>
    {:else}
      <p class="none">No datasheet description for this pin.</p>
    {/if}
  </section>

  <dl>
    <dt>Net</dt>
    <dd class="mono">{pin.net ?? "not connected"}</dd>

    <dt>Assignment</dt>
    <dd class="mono">
      {#each pin.assignments as a (a.peripheral + a.signal)}
        <div>{a.peripheral} {a.signal}</div>
      {:else}
        none
      {/each}
    </dd>

    <dt>Flags</dt>
    <dd>
      {#each pin.flags as flag (flag)}
        <div><span class="flag" aria-hidden="true">⚑</span> <span class="mono">{flag}</span>
          <span class="source">from {pin.flags_source[flag]}</span></div>
      {:else}
        none
      {/each}
    </dd>

    <dt>Override</dt>
    <dd>
      {#each pin.overrides as o (o.allow)}
        <div><span class="mono">allow {o.allow}</span>: “{o.note}”</div>
      {:else}
        none
      {/each}
    </dd>

    <dt>Issues</dt>
    <dd>
      {#each pin.issues as issue (issue.code + issue.where)}
        <div class={issue.severity}>{issue.severity}[{issue.code}] {issue.message}</div>
      {:else}
        none
      {/each}
    </dd>

    <dt>Can carry</dt>
    <dd class="functions">
      {#each pin.functions as fn (fn)}<span class="mono fn">{fn}</span>{/each}
    </dd>
  </dl>

  <section class="doc" aria-label="Datasheet">
    <h3>Datasheet</h3>
    {#if !hasDatasheet}
      <p class="none">No datasheet for {part}. Nothing here is a datasheet fact.</p>
    {:else if doc}
      <p class="heading">{doc.heading}</p>
      {#each blocks(doc.text) as block, i (i)}
        {#if block.kind === "table"}
          <table>
            <thead><tr>{#each block.head as cell, c (c)}<th>{cell}</th>{/each}</tr></thead>
            <tbody>
              {#each block.rows as row, r (r)}
                <tr class:hit={row.some((cell) => cell === pin.pin || cell === pin.datasheet_name)}>
                  {#each row as cell, c (c)}<td>{cell}</td>{/each}
                </tr>
              {/each}
            </tbody>
          </table>
        {:else}
          <p class="para">{block.text}</p>
        {/if}
      {/each}
      <p class="cite">Cite: <span class="mono">{doc.citation}</span></p>
      <p class="sources">{doc.sources.join(" · ")}</p>
    {:else}
      <p class="none">The matching section is looked up live (search_docs) once the UI is wired.</p>
    {/if}
  </section>
</section>

<style>
  .detail {
    padding: 16px 18px;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 10px;
    margin-bottom: 14px;
  }
  h2 {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
  }
  .sub {
    color: var(--muted);
    font-size: 12px;
  }
  .family {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--cream);
    font-size: 12px;
    font-weight: 600;
    padding: 1px 8px 1px 4px;
    border: 1px solid var(--bone);
    border-radius: 4px;
  }
  .line-chip {
    width: 16px;
    height: 2px;
    background: var(--pin-idle);
  }
  .line-chip.connected {
    height: 3px;
    background: var(--bone);
  }
  .desc {
    margin: -4px 0 14px;
  }
  .desc p {
    margin: 0;
    font-size: 12px;
    color: var(--bone);
  }
  .desc .cite {
    margin-top: 3px;
    font-size: 11px;
    color: var(--muted);
  }
  dl {
    display: grid;
    grid-template-columns: 92px 1fr;
    gap: 8px 12px;
    margin: 0 0 18px;
  }
  dt {
    color: var(--muted);
    font-size: 12px;
    padding-top: 1px;
  }
  dd {
    margin: 0;
    color: var(--bone);
  }
  .flag {
    color: var(--cream);
  }
  .source {
    color: var(--muted);
    font-size: 12px;
  }
  .error {
    color: var(--error-text);
  }
  .functions {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .fn {
    font-size: 11px;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 0 5px;
  }
  .doc h3 {
    font-size: 12px;
    color: var(--muted);
    font-weight: 400;
    margin: 0 0 6px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .heading {
    margin: 0 0 6px;
    font-weight: 600;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--bone);
    background: var(--bg);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }
  th,
  td {
    text-align: left;
    padding: 4px 8px;
    border-bottom: 1px solid var(--line);
    vertical-align: top;
  }
  th {
    color: var(--muted);
    font-weight: 400;
  }
  tr.hit td {
    background: var(--blue);
    color: var(--cream);
  }
  .para {
    margin: 0 0 6px;
    color: var(--bone);
    font-size: 13px;
  }
  .cite {
    margin: 8px 0 2px;
    font-size: 12px;
    color: var(--bone);
  }
  .sources,
  .none {
    margin: 0;
    font-size: 12px;
    color: var(--muted);
  }
</style>
