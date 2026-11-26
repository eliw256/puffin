<script lang="ts">
  // Swatch sheet: the proposed 8 family swatches at pin-map size, a "does the error still pop?"
  // check, and every allowed candidate (colour pair x pattern), with greyscale and L*.
  import DitherCanvas from "../lib/DitherCanvas.svelte";
  import {
    candidateGroups,
    FAMILY_SWATCH,
    lightness,
    PATTERNS,
    patternLabel,
    sameSwatch,
    swatchLabel,
    warmChromaRatio,
    warmShare,
    type PatternName,
    type Swatch,
  } from "../lib/dither";
  import { FAMILY_LABEL } from "../lib/families";
  import MockPin from "./MockPin.svelte";

  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
  const groups = candidateGroups();
  const all = groups.flatMap((g) => g.items);
  const idOf = (s: Swatch) => all.find((c) => sameSwatch(c.swatch, s))?.id ?? "?";
  const patternCols = PATTERNS.filter((p) => p !== "solid");

  type FamilyKey = keyof typeof FAMILY_SWATCH;
  const EXAMPLE: Record<FamilyKey, { tag: string; net: string; name: string }> = {
    gpio: { tag: "gpio io.boot_button", net: "BOOT_MAIN", name: "IO0" },
    spi: { tag: "spi2 sck", net: "IMU_SCK", name: "IO12" },
    i2c: { tag: "i2c0 sda", net: "IMU_SDA", name: "IO8" },
    uart: { tag: "uart1 tx", net: "LINK_MAIN_TX", name: "IO17" },
    adc: { tag: "adc1 ch3", net: "VBAT_SENSE", name: "IO4" },
    usb: { tag: "usb d+", net: "USB_DP", name: "IO20" },
    power: { tag: "3V3", net: "+3V3", name: "3V3" },
    ground: { tag: "GND", net: "GND", name: "GND" },
  };

  const families = (Object.entries(FAMILY_SWATCH) as [FamilyKey, Swatch][])
    .map(([family, swatch]) => ({ family, swatch, l: lightness(swatch) }))
    .sort((x, y) => x.l - y.l);
  const minGap = Math.min(...families.slice(1).map((f, i) => f.l - families[i].l));
  const gap = (i: number) => {
    const near = [families[i - 1], families[i + 1]].filter(Boolean).map((f) => Math.abs(f.l - families[i].l));
    return Math.min(...near);
  };

  // A row of real pins with one warning-state and one error-state pin mixed in.
  const popRows: { family: FamilyKey | null; state: "ok" | "warning" | "error" }[] = [
    { family: "gpio", state: "ok" },
    { family: "uart", state: "ok" },
    { family: "spi", state: "warning" },
    { family: "usb", state: "ok" },
    { family: "i2c", state: "ok" },
    { family: "uart", state: "error" },
    { family: "adc", state: "ok" },
    { family: "spi", state: "ok" },
    { family: "power", state: "ok" },
    { family: "ground", state: "ok" },
    { family: null, state: "ok" },
  ];

  function find(group: { items: { id: string; swatch: Swatch }[] }, pattern: PatternName) {
    return group.items.find((c) => c.swatch.pattern === pattern);
  }
  const used = (s: Swatch) =>
    families.filter((f) => sameSwatch(f.swatch, s)).map((f) => FAMILY_LABEL[f.family]);
</script>

<main>
  <header>
    <h1>Swatch sheet</h1>
    <p>
      Family swatches are a base colour, a mask colour and a 4×4 pattern. Solids: the §7 palette,
      with bg and surface as one “dark”. <strong>Red and orange appear only inside a dithered mix</strong>,
      at most 50 %, with dark, blue or bone; solid red/orange means error/warning, always with a glyph
      and outline. Everything is drawn in whole device pixels on the real background
      ({dpr} device px per CSS px here). L* is the lightness of the mix as the eye averages it; the
      greyscale previews use the same luminance.
    </p>
  </header>

  <section>
    <h2>Proposed families, at pin-map size</h2>
    <p class="note">
      Every family has a different pattern geometry, and lightness at least {minGap.toFixed(1)} L* from
      its neighbour, so they stay apart in greyscale. Red is held to 25 % so an error's solid red
      outline stays the strongest red on the map.
    </p>
    <table class="families">
      <thead>
        <tr><th>Family</th><th>Swatch</th><th>id</th><th>L*</th><th>gap</th><th>warm share / chroma</th><th>1x</th><th>2x</th><th>greyscale 1x</th></tr>
      </thead>
      <tbody>
        {#each families as f, i (f.family)}
          {@const ex = EXAMPLE[f.family]}
          <tr>
            <td>{FAMILY_LABEL[f.family]}</td>
            <td class="small">{swatchLabel(f.swatch)}</td>
            <td class="mono">{idOf(f.swatch)}</td>
            <td class="mono">{f.l.toFixed(1)}</td>
            <td class="mono">{gap(i).toFixed(1)}</td>
            <td class="mono">{warmShare(f.swatch) ? `${Math.round(warmShare(f.swatch) * 100)}% / ${Math.round(warmChromaRatio(f.swatch) * 100)}%` : "—"}</td>
            <td><MockPin swatch={f.swatch} tag={ex.tag} net={ex.net} name={ex.name} /></td>
            <td><MockPin swatch={f.swatch} tag={ex.tag} net={ex.net} name={ex.name} scale={2} /></td>
            <td class="grey"><MockPin swatch={f.swatch} tag={ex.tag} net={ex.net} name={ex.name} grey /></td>
          </tr>
        {/each}
      </tbody>
    </table>
  </section>

  <section>
    <h2>Does the error still pop?</h2>
    <p class="note">
      Real pins with the proposed swatches, one warning (orange outline, “!”) and one error (red
      outline, “✕”) mixed in. The error pin is a UART pin, whose swatch carries orange, on purpose.
    </p>
    <div class="pop">
      {#each [1, 2] as scale (scale)}
        <figure>
          <figcaption>{scale}x</figcaption>
          {#each popRows as row, i (i)}
            {@const ex = row.family ? EXAMPLE[row.family] : { tag: "", net: "", name: "IO21" }}
            <MockPin swatch={row.family ? FAMILY_SWATCH[row.family] : null} tag={ex.tag} net={ex.net}
              name={ex.name} state={row.state} {scale} />
          {/each}
        </figure>
      {/each}
      <figure class="grey">
        <figcaption>greyscale 1x</figcaption>
        {#each popRows as row, i (i)}
          {@const ex = row.family ? EXAMPLE[row.family] : { tag: "", net: "", name: "IO21" }}
          <MockPin swatch={row.family ? FAMILY_SWATCH[row.family] : null} tag={ex.tag} net={ex.net}
            name={ex.name} state={row.state} grey />
        {/each}
      </figure>
    </div>
  </section>

  <section>
    <h2>All allowed candidates</h2>
    <p class="note">
      Rows are colour pairs (base + mask colour), columns are patterns. Each cell: the swatch at the
      pin map's dither cell size, its greyscale, id and L*. A dash in a warm row means the mix is
      not allowed: over 50 % warm, or its averaged colour keeps more than half the chroma of solid
      red/orange, so it would read as near-solid.
      Cream-framed cells are the proposed families.
    </p>
    <table class="grid">
      <thead>
        <tr><th>pair</th>{#each patternCols as p (p)}<th>{patternLabel(p)}</th>{/each}</tr>
      </thead>
      <tbody>
        <tr>
          <th>solids</th>
          <td colspan={patternCols.length}>
            <div class="solids">
              {#each groups[0].items as c (c.id)}
                <div class="cell" class:used={used(c.swatch).length}>
                  <DitherCanvas swatch={c.swatch} width={40} height={20} cell={2} />
                  <DitherCanvas swatch={c.swatch} width={40} height={8} cell={2} grey />
                  <span class="mono">{c.id} · {c.swatch.a}</span>
                  <span class="mono l">L* {lightness(c.swatch).toFixed(0)}</span>
                </div>
              {/each}
            </div>
          </td>
        </tr>
        {#each groups.slice(1) as group (group.pair)}
          <tr>
            <th class="mono">{group.pair}</th>
            {#each patternCols as p (p)}
              {@const c = find(group, p)}
              <td>
                {#if c}
                  <div class="cell" class:used={used(c.swatch).length} title={swatchLabel(c.swatch)}>
                    <DitherCanvas swatch={c.swatch} width={40} height={20} cell={2} />
                    <DitherCanvas swatch={c.swatch} width={40} height={8} cell={2} grey />
                    <span class="mono">{c.id}</span>
                    <span class="mono l">L* {lightness(c.swatch).toFixed(0)}</span>
                  </div>
                {:else}
                  <span class="na" title="not allowed: warm share over 50%, or reads too close to solid red/orange">—</span>
                {/if}
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </section>
</main>

<style>
  main {
    padding: 24px 32px 64px;
  }
  h1 {
    margin: 0 0 8px;
    font-size: 22px;
  }
  h2 {
    font-size: 16px;
    margin: 32px 0 6px;
  }
  header p,
  .note {
    color: var(--bone);
    max-width: 90ch;
    margin: 0 0 12px;
  }
  table {
    border-collapse: collapse;
    font-size: 12.5px;
  }
  th,
  td {
    text-align: left;
    padding: 6px 12px 6px 0;
    border-bottom: 1px solid var(--line);
    color: var(--bone);
    vertical-align: middle;
  }
  th {
    color: var(--muted);
    font-weight: 400;
  }
  .small {
    font-size: 12px;
    max-width: 150px;
  }
  .grey {
    filter: grayscale(1);
  }
  .pop {
    display: flex;
    gap: 40px;
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .pop figure {
    margin: 0;
  }
  figcaption {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 6px;
  }
  .grid td,
  .grid th {
    padding: 6px 8px 6px 0;
  }
  .grid th {
    white-space: nowrap;
  }
  .cell {
    display: inline-flex;
    flex-direction: column;
    gap: 2px;
    padding: 3px;
    border: 1px solid transparent;
    border-radius: 3px;
    font-size: 10.5px;
  }
  .cell.used {
    border-color: var(--cream);
  }
  .cell .l {
    color: var(--muted);
  }
  .solids {
    display: flex;
    gap: 14px;
  }
  .na {
    color: var(--muted);
  }
</style>
