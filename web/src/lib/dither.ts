// Ordered (Bayer 4x4) dither swatches built only from the §7 palette (see dither.json).
import data from "./dither.json";
import type { Family } from "./types";

export type ColorName = keyof typeof data.colors;
export type Swatch = { a: ColorName; b: ColorName | null; ratio: number };

export const COLORS = data.colors as Record<ColorName, string>;
export const COLOR_NAMES = Object.keys(COLORS) as ColorName[];
export const RATIOS: number[] = data.ratios;
export const BAYER4: number[][] = data.bayer4;
export const FAMILY_SWATCH = data.families as Record<Exclude<Family, "unused">, Swatch>;

function rgb(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
}

/** True where the dither cell at (x, y) shows colour b. ratio = share of b (0..1). */
export function usesB(swatch: Swatch, x: number, y: number): boolean {
  return swatch.b !== null && BAYER4[y & 3][x & 3] < swatch.ratio * 16;
}

export function cellColor(swatch: Swatch, x: number, y: number): string {
  return usesB(swatch, x, y) && swatch.b ? COLORS[swatch.b] : COLORS[swatch.a];
}

const toLinear = (c: number) => {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};
const toSrgb = (v: number) => 255 * (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);

/** Relative luminance of a hex colour (WCAG). */
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map(toLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Hex of the grey with the same luminance, for greyscale previews. */
export function grey(hex: string): string {
  const v = Math.round(toSrgb(luminance(hex)));
  const h = v.toString(16).padStart(2, "0");
  return `#${h}${h}${h}`;
}

/** Mean luminance of the swatch as the eye averages it (linear light). */
export function meanLuminance(swatch: Swatch): number {
  const share = swatch.b === null ? 0 : swatch.ratio;
  const la = luminance(COLORS[swatch.a]);
  const lb = swatch.b ? luminance(COLORS[swatch.b]) : la;
  return la * (1 - share) + lb * share;
}

/** Perceived lightness 0..100 (CIE L*) of the averaged swatch. */
export function lightness(swatch: Swatch): number {
  const y = meanLuminance(swatch);
  return y > 216 / 24389 ? 116 * Math.cbrt(y) - 16 : (24389 / 27) * y;
}

export function swatchLabel(swatch: Swatch): string {
  return swatch.b === null ? `${swatch.a} solid` : `${swatch.a} + ${Math.round(swatch.ratio * 100)}% ${swatch.b}`;
}

/** Every candidate: solids, then each pair at each ratio. Stable ids for picking. */
export function candidates(): { id: string; swatch: Swatch }[] {
  const out: { id: string; swatch: Swatch }[] = COLOR_NAMES.map((a, i) => ({
    id: `S${i + 1}`,
    swatch: { a, b: null, ratio: 0 },
  }));
  let n = 1;
  for (let i = 0; i < COLOR_NAMES.length; i++) {
    for (let j = i + 1; j < COLOR_NAMES.length; j++) {
      for (const ratio of RATIOS) {
        out.push({ id: `M${String(n++).padStart(2, "0")}`, swatch: { a: COLOR_NAMES[i], b: COLOR_NAMES[j], ratio } });
      }
    }
  }
  return out;
}

export function sameSwatch(x: Swatch, y: Swatch): boolean {
  if (x.b === null || y.b === null) return x.b === y.b && x.a === y.a;
  if (x.a === y.a && x.b === y.b) return x.ratio === y.ratio;
  return x.a === y.b && x.b === y.a && x.ratio === 1 - y.ratio;
}
