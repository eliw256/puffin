<script lang="ts">
  // Swatch sheet (M4 step 2 feedback): every §7 solid and every 2-colour ordered-dither mix at
  // 25/50/75 %, on the real background, at 1x and 2x, with a greyscale preview.
  import DitherCanvas from "../lib/DitherCanvas.svelte";
  import {
    candidates,
    COLORS,
    FAMILY_SWATCH,
    lightness,
    sameSwatch,
    swatchLabel,
    type Swatch,
  } from "../lib/dither";
  import { FAMILY_LABEL } from "../lib/families";

  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
  const all = candidates();
  const solids = all.filter((c) => c.swatch.b === null);
  const mixes = all.filter((c) => c.swatch.b !== null);
  const pairs = [...new Set(mixes.map((m) => `${m.swatch.a}+${m.swatch.b}`))];

  function idOf(swatch: Swatch): string {
    return all.find((c) => sameSwatch(c.swatch, swatch))?.id ?? "?";
  }
  const assigned = Object.entries(FAMILY_SWATCH).map(([family, swatch]) => ({
    family: family as keyof typeof FAMILY_SWATCH,
    swatch,
    id: idOf(swatch),
  }));
  const usedBy = (id: string) => assigned.filter((a) => a.id === id).map((a) => FAMILY_LABEL[a.family]);
</script>

{#snippet card(id: string, swatch: Swatch)}
  {@const used = usedBy(id)}
  <figure class="card" class:used={used.length > 0}>
    <figcaption>
      <strong class="mono">{id}</strong>
      <span>{swatchLabel(swatch)}</span>
      <span class="l mono">L* {lightness(swatch).toFixed(0)}</span>
    </figcaption>
    <div class="row">
      <div><DitherCanvas {swatch} width={64} height={40} cell={1} label="{id} at 1x" /><small>1x</small></div>
      <div><DitherCanvas {swatch} width={64} height={40} cell={2} label="{id} at 2x" /><small>2x</small></div>
      <div><DitherCanvas {swatch} width={64} height={40} cell={2} grey label="{id} greyscale" /><small>grey</small></div>
    </div>
    <div class="context" aria-hidden="true">
      <span class="stub"><DitherCanvas {swatch} width={48} height={6} cell={1} /></span>
      <span class="tag"><DitherCanvas {swatch} width={8} height={18} cell={1} /><span class="mono">spi2 sck</span></span>
    </div>
    {#if used.length}<p class="used-by">provisional: {used.join(", ")}</p>{/if}
  </figure>
{/snippet}

<main>
  <header>
    <h1>Swatch sheet</h1>
    <p>
      Every §7 colour usable for pin families (red and orange are reserved for errors and warnings),
      and every two-colour <strong>ordered (Bayer 4×4)</strong> mix at 25, 50 and 75 %. All on the
      real background, drawn in whole device pixels and snapped to the pixel grid: 1x is one CSS
      pixel per dither cell ({dpr} device pixel{dpr === 1 ? "" : "s"} on this display), 2x is two.
      “grey” shows the same swatch by luminance only. L* is the perceived lightness of the mix as
      the eye averages it. Text never sits on a swatch: in the UI a family shows as a dithered pin
      stub and a strip at the end of a solid tag, as in the small preview under each swatch.
    </p>
    <p>Pick one id per family; the current picks below are provisional.</p>
    <table>
      <thead><tr><th>Family</th><th>Provisional</th><th>Swatch</th><th>L*</th></tr></thead>
      <tbody>
        {#each assigned as a (a.family)}
          <tr>
            <td>{FAMILY_LABEL[a.family]}</td>
            <td class="mono">{a.id}</td>
            <td><span class="inline"><DitherCanvas swatch={a.swatch} width={40} height={14} cell={1} />
              <DitherCanvas swatch={a.swatch} width={40} height={14} cell={1} grey /></span></td>
            <td class="mono">{lightness(a.swatch).toFixed(0)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </header>

  <section>
    <h2>Solids</h2>
    <div class="grid">
      {#each solids as s (s.id)}{@render card(s.id, s.swatch)}{/each}
    </div>
  </section>

  {#each pairs as pair (pair)}
    <section>
      <h2>{pair.replace("+", " + ")} <span class="hex mono">{COLORS[pair.split("+")[0] as keyof typeof COLORS]} · {COLORS[pair.split("+")[1] as keyof typeof COLORS]}</span></h2>
      <div class="grid">
        {#each mixes.filter((m) => `${m.swatch.a}+${m.swatch.b}` === pair) as m (m.id)}{@render card(m.id, m.swatch)}{/each}
      </div>
    </section>
  {/each}
</main>

<style>
  main {
    padding: 24px 32px 64px;
    max-width: 1200px;
  }
  h1 {
    margin: 0 0 8px;
    font-size: 22px;
  }
  header p {
    color: var(--bone);
    max-width: 78ch;
  }
  table {
    border-collapse: collapse;
    margin: 12px 0 8px;
    font-size: 13px;
  }
  th,
  td {
    text-align: left;
    padding: 4px 14px 4px 0;
    border-bottom: 1px solid var(--line);
    color: var(--bone);
  }
  th {
    color: var(--muted);
    font-weight: 400;
  }
  .inline {
    display: inline-flex;
    gap: 6px;
  }
  h2 {
    font-size: 15px;
    margin: 28px 0 10px;
  }
  .hex {
    color: var(--muted);
    font-weight: 400;
    font-size: 12px;
    margin-left: 8px;
  }
  .grid {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
  }
  .card {
    margin: 0;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    width: 252px;
  }
  .card.used {
    border-color: var(--cream);
  }
  figcaption {
    display: flex;
    gap: 8px;
    align-items: baseline;
    font-size: 12.5px;
    color: var(--bone);
    margin-bottom: 8px;
  }
  figcaption strong {
    color: var(--cream);
  }
  .l {
    margin-left: auto;
    color: var(--muted);
  }
  .row {
    display: flex;
    gap: 12px;
  }
  .row small {
    display: block;
    color: var(--muted);
    font-size: 11px;
    margin-top: 2px;
  }
  .context {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-top: 10px;
  }
  .stub {
    display: inline-flex;
  }
  .tag {
    display: inline-flex;
    align-items: center;
    background: var(--surface);
    border: 1px solid var(--bone);
    border-radius: 3px;
    overflow: hidden;
    height: 20px;
  }
  .tag .mono {
    padding: 0 8px;
    font-size: 11px;
    color: var(--cream);
  }
  .used-by {
    margin: 8px 0 0;
    font-size: 12px;
    color: var(--cream);
  }
</style>
