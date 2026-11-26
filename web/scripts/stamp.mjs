// Records a hash of the sources the bundle was built from (puffin/web/static/SOURCE_HASH), so a
// test can fail when web/ changes without `npm run build`. Mirrored in tests/test_web.py.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync, copyFileSync } from "node:fs";
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
writeFileSync("../puffin/web/static/SOURCE_HASH", hash.digest("hex") + "\n");
copyFileSync("node_modules/elkjs/LICENSE.md", "../puffin/web/static/ELKJS-LICENSE.md");
console.log(`stamped ${files.length} source files`);
