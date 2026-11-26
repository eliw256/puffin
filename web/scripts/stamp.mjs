// Records a hash of the sources the bundle was built from (puffin/web/static/SOURCE_HASH), so a
// test can fail when web/ changes without `npm run build`. Mirrored in tests/test_web.py.
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export const INPUTS = ["index.html", "package-lock.json", "svelte.config.js", "tsconfig.json", "vite.config.ts"];
export const DIRS = ["src/app", "src/lib"];

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

const files = [...INPUTS, ...DIRS.flatMap(walk)].map((p) => p.split("\\").join("/")).sort();
const hash = createHash("sha256");
for (const file of files) {
  hash.update(file + "\0");
  hash.update(readFileSync(file));
  hash.update("\0");
}
const STATIC = "../puffin/web/static";
writeFileSync(`${STATIC}/SOURCE_HASH`, hash.digest("hex") + "\n");

// Third-party licences travel with the bundle (see THIRD_PARTY_NOTICES.md).
mkdirSync(`${STATIC}/licenses`, { recursive: true });
const LICENSES = {
  "elkjs-LICENSE.md": "node_modules/elkjs/LICENSE.md",
  "svelte-LICENSE.md": "node_modules/svelte/LICENSE.md",
  "inter-OFL.txt": "node_modules/@fontsource/inter/LICENSE",
  "jetbrains-mono-OFL.txt": "node_modules/@fontsource/jetbrains-mono/LICENSE",
};
for (const [name, source] of Object.entries(LICENSES)) copyFileSync(source, `${STATIC}/licenses/${name}`);
copyFileSync("../THIRD_PARTY_NOTICES.md", `${STATIC}/licenses/THIRD_PARTY_NOTICES.md`);

// Minification strips elkjs's own header; put its licence and source pointer back on the file.
const elkVersion = JSON.parse(readFileSync("node_modules/elkjs/package.json", "utf8")).version;
const banner =
  `/*! elkjs ${elkVersion} (Eclipse Layout Kernel), used unmodified under EPL-2.0 ` +
  `(offered as EPL-2.0 OR GPL-3.0-or-later). Source: https://github.com/kieler/elkjs, ` +
  `https://github.com/eclipse/elk, npm elkjs@${elkVersion}. Licence: /licenses/elkjs-LICENSE.md */\n`;
for (const name of readdirSync(`${STATIC}/assets`).filter((n) => /^elkjs-.*\.js$/.test(n))) {
  const path = `${STATIC}/assets/${name}`;
  const code = readFileSync(path, "utf8");
  if (!code.startsWith("/*! elkjs")) writeFileSync(path, banner + code);
}
console.log(`stamped ${files.length} source files; licences copied`);
