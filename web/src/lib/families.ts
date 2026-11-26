import { FAMILY_SWATCH, type Swatch } from "./dither";
import type { DropStatus, Family, PinInfo } from "./types";

export const FAMILY_LABEL: Record<Family, string> = {
  gpio: "GPIO",
  spi: "SPI",
  i2c: "I²C",
  uart: "UART",
  adc: "ADC",
  usb: "USB / JTAG",
  power: "Power",
  ground: "Ground",
  connected: "Connected, no assignment",
  unused: "Unused (no net)",
};

export const FAMILY_ORDER: Family[] = [
  "gpio",
  "spi",
  "i2c",
  "uart",
  "adc",
  "usb",
  "power",
  "ground",
  "connected",
  "unused",
];

/** The family's dither swatch; connected and unused pins have none (they draw a solid line). */
export function familySwatch(family: Family): Swatch | null {
  return family === "unused" || family === "connected" ? null : FAMILY_SWATCH[family];
}

/** What the pin does in this design, in a few words. */
export function doing(pin: PinInfo): string {
  const role = pin.assignments.map((a) => `${a.peripheral} ${a.signal}`).join(", ");
  if (role && pin.net) return `${role} on net ${pin.net}`;
  if (role) return `${role} (no net)`;
  if (pin.net) return `net ${pin.net}, no assignment`;
  return "not used in this design";
}

/** The text in a pin's outside tag: what the pin does, or nothing when it does nothing. */
export function tagText(pin: PinInfo): string {
  if (pin.assignments.length) {
    const a = pin.assignments[0];
    return `${a.peripheral} ${a.signal}`;
  }
  if (pin.family === "power") return pin.rail ? pin.rail.toUpperCase() : pin.pin;
  if (pin.family === "ground") return "GND";
  return "";
}

export const STATUS_LABEL: Record<DropStatus, string> = {
  current: "current pin",
  free: "free",
  connected: "on another net",
  in_use: "in use",
  needs_override: "needs override",
  illegal: "can't carry this signal",
};

/** A short glyph per drop status, so states never rely on colour or line style alone. */
export const STATUS_GLYPH: Record<DropStatus, string> = {
  current: "●",
  free: "✓",
  connected: "↔",
  in_use: "■",
  needs_override: "⚑",
  illegal: "⊘",
};
