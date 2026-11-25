import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

export default {
  preprocess: vitePreprocess(),
  // Runes mode everywhere: Svelte 4 syntax (`export let`, `$:`) fails to compile.
  compilerOptions: { runes: true },
};
