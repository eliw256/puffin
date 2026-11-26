import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// `npm run build` writes only the app (index.html) into the Python package, which serves it;
// prototype.html and swatches.html are dev-only pages (`npm run prototype`, `npm run swatches`).
export default defineConfig({
  plugins: [svelte()],
  server: { host: "127.0.0.1", port: 5180 },
  build: {
    outDir: "../puffin/web/static",
    emptyOutDir: true,
    assetsInlineLimit: 0, // fonts as files: the CSP allows font-src 'self', never data: URIs
    rollupOptions: { input: { index: "index.html" } },
  },
});
