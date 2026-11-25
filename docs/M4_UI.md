# M4: Web UI, design note

Status: approved 2026-11-25, with the resolutions at the end.

Sources: PUFFIN_SPEC.md §6 (M4) and §7 (visual identity), DECISIONS 11.1 (Tauri shell), and the
M3 and write-back notes.

The prompt also points to `docs/BACKLOG.md`. That file doesn't exist: not on this branch, not on
`main`, not anywhere in git history. Its write-back UX item is described in the prompt (the
"Puffin only / Puffin + schematic" choice), and this note follows that description. If the
backlog has more for M4, I need it.

## a. Staging: accepted

`puffin ui [path] [--port N] [--no-browser]` starts a local server and opens the browser. Tauri
comes later, as its own step, and nothing in the UI may depend on it:
- The frontend gets its API base URL and token at startup. Tauri can later supply the same two
  values.
- The frontend uses only standard web APIs: `fetch`, streams, SVG, pointer events.
- The built frontend is plain static files.

## b. One service layer: accepted, with three additions

The HTTP API is a thin layer over `PuffinService`, the same one MCP uses. Every route calls a
service method and returns its dict unchanged: the same `ok` envelope, issue codes, proposal ids,
stale checks and write-back. No validation, diffing or file writing lives in the UI backend.
Things the UI needs that don't exist yet are added **to the service**, so MCP could expose them
later:
- **`symbol_view(component)`:** the drawable symbol (§e), built from the schematic parser
  write-back already uses.
- **`history(limit)` and `propose_undo()`** (§h).
- **`issue_target(where)`:** turns an issue's `where` (`U1.GPIO0`, `nets.IMU_SCK`,
  `assignments.U1.spi2.sck`, `components.U3`, `kicad_schematic …`) into
  `{kind, component, pin, net}`, so a click on a diagnostic can find its target (§g).

`writeback confirm` and `writeback restore` become service methods too. The UI calls them; MCP
still doesn't register them, because confirming is the engineer's job, and in the UI the
engineer is the one clicking.

**Concurrency.**
- The UI server and an MCP server are separate processes with separate proposal stores. What
  they share is the files on disk, and the existing hash checks already handle that: an
  `apply_edit` against a `puffin.yaml` (or schematic) that changed since the proposal fails with
  `stale-proposal`.
- Inside the UI server, service calls are serialized with a lock. `PuffinService` was written
  for one caller at a time.
- **How the UI shows a stale proposal:** when a file-change event (§c) arrives while the review
  panel is open, the UI compares the proposal's base hash with the new one. If they differ, the
  panel turns stale before Apply is clicked: "puffin.yaml changed (by another editor or an AI
  session); this proposal no longer applies", with a "Propose again" button that re-runs the same
  drag on the new graph. If the apply call itself returns `stale-proposal` (a race), the panel
  shows the same thing.

## c. Live updates: accepted, with these specifics

- **SSE over a streaming `fetch`, not WebSocket.** Updates only flow server to client; SSE is
  plain HTTP, and reconnecting is simple. I'm not using `EventSource` because it can't send
  an `Authorization` header (§i); a 30-line stream reader replaces it.
- **Polling the files, not a file-watching library.** Every 0.5 s the server stats `puffin.yaml`,
  each schematic (including sub-sheets), `state.json` and the write-back checkpoint, and hashes a
  file only when its size or mtime changed.
  - That's two files plus a few small ones, so the cost is negligible.
  - There's no new dependency, it works on every OS, and it isn't fooled by KiCad and Puffin
    saving through atomic renames.
  - A change is reported once it has been stable for one poll.
- **Events are small** (`{"type": "project"|"schematic"|"state"|"checkpoint", "sha256": …}`).
  The UI refetches what depends on the changed file: the project, `check` and the pin map on
  `project`; drift and `check` on `schematic`.
- **Schematic drift** shows a banner, "the schematic changed since the last import", with an
  **Import from KiCad** button. It runs `propose_import_kicad` and opens the normal review
  panel: change set sections, ambiguous fixes, errors, and Apply only on confirm.

## d. Frontend: Svelte 5 + Vite + TypeScript

**Recommendation: Svelte.** Your criterion was the smallest, clearest code:
- A Svelte component is HTML with `{expressions}` plus a small script. A pin map is mostly SVG
  markup, and Svelte writes it as markup.
- Reactive state (`$state`, `$derived`) needs no hooks, memoisation or state library. That's
  where React apps grow most of their extra code.
- There's no virtual DOM, so the shipped JS is small. That helps a future Tauri shell too.
- It has scoped CSS per component, which suits a palette of CSS custom properties.

**The cost, honestly:**
- React has the bigger ecosystem, and AI models write it more fluently. Svelte 5's "runes"
  syntax arrived in late 2024, so models sometimes produce Svelte 4 code.
- I'll mitigate that by writing in one consistent Svelte 5 style. The UI is small, too: about
  ten components.

**Other choices:**
- No component library. Plain CSS with the palette as variables.
- **The server is Starlette + uvicorn,** not FastAPI. Both are already installed (they're
  dependencies of the MCP SDK); I'll declare them directly. FastAPI's schema generation adds
  little when every route returns a service dict.
- **The built frontend is committed** in `puffin/web/static/`, so `uv sync` plus `puffin ui`
  works without Node. Node is needed only to change the UI (`npm run build` in `web/`). A test
  fails if the committed bundle's recorded source hash doesn't match `web/src`, so a stale
  bundle can't ship.

## e. Pin map: accepted, with four specifics

- **Symbol drawing.**
  - The MCU is drawn from the schematic's embedded `lib_symbols` and the instance transform: the
    same code write-back uses, so it matches KiCad.
  - It draws the primitives KiCad 9 symbols use: rectangle, polyline, circle, arc, text and pins
    (line, name and number, using the lib's name and number offsets).
  - Any other primitive is skipped, with a note in the view. For components without a schematic
    there's a generated box with pins in part-file order, split left and right.
  - SVG, scaled to fit, with zoom and pan.
- **What colour means.**
  - A pin's colour shows **what it does in this design**: its assignment's function family.
    What it *could* do appears in the detail panel and during a drag. Power and ground come from
    `rail`. Unassigned pins are neutral, and no-connect pins are dimmed with a ×.
  - Families: GPIO, SPI, I2C, UART, ADC, USB/JTAG, power, ground.
  - Hues are derived from the §7 blues, yellows and creams, at equal perceived lightness
    (OKLCH), and checked for WCAG AA. Every pin label sits on a background it reaches 4.5:1
    against.
  - **No function uses red or orange.** Those mean error and warning.
- **Never colour alone.**
  - Every pin shows its name.
  - Flagged pins get a diagonal hatch plus a flag glyph, so they read in greyscale.
  - Pins with a current error get a red outline *and* an error glyph.
- **Detail panel** (on click):
  - net, assignments, flags with where each comes from (part, or `variant:N16R8`), overrides
    with notes, and current issues;
  - the datasheet section found with `search_docs` (pin's datasheet name, within that part),
    with its citation (`part #section`);
  - "No datasheet for this part" when `has_datasheet` is false, never a guess.
- **Dragging.**
  - Dragging a pin's assignment calls `legal_pins` once at drag start. Every pin then shows its
    status: current, free, connected, in use, needs override, or illegal (not a candidate), by
    outline style and an icon, not colour alone.
  - Dropping creates **`move_pin`** (the net and every assignment on that pin move together).
    The review panel names it: "Move U1.GPIO10 → GPIO4 (net IMU_CS; spi2.cs.imu)".
  - Dropping on an illegal pin opens nothing. It shows the reason inline ("GPIO19 can't carry
    spi2.cs: legal pins are …"), and the drop is refused.
  - **Keyboard alternative** (dragging isn't accessible): select a pin, press `M` (or click
    "Move…"), and a list of legal targets appears with the same statuses.
- **Review panel.** It shows the YAML diff, the header diff, and the warnings and errors.
  - When the proposal carries `kicad-overwrite`, there's a required choice and **neither option
    is preselected**:
    - *Puffin only:* the next import reverts it.
    - *Puffin + schematic:* write-back, verified with kicad-cli.
  - Choosing write-back re-proposes with `writeback: true`, showing a spinner while kicad-cli
    runs, then adds the schematic diff. If write-back doesn't qualify, that option is disabled
    with the refusal reason (from the warning hint).
  - Apply stays disabled until a choice is made. Nothing applies without a click on Apply.

## f. Block diagram: accepted, with elkjs

- **elkjs over dagre.** ELK handles **compound nodes** (each board drawn as a container holding
  its components, so cross-board links visibly cross a boundary), ports on node sides, and
  orthogonal edge routing. dagre's compound support is weak, and its original package is
  unmaintained. ELK's JS is larger (about 1.4 MB). It runs in a web worker so the UI never
  freezes, and gives the same layout for the same input.
- **Edges are built in the service, not the UI** (`block_view()`):
  - Nets touching an MCU pin assigned to a peripheral are grouped by (MCU, peripheral, other
    component) into one edge, labelled with the peripheral and count: `SPI2 ×4`.
  - UART links between boards become one `UART1` edge between U1 and U2, crossing the board
    boundary.
  - Power and ground nets are hidden by default, behind a "show rails" toggle.
  - Unassigned nets become single edges named after the net.
- **Read-only.** Click to select. Selection syncs both ways with the pin map: selecting an MCU
  switches the pin map to it, and selecting an edge highlights its pins.

## g. Diagnostics: accepted

- **Content:** live `check` results, including drift and generator checks, grouped errors first,
  then warnings, refreshed on every file event.
- **Navigation:** clicking an issue uses `issue_target` to select the pin, net or component and
  scroll it into view.
- **`writeback-unconfirmed`** shows the pending count, a reminder ("open the schematic in KiCad
  and look at the moved labels first"), and **Confirm** and **Restore all N** buttons.
  - Restore asks a second time, because it undoes N write-backs.
  - Both call the service methods, then refresh through the file events.

## h. Undo: accepted, with inverse ops recorded at apply time

You can't always rebuild an inverse from the ops alone: `set_net` and `remove_net` lose the old
endpoints, and `set_component` loses the old fields. So:
- **At apply time,** the service computes each op's inverse against the *before* model and
  stores it in the history entry as `"undo": [ops]`. A batch's inverse is its inverses in
  reverse order.
- **The inverses:**
  - `move_pin` reverses itself; `rename_net` renames back.
  - `assign_pin` ↔ `unassign_pin`; `add_override` ↔ `remove_override` (with the old note).
  - `connect` ↔ `disconnect`.
  - `set_net` and `set_assignment_pin` go back to the old value (or to `remove_net`).
  - `add_component` ↔ `remove_component` (with the old fields).
  - `set_component` goes back to the old fields.
- **The Undo button** offers the most recent entry: `propose_undo()` gives a normal
  `propose_edit` of its `undo` ops. The same review panel follows: diffs, checks, the write-back
  choice (undoing a write-back move qualifies again, as a move back), and confirm.
  - If `puffin.yaml` changed since that entry (the hash differs), the panel says so; validation
    decides whether it still applies.
  - It never reverts a raw file.
- **The history list** shows the last 10 entries from `history.jsonl`: time, summary, kind.
  Entries written before M4 have no `undo` and show "can't undo (recorded before undo
  support)".

## i. Security: accepted, with every item tested

- **Binding:** 127.0.0.1 only, on a random free port unless `--port` is given. It refuses to
  start if asked to bind anything else.
- **Token:** 32 random bytes, URL-safe, generated per run. The CLI opens
  `http://127.0.0.1:PORT/#token=…`. The token is in the **URL fragment**: the browser never sends
  a fragment to the server, so it can't end up in logs, Referer headers or history-sync servers.
  The page reads it, removes it from the address bar, and sends it as
  `Authorization: Bearer …` on every API call, including the event stream.
- **What the token protects:** every `/api/*` request without the right token gets 401, compared
  in constant time. The static assets carry no project data and are served without it.
- **Host check** on every request: the Host must be `127.0.0.1:PORT` or `localhost:PORT`.
  Otherwise 421. That blocks DNS rebinding.
- **Origin check.** A request carrying `Origin` must come from `http://127.0.0.1:PORT` or
  `http://localhost:PORT`. Every non-GET API request must carry it. A `Sec-Fetch-Site:
  cross-site` header is refused too.
- **No CORS:** no `Access-Control-*` headers ever, and `OPTIONS` returns 405.
- **Headers:** `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'` (no inline
  script), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.
- **Tests:** one each for no token, wrong token, wrong Host (rebinding), foreign Origin,
  cross-site fetch, a POST without Origin, preflight, the bind address, the headers present, and
  the token being absent from server logs.

## j. Visual identity: accepted

- **Dark theme by default,** with the §7 palette as CSS custom properties. Red and orange are
  for errors and warnings only.
- **Fonts:** Inter for UI text and JetBrains Mono for pins, nets and code. Both are OFL-licensed,
  bundled through `@fontsource` at build time and shipped in the static files with their
  licenses. No CDN, no runtime network.
- **Dithering:** a small Floyd–Steinberg function dithers the splash and empty-state
  illustrations into the palette. It's never used on pin labels, diagrams or diffs.

## Step 2 prototype (planned)

The prototype is built in the real stack (Svelte), so once approved it becomes the code, not a
throwaway.
- **Data:** a snapshot JSON exported from the frozen fixture by a small Python script:
  - U1's real symbol geometry, pins and assignments;
  - `legal_pins` for `spi2 cs.imu`;
  - one real `propose_edit` result for GPIO10 → GPIO4, with and without write-back, including
    both diffs.
- **States shown:** colours and labels, GPIO0 selected with its detail panel open, a drag in
  progress showing legal-pin states, and the review panel with the write-back choice.
- **Command:** `cd web && npm install && npm run prototype`, which opens it in your browser.
  No backend runs.

## Tests (planned)

- **API (pytest):** every route against fixture copies; the envelope and codes match the service
  exactly; and every security item in §i.
- **End to end (pytest-playwright, Python):** so all tests stay in pytest. The browser binary
  is downloaded once at setup (`playwright install chromium`); without it these tests are
  skipped, with a count. The flows:
  - drag, review and apply, Puffin only, with the header updated;
  - the same with write-back (needs kicad-cli);
  - an illegal drop refused;
  - an external edit appearing live;
  - a stale proposal after a concurrent edit through a separate `PuffinService` (standing in
    for an MCP process), handled with "Propose again".
- **Not yet:** pixel snapshots.

## Resolutions (review, 2026-11-25)

1. **Svelte 5, with a mechanical guard against Svelte 4 syntax.**
   - Components compile in forced runes mode, so `export let` and `$:` are compile errors.
   - `svelte-check --fail-on-warnings` makes the deprecated `on:` directive fail.
   - A lint script (`web/scripts/check-svelte5.mjs`) fails on all three patterns explicitly.
   - A pytest test runs both. It's skipped, with the reason, when Node or `web/node_modules` is
     missing.
2. **The built bundle is committed,** with the stale-bundle hash test, and `puffin/web/static/`
   is marked `linguist-generated` in `.gitattributes`.
3. **Drag means `move_pin`.** A pin carrying two assignments is already a validation error
   (`pin-double-assigned`), so on a valid graph a drag moves exactly one assignment plus its net.
4. **Undo** covers entries applied from M4 on. Older history entries show "can't undo".
5. **`docs/BACKLOG.md` is created.** Nothing in it is M4 scope except the write-back choice UX,
   which §e covers.
