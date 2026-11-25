<script lang="ts">
  // A swatch drawn in device pixels: each dither cell is a whole number of device pixels and the
  // canvas is never resampled, so there is no moiré at any zoom or display density.
  import { cellColor, grey as toGrey, type Swatch } from "./dither";

  type Props = { swatch: Swatch; width: number; height: number; cell?: number; grey?: boolean; label?: string };
  let { swatch, width, height, cell = 1, grey = false, label = "" }: Props = $props();

  let canvas: HTMLCanvasElement | undefined = $state();
  let dpr = $state(typeof window === "undefined" ? 1 : window.devicePixelRatio || 1);

  $effect(() => {
    const query = matchMedia(`(resolution: ${dpr}dppx)`);
    const update = () => (dpr = window.devicePixelRatio || 1);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  });

  // Layout can place the canvas at a fractional device-pixel position (text widths, centring);
  // the browser would then resample it. Nudge it by less than one device pixel onto the grid.
  let snap = $state({ x: 0, y: 0 });

  function measure() {
    if (!canvas) return;
    const r = canvas.getBoundingClientRect();
    const x = r.left - snap.x;
    const y = r.top - snap.y;
    const next = { x: Math.round(x * dpr) / dpr - x, y: Math.round(y * dpr) / dpr - y };
    if (Math.abs(next.x - snap.x) > 1e-4 || Math.abs(next.y - snap.y) > 1e-4) snap = next;
  }

  $effect(() => {
    if (!canvas) return;
    measure();
    const observer = new ResizeObserver(() => measure());
    observer.observe(document.documentElement);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, { passive: true, capture: true });
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, { capture: true });
    };
  });

  $effect(() => {
    if (!canvas) return;
    const w = Math.round(width * dpr);
    const h = Math.round(height * dpr);
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const cellPx = Math.max(1, Math.round(cell * dpr));
    for (let y = 0; y < h; y += cellPx) {
      for (let x = 0; x < w; x += cellPx) {
        const color = cellColor(swatch, x / cellPx, y / cellPx);
        ctx.fillStyle = grey ? toGrey(color) : color;
        ctx.fillRect(x, y, cellPx, cellPx);
      }
    }
  });
</script>

<canvas
  bind:this={canvas}
  style="width: {width}px; height: {height}px; transform: translate({snap.x}px, {snap.y}px)"
  role={label ? "img" : "presentation"}
  aria-label={label || undefined}
></canvas>

<style>
  canvas {
    display: block;
    image-rendering: pixelated;
  }
</style>
