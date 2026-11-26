<script lang="ts">
  // One pin row as the pin map draws it: net label, solid tag with a dithered strip, dithered stub,
  // pin name. Sizes are the pin map's (tag 18 px, strip 10 px, stub 6 px, dither cell 2 px),
  // multiplied by `scale`.
  import DitherCanvas from "../lib/DitherCanvas.svelte";
  import type { Swatch } from "../lib/dither";

  type Props = {
    swatch: Swatch | null;
    tag: string;
    net: string;
    name: string;
    state?: "ok" | "warning" | "error";
    scale?: number;
    grey?: boolean;
  };
  let { swatch, tag, net, name, state = "ok", scale = 1, grey = false }: Props = $props();
</script>

<div class="pin {state}" style="--k: {scale}">
  <span class="net mono">{net}</span>
  {#if state !== "ok"}
    <span class="glyph" aria-label={state}>{state === "error" ? "✕" : "!"}</span>
  {/if}
  {#if tag}
    <span class="tag">
      <span class="text mono">{tag}</span>
      {#if swatch}<DitherCanvas {swatch} width={10 * scale} height={16 * scale} cell={2 * scale} {grey} />{/if}
    </span>
  {/if}
  {#if swatch}
    <DitherCanvas {swatch} width={28 * scale} height={6 * scale} cell={2 * scale} {grey} />
  {:else}
    <span class="idle"></span>
  {/if}
  <span class="name mono">{name}</span>
</div>

<style>
  .pin {
    white-space: nowrap;
    display: flex;
    align-items: center;
    gap: calc(6px * var(--k));
    height: calc(24px * var(--k));
    font-size: calc(11px * var(--k));
  }
  .net {
    width: calc(96px * var(--k));
    text-align: right;
    color: var(--bone);
  }
  .glyph {
    width: calc(14px * var(--k));
    text-align: center;
    font-weight: 600;
  }
  .error .glyph {
    color: var(--error-text);
  }
  .warning .glyph {
    color: var(--warning);
  }
  .tag {
    flex: none;
    display: inline-flex;
    align-items: center;
    height: calc(18px * var(--k));
    padding-left: calc(8px * var(--k));
    background: var(--surface);
    border: calc(1px * var(--k)) solid var(--bone);
    border-radius: calc(3px * var(--k));
    overflow: hidden;
  }
  .error .tag {
    border: calc(2px * var(--k)) solid var(--error);
  }
  .warning .tag {
    border: calc(2px * var(--k)) solid var(--warning);
  }
  .text {
    color: var(--cream);
    font-weight: 600;
    padding-right: calc(8px * var(--k));
  }
  .idle {
    width: calc(28px * var(--k));
    height: calc(1.5px * var(--k));
    background: var(--pin-idle);
  }
  .name {
    color: var(--cream);
  }
</style>
