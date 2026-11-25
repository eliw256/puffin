// Just enough Markdown for extracted datasheet sections: pipe tables and paragraphs.

export type Block = { kind: "table"; head: string[]; rows: string[][] } | { kind: "text"; text: string };

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function blocks(source: string): Block[] {
  const out: Block[] = [];
  const lines = source.split("\n");
  let i = 0;
  while (i < lines.length) {
    if (lines[i].trim().startsWith("|")) {
      const table: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) table.push(lines[i++]);
      const rows = table.filter((line) => !/^\s*\|?\s*:?-{2,}/.test(line)).map(cells);
      out.push({ kind: "table", head: rows[0] ?? [], rows: rows.slice(1) });
    } else {
      const text: string[] = [];
      while (i < lines.length && !lines[i].trim().startsWith("|")) text.push(lines[i++]);
      const joined = text.join(" ").trim();
      if (joined) out.push({ kind: "text", text: joined });
    }
  }
  return out;
}
