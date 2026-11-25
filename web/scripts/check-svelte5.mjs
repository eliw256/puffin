// Fails on Svelte 4 syntax in .svelte files: `export let`, `$:` reactive statements, and `on:`
// event directives. Svelte 5 code uses $props(), $derived/$effect, and onclick= attributes.
// Usage: node scripts/check-svelte5.mjs [dir]   (default: src)
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const RULES = [
  { name: "export let (use $props())", pattern: /^\s*export\s+let\s/ },
  { name: "$: reactive statement (use $derived or $effect)", pattern: /^\s*\$:\s/ },
  { name: "on: event directive (use onclick= etc.)", pattern: /<[^>]*\son:[a-z]+(\|[a-z|]+)?[\s=>]/ },
];

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "node_modules" ? [] : files(path);
    return name.endsWith(".svelte") ? [path] : [];
  });
}

const root = process.argv[2] ?? "src";
const problems = [];
for (const file of files(root)) {
  readFileSync(file, "utf8")
    .split("\n")
    .forEach((line, index) => {
      for (const rule of RULES) {
        if (rule.pattern.test(line)) problems.push(`${relative(".", file)}:${index + 1}: ${rule.name}`);
      }
    });
}
if (problems.length) {
  console.error(`Svelte 4 syntax found:\n${problems.join("\n")}`);
  process.exit(1);
}
console.log(`check-svelte5: ${files(root).length} file(s) clean`);
