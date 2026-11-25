// Shapes of the data the pin map uses. They mirror PuffinService results (docs/M3_MCP.md).

export type Issue = {
  severity: "error" | "warning";
  code: string;
  where: string;
  message: string;
  hint: string;
};

export type Shape =
  | { kind: "rect"; x: number; y: number; w: number; h: number }
  | { kind: "polyline"; points: number[][] }
  | { kind: "circle"; center: number[]; r: number }
  | { kind: "arc"; points: number[][] }
  | { kind: "text"; text: string; at: number[]; angle: number };

export type SymbolPin = {
  number: string;
  name: string;
  point: number[]; // connection end, mm, y down
  body: number[]; // where the pin meets the body
  outward: number; // 0 right, 90 up, 180 left, 270 down
  hidden: boolean;
};

export type SymbolView = {
  ref: string;
  lib_id: string;
  pin_name_offset: number;
  shapes: Shape[];
  pins: SymbolPin[];
  skipped: string[];
};

export type Assignment = { peripheral: string; signal: string };

export type Family =
  | "gpio"
  | "spi"
  | "i2c"
  | "uart"
  | "adc"
  | "usb"
  | "power"
  | "ground"
  | "unused";

export type PinInfo = {
  pin: string;
  numbers: number[];
  datasheet_name: string | null;
  description: string | null; // verbatim from the datasheet
  description_source: string | null;
  type: string | null;
  rail: string | null;
  functions: string[];
  flags: string[];
  flags_source: Record<string, string>;
  net: string | null;
  assignments: Assignment[];
  overrides: { allow: string; note: string }[];
  issues: Issue[];
  family: Family;
};

export type CandidateStatus = "current" | "free" | "connected" | "in_use" | "needs_override";
export type DropStatus = CandidateStatus | "illegal";

export type Candidate = {
  pin: string;
  status: CandidateStatus;
  flags: string[];
  needs_override: string[];
  net: string | null;
  assigned: Assignment[];
};

export type LegalPins = {
  component: string;
  peripheral: string;
  signal: string;
  base: string;
  current_pin: string | null;
  candidates: Candidate[];
};

export type DocSection = {
  part: string;
  section_id: string;
  heading: string;
  text: string;
  sources: string[];
  citation: string;
};

export type WritebackPlan = {
  schematic: string;
  available: boolean;
  reason: string;
  moves: string[];
  verified: boolean;
  diff: string;
};

export type Proposal = {
  ok: boolean;
  proposal_id: string | null;
  summary: string[];
  diff: string;
  introduced_errors: Issue[];
  warnings: Issue[];
  generated: { board: string; path: string; diff: string }[];
  writeback?: WritebackPlan[];
};

export type Snapshot = {
  project: string;
  boards: string[];
  component: string;
  part: string;
  variant: string | null;
  symbol: SymbolView;
  pin_by_number: Record<string, string>;
  pins: Record<string, PinInfo>;
  legal: LegalPins;
  selected: string;
  doc: Record<string, DocSection>;
  proposal: Proposal;
  proposal_writeback: Proposal;
  check: { ok: boolean; errors: number; warnings: number; issues: Issue[] };
};
