// Family swatches: a base colour, a mask colour and a 4x4 pattern (dither.json, spec §7).
import data from "./dither.json";
import type { Family } from "./types";

type Cool = keyof typeof data.colors;
type Warm = keyof typeof data.warm;
export type ColorName = Cool | Warm;
export type PatternName = keyof typeof data.patterns;
export type Swatch = { a: ColorName; b: ColorName | null; pattern: PatternName };

export const COLORS: Record<ColorName, string> = { ...data.colors, ...data.warm };
export const COOL = Object.keys(data.colors) as Cool[];
export const WARM = Object.keys(data.warm) as Warm[];
export const WARM_PARTNERS = data.warm_rules.partners as Cool[];
export const MAX_WARM_SHARE = data.warm_rules.max_share;
export const MAX_CHROMA_RATIO = data.warm_rules.max_chroma_ratio;
export const PATTERNS = Object.keys(data.patterns) as PatternName[];
export const FAMILY_SWATCH = data.families as Record<Exclude<Family, "unused" | "connected">, Swatch>;

const MASKS = Object.fromEntries(
  Object.entries(data.patterns).map(([name, p]) => [name, p.mask.map((row) => [...row].map((c) => c === "1"))]),
) as Record<PatternName, boolean[][]>;

export function patternLabel(pattern: PatternName): string {
  return data.patterns[pattern].label;
}

/** Share of cells showing the mask colour b. */
export function share(pattern: PatternName): number {
  return MASKS[pattern].flat().filter(Boolean).length / 16;
}

export function isWarm(color: ColorName | null): boolean {
  return color !== null && (WARM as string[]).includes(color);
}

/** Share of the swatch that is red or orange. */
export function warmShare(swatch: Swatch): number {
  let s = 0;
  if (isWarm(swatch.a)) s += swatch.b === null ? 1 : 1 - share(swatch.pattern);
  if (swatch.b && isWarm(swatch.b)) s += share(swatch.pattern);
  return s;
}

export function cellColor(swatch: Swatch, x: number, y: number): string {
  const useB = swatch.b !== null && MASKS[swatch.pattern][y & 3][x & 3];
  return useB && swatch.b ? COLORS[swatch.b] : COLORS[swatch.a];
}

const toLinear = (c: number) => {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};
const toSrgb = (v: number) => 255 * (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);

export function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => toLinear(parseInt(hex.slice(i, i + 2), 16)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The grey with the same luminance, for greyscale previews. */
export function grey(hex: string): string {
  const v = Math.round(toSrgb(luminance(hex)));
  const h = v.toString(16).padStart(2, "0");
  return `#${h}${h}${h}`;
}

/** Perceived lightness 0..100 (CIE L*) of the swatch as the eye averages it. */
export function lightness(swatch: Swatch): number {
  const s = swatch.b === null ? 0 : share(swatch.pattern);
  const y = luminance(COLORS[swatch.a]) * (1 - s) + (swatch.b ? luminance(COLORS[swatch.b]) * s : 0);
  return y > 216 / 24389 ? 116 * Math.cbrt(y) - 16 : (24389 / 27) * y;
}

function linearRgb(hex: string): number[] {
  return [1, 3, 5].map((i) => toLinear(parseInt(hex.slice(i, i + 2), 16)));
}

/** OKLab chroma of a linear-light colour. */
function chroma([r, g, b]: number[]): number {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return Math.hypot(A, B);
}

/** Averaged chroma of a warm mix relative to its solid warm colour (0 for cool swatches). */
export function warmChromaRatio(swatch: Swatch): number {
  if (!swatch.b || !isWarm(swatch.b)) return 0;
  const s = share(swatch.pattern);
  const a = linearRgb(COLORS[swatch.a]);
  const b = linearRgb(COLORS[swatch.b]);
  return chroma(a.map((v, i) => v * (1 - s) + b[i] * s)) / chroma(b);
}

export function swatchLabel(swatch: Swatch): string {
  if (swatch.b === null) return `${swatch.a} solid`;
  return `${swatch.a} + ${swatch.b} · ${patternLabel(swatch.pattern)}`;
}

/** Allowed by the palette rules: no solid warm; warm only as the mask colour, share <= 50%, on
 *  dark/blue/bone, and muted (averaged chroma <= half the solid's). */
export function allowed(swatch: Swatch): boolean {
  if (swatch.b === null) return !isWarm(swatch.a);
  if (isWarm(swatch.a) && isWarm(swatch.b)) return false;
  if (isWarm(swatch.a)) return false; // warm is always the mask colour, so its share is explicit
  if (isWarm(swatch.b)) {
    return (
      WARM_PARTNERS.includes(swatch.a as Cool) &&
      warmShare(swatch) <= MAX_WARM_SHARE &&
      warmChromaRatio(swatch) <= MAX_CHROMA_RATIO
    );
  }
  return true;
}

export type Candidate = { id: string; swatch: Swatch };

/** Every allowed candidate, grouped by colour pair; ids are stable for picking. */
export function candidateGroups(): { pair: string; items: Candidate[] }[] {
  const groups: { pair: string; items: Candidate[] }[] = [];
  const solids = COOL.filter((c) => c !== "dark").map((a, i) => ({
    id: `S${i + 1}`,
    swatch: { a, b: null, pattern: "solid" as PatternName },
  }));
  groups.push({ pair: "solids", items: solids });
  let n = 1;
  const pairs: [ColorName, ColorName][] = [];
  for (const a of COOL) for (const b of COOL) if (a !== b) pairs.push([a, b]);
  for (const w of WARM) for (const a of WARM_PARTNERS) pairs.push([a, w]);
  for (const [a, b] of pairs) {
    const items = PATTERNS.filter((p) => p !== "solid")
      .map((pattern) => ({ a, b, pattern }) as Swatch)
      .filter(allowed)
      .map((swatch) => ({ id: `M${String(n++).padStart(3, "0")}`, swatch }));
    groups.push({ pair: `${a} + ${b}`, items });
  }
  return groups;
}

export function sameSwatch(x: Swatch, y: Swatch): boolean {
  return x.a === y.a && x.b === y.b && x.pattern === y.pattern;
}
