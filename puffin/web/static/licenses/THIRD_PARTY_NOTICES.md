# Third-party notices

Puffin is MIT-licensed (see `LICENSE`). The built web UI in `puffin/web/static/` also contains
the third-party code and fonts below, each under its own licence. They are used **unmodified**.
Their full licence texts ship with the bundle in `puffin/web/static/licenses/`.

| Component | Version | Licence | Where it is in the bundle | Source |
|---|---|---|---|---|
| elkjs (Eclipse Layout Kernel, JavaScript build) | 0.13.0 | EPL-2.0 (chosen from "EPL-2.0 OR GPL-3.0-or-later") | `assets/elkjs-*.js`, a file of its own, loaded only when the block diagram opens | https://github.com/kieler/elkjs and https://github.com/eclipse/elk; npm package `elkjs@0.13.0` |
| Svelte runtime | 5.57.3 | MIT | compiled into `assets/index-*.js` | https://github.com/sveltejs/svelte; npm package `svelte@5.57.3` |
| Inter (font) | 5.3.0 (`@fontsource/inter`) | SIL Open Font License 1.1 | `assets/inter-*.woff2`, `assets/inter-*.woff` | https://github.com/rsms/inter; https://github.com/fontsource/font-files |
| JetBrains Mono (font) | 5.3.0 (`@fontsource/jetbrains-mono`) | SIL Open Font License 1.1 | `assets/jetbrains-mono-*.woff2`, `assets/jetbrains-mono-*.woff` | https://github.com/JetBrains/JetBrainsMono; https://github.com/fontsource/font-files |

## elkjs and the Eclipse Public License 2.0

elkjs is offered under EPL-2.0 or GPL-3.0-or-later. Puffin uses it under **EPL-2.0**. In the
bundle it is minified, which EPL-2.0 treats as object code. Its source code is available at the
repositories above and in the npm package `elkjs@0.13.0`. elkjs is not modified; any
modification of it would have to be published under EPL-2.0. EPL-2.0 does not extend to
Puffin's own code, which stays MIT. Anyone redistributing Puffin's built UI receives elkjs under
the same EPL-2.0 terms.

Licence text: `puffin/web/static/licenses/elkjs-LICENSE.md`.

## Fonts

Inter and JetBrains Mono are distributed under the SIL Open Font License 1.1, which requires the
licence to travel with the font files. Licence texts: `puffin/web/static/licenses/inter-OFL.txt`
and `puffin/web/static/licenses/jetbrains-mono-OFL.txt`.

## Svelte

Svelte's runtime is compiled into the UI bundle. MIT licence text:
`puffin/web/static/licenses/svelte-LICENSE.md`.

Development-only tools (Vite, TypeScript, svelte-check, Playwright, pytest, ruff, mypy, uv) are
not part of what Puffin distributes and are not listed here.
