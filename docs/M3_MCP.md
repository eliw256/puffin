# M3: MCP server, design note

Status: approved 2026-11-25, with the resolutions in §9.

Tool names and output shapes are an API that models learn, so this note fixes them exactly. The
spec references are §6 (M3) and §8 (AI behavior contract).

Items first written as my own proposals (the review prompt was cut off partway through default c)
are marked **[proposed]**; §9 records how each was resolved.

## 1. Server and transport (default a: accepted, with two additions)

- **SDK:** the official MCP Python SDK, `mcp>=2.3.0,<3`; 2.3.0 was released 2026-10-02.
  - In 2.x the high-level class is `mcp.server.mcpserver.MCPServer`; `FastMCP` is gone.
  - I checked the 2.3.0 wheel. It supports `instructions`, tool `annotations`, structured output
    (`outputSchema`), resource templates and prompts.
  - It's a regular dependency, not an optional extra, so `uv sync` gives a working
    `puffin mcp`.
- **Entry point:** `puffin mcp [path]` serves one project over stdio.
  - The project is loaded with `checkers=[check_generation]`, the same as the CLI, so "check is
    the whole truth" holds over MCP too.
- **stdout belongs to the protocol.**
  - While serving, the SDK (2.3) writes the protocol to a private duplicate of fd 1 and points
    fd 1 itself at stderr. *As built:* that alone leaked. `sys.stdout` is block-buffered on a
    pipe, so a stray `print` sat in its buffer and was flushed at exit, after the SDK had
    restored fd 1 to the wire. `serve()` therefore also makes `sys.stdout` unbuffered, so every
    stray write reaches fd 1 at once, while it still points at stderr.
  - All logging goes to stderr.
  - *Test:* spawn the real server through a wrapper script that patches a tool to `print`, and
    tee its stdout. Drive it with the SDK's stdio client. Every captured stdout line must parse
    as JSON-RPC, and the stray text must not appear. The test fails without the unbuffered
    stdout. No production code carries a test hook.
- **Package location:** `puffin/mcp/`, as in the spec's repo layout. Absolute imports keep
  `import mcp` pointing at the SDK.
- *Addition:* **the project reloads when it changes on disk.** Before each tool call, the server
  compares the SHA-256 of `puffin.yaml` with what it last loaded. The engineer may edit the file,
  run the CLI, or run KiCad import in parallel. A tool never answers from a stale graph.
- *Addition:* **no network, and no paths outside the project.**
  - Doc and part lookups go through the part library roots.
  - `generate` uses the existing writer guards: the marker check and symlink escape.

## 2. Conventions every tool follows

- **Issue shape.** Everywhere, an issue is the existing `Issue.to_dict()`:
  `{"severity": "error"|"warning", "code": str, "where": str, "message": str, "hint": str}`.
- **Result envelope.** Every tool returns a JSON object (structured content plus a text copy).
  Its top level always has `"ok": bool`; tool-specific fields sit next to it. When `ok` is false,
  there's an `"errors": [Issue]` list.
- **When `isError` is set.** `isError: true` means the request could not be carried out: an
  unknown component, net, part, section, board, or proposal; a stale proposal; an unreadable
  project; or kicad-cli failing. The body is the same envelope, so the model gets a code and a
  hint.
  - A proposal that would *introduce errors* is **not** `isError`. The tool did its job, and
    the answer is "this edit is invalid: here is why". It returns `ok: false` with
    `introduced_errors`.
  - SDK argument-schema violations come back as the SDK's own `isError` results.
- **MCP error codes.** Codes MCP adds on top of the validation, generation and import codes:

  | code | meaning |
  |---|---|
  | `not-found` | unknown component, net, part, board, peripheral, signal or doc section; `where` names which |
  | `proposal-unknown` | no proposal with that id (never issued, already applied, or evicted) |
  | `stale-proposal` | `puffin.yaml` (or for imports, the schematic) changed since the proposal |
  | `project-unreadable` | `puffin.yaml` can't be loaded; the message is the `ProjectLoadError` text |
  | `path-escape` | a part, doc or output path resolves outside the project or part library |
  | `internal-error` | an unexpected exception; never a stack trace in the result |
  | `kicad-cli-failed` | export failed; the message is the `KiCadCliError` text |

- **Determinism.** Every list is sorted, or in file or part order where that order means
  something (pins in part-file order, nets in graph order). The same project gives byte-identical
  tool output.
- **Annotations.** Read tools set `readOnlyHint: true`. `apply_edit` and `generate` set
  `destructiveHint: false` and `idempotentHint: false`. No tool sets `openWorldHint`.

## 3. Tools

The 12 tools: 8 read, 2 propose, 2 write. In the JSON shapes below, `?` marks an optional field
and `[T]` an array.

### Read

**`get_project`**
- Input: `{board?: str}`.
- Output: `{ok, puffin_api: 1, project, schema, boards, components, nets, assignments,
  constraints, overrides, intent}`.
  - This is the `puffin show --json` view, filtered by `board`.
  - `puffin_api` is the version of this tool surface. It's bumped only on a breaking change.

**`query_pins`**: what each pin of one component is, and what it's doing.
- Input: `{component: str, pins?: [str], used_only?: bool = false}`.
- Output: `{ok, component, part, variant, pins: [Pin]}`, in part-file order.
- `Pin`:
  ```json
  {"pin": "GPIO0", "numbers": [27], "datasheet_name": "IO0", "type": "I/O/T",
   "rail": null, "functions": ["gpio", "..."],
   "flags": ["strapping"], "flags_source": {"strapping": "part"},
   "net": "BOOT_MAIN", "assignments": [{"peripheral": "gpio", "signal": "io.boot_button"}],
   "overrides": [{"allow": "strapping", "note": "boot button, intentional"}],
   "issues": [Issue]}
  ```
  - `flags` already includes the variant's restrictions (`effective_flags`).
  - `flags_source` says where each flag comes from: `part`, or `variant:N16R8`.
  - `issues` are the current validation issues whose `where` or endpoint is this pin.

**`query_net`**
- Input: `{net: str}`.
- Output:
  ```json
  {"ok": true, "net": "IMU_SCK",
   "endpoints": [{"endpoint": "U1.GPIO12", "component": "U1", "board": "main", "pin": "GPIO12",
                  "type": "I/O/T", "rail": null,
                  "assignments": [{"peripheral": "spi2", "signal": "sck"}]}],
   "boards": ["main"], "cross_board": false,
   "constraints": [{"max_freq_hz": 10000000}], "intent": null, "issues": [Issue]}
  ```

**`legal_pins`** (addition b: accepted): "where can SPI2 CS go?"
- Input: `{component: str, peripheral: str, signal: str}`. `signal` may carry a sub-name, such
  as `cs.imu`.
- Output:
  ```json
  {"ok": true, "component": "U1", "peripheral": "spi2", "signal": "cs.imu", "base": "cs",
   "routing": "gpio_matrix", "current_pin": "GPIO10",
   "candidates": [{"pin": "GPIO4", "status": "free", "flags": [], "needs_override": [],
                   "net": null, "assigned": []}]}
  ```
- `status` is one of:
  - `current`: holds this signal now.
  - `free`: legal, unflagged (or already overridden), no assignment, on no net.
  - `connected`: legal and unassigned, but already on a net *(added while building: moving a
    signal there would join two nets)*.
  - `in_use`: legal, but another signal is assigned there.
  - `needs_override`: legal, but flagged; `needs_override` lists the flags.
  - Flag exemptions from `flag_policy` are applied.
- Candidates come in part-file order. The list holds only pins that can carry the signal
  (`PartDefinition.legal_pins`), so the answer is data, not reasoning over pin tables.
- An unknown peripheral or signal gives `not-found`, with the known names in the hint.

**`get_part`**
- Input: `{part: str}`, for example `"espressif/esp32-s3-wroom-1"`.
- Output: `{ok, part: <PartDefinition as JSON>, has_datasheet: bool, sources: [str],
  doc_sections: [section_id]}`. `has_datasheet` is false when the part has no extracted docs
  and no `docs.datasheet`, as for the placeholder `example/spi-imu`, so a model can say "no
  datasheet" from data instead of guessing.

**`search_docs`**
- Input: `{query: str, part?: str, limit?: int = 8}`.
- Output: `{ok, results: [{part, doc, section_id, heading, snippet, score}]}`. `doc` is the doc's
  path relative to its part library root, e.g. `espressif/docs/esp32-s3-wroom-1.md`.
- **How it searches [proposed]:**
  - The corpus is every `docs/*.md` in the part library roots: project-local parts, then
    bundled. Each file splits into sections at `##` headings.
  - Ranking is deterministic term matching: case-insensitive tokens, with each query term
    weighted by inverse section frequency. Ties are broken by `section_id`.
  - There are no embeddings and no network.
  - `snippet` is the best-matching line, up to 200 characters.

**`read_doc_section`**
- Input: `{section_id: str}`.
- Output: `{ok, part, doc, section_id, heading, sources, text, citation}`.
- `section_id` is `<vendor>/<doc>#<slug>`, for example
  `espressif/esp32-s3-wroom-1#strapping-pins-table-4-1`.
- `sources` is the part's `sources` list (the datasheets the doc was extracted from).
- `citation` is what the contract tells the model to cite: part id plus section id, for example
  `"espressif/esp32-s3-wroom-1 #strapping-pins-table-4-1"`.

**`check`**
- Input: `{}`.
- Output: `{ok, errors: int, warnings: int, issues: [Issue]}`, the same as `puffin check --json`,
  including drift warnings.

### Propose and apply (default c, completed)

**`propose_edit`**
- Input: `{ops: [Op]}`. These are the same serializable ops as `Project.batch`; see §4.
- Output:
  ```json
  {"ok": true, "proposal_id": "p_3f9c2a1b7e04", "summary": ["move U1 spi2.cs.imu GPIO10 -> GPIO4", "..."],
   "diff": "--- puffin.yaml\n+++ puffin.yaml\n@@ ...", "introduced_errors": [], "warnings": [Issue],
   "generated": [{"board": "main", "path": "fw/main/main/include/puffin_pins.h", "diff": "..."}]}
  ```
- `diff` is a unified diff of `puffin.yaml`.
- `summary` has one plain line per op.
- `generated` previews the header diff each board would get [proposed]. It lets the engineer
  see the firmware effect before applying, and it's nearly free because generation is pure.
- When the batch would introduce errors: `ok: false`, the `introduced_errors` are listed, and
  there's still a `proposal_id: null` and a `diff`, so the model can show what it tried.
- **What a proposal records [proposed]:**
  - the ops, the SHA-256 of the `puffin.yaml` bytes it was computed against, and the resulting
    bytes.
  - The id is `p_` plus the first 12 hex characters of
    `sha256(base_sha256 + canonical JSON of ops)`, so the same request gives the same id.
  - Proposals live in server memory only. They're single-use, with at most 64 kept and the
    oldest evicted first.
  - A proposal isn't persisted. A restart means propose again, which costs nothing.

**`apply_edit`**
- Input: `{proposal_id: str}`. Only the id: the model can't change ops between showing the diff
  and applying them.
- Output: `{ok, applied: bool, changed_artifacts: ["puffin.yaml"], warnings: [Issue]}`.
- On apply, the server checks that `puffin.yaml` still has `base_sha256`; otherwise
  `stale-proposal`.
- It then re-runs the batch through `Project.batch` rather than writing the cached bytes, so
  validation runs at apply time too, and saves atomically.
- It does **not** regenerate. `generate` stays a separate, explicit step, matching the spec's
  acceptance flow: `propose_edit`, then `apply_edit`, then `generate`.

**`propose_import_kicad` [proposed]**: KiCad import through the same proposal path.
- Input: `{}`. kicad-cli runs for each schematic.
- Output: `{ok, proposal_id, in_sync, schematics: [ChangeSet.to_dict()], introduced_errors,
  diff}`.
- It's applied with `apply_edit`, so there's one apply path, and the contract rule "propose
  before apply" covers imports too.
- The proposal also records each schematic's hash.
  - `apply_edit` refuses with `stale-proposal` if the schematic changed in between.
  - On success it records sync state, both hashes, exactly as the CLI does for a kicad-cli
    import.
- An in-sync result still returns a proposal with zero ops. Applying it records the sync, so
  the propose call itself stays read-only.
- **No `netlist` parameter.** Over MCP, every import is a real kicad-cli sync, so drift state
  stays honest. The spec's `import_kicad` is renamed `propose_import_kicad` to make the
  two-step flow explicit.

### Write

**`generate`**
- Input: `{board?: str, check_only?: bool = false}`.
- Output: `{ok, files: [{board, path, action: "create"|"update"|"unchanged"|"blocked",
  lines_added, lines_removed, reason?}], issues: [Issue]}`.
- It writes only marked, generated files, through the existing writer.
- It isn't proposal-gated [proposed]: the output is derived and deterministic, and it's already
  previewed in `propose_edit.generated`.
- `check_only` is `puffin gen --check`.
- It refuses, with `ok: false` and the issues, while the graph has errors.

## 4. The `Op` schema

`propose_edit.ops` is a list of objects, each with an `"op"` discriminator. The input schema is a
`oneOf` over these, generated from `OPERATIONS`, so the model sees every operation and its
arguments:

| op | arguments |
|---|---|
| `add_component` | `ref, part, board, variant?` |
| `set_component` | `ref, part, board, variant?` (create, or update in place) |
| `remove_component` | `ref` (rejected while anything still references it) |
| `connect` | `net, endpoints: [str]` |
| `disconnect` | `net, endpoints: [str]` |
| `set_net` | `name, endpoints: [str]` |
| `rename_net` | `old, new` (constraints and an intent entry follow) |
| `remove_net` | `name` |
| `assign_pin` | `component, peripheral, signal, pin` |
| `unassign_pin` | `component, peripheral, signal` |
| `set_assignment_pin` | `component, peripheral, signal, pin` (assignment only, nets untouched) |
| `move_pin` | `component, from_pin, to_pin` (nets and assignments together) |
| `add_override` | `pin, allow, note` |
| `remove_override` | `pin, allow` |

For example, moving the IMU CS: `[{"op": "move_pin", "component": "U1", "from_pin": "GPIO10",
"to_pin": "GPIO4"}]`.

## 5. Resources

| URI | content |
|---|---|
| `puffin://project` | `puffin.yaml` as it is on disk (`application/yaml`) |
| `puffin://parts/{vendor}/{part}` | a part definition YAML; the listing covers parts the project uses plus bundled parts |
| `puffin://docs/{vendor}/{doc}` | an extracted doc (`text/markdown`) |
| `puffin://generated/{board}` | that board's header as `generate` would write it, read-only [proposed] |
| `puffin://contract` | the AI behavior contract (§7 below) |

Resources are for clients that like to attach context. Every fact in them is also reachable
through a tool, because many clients never read resources.

## 6. Prompts

- **`puffin-contract`** (no arguments): the contract and the workflow from §7.
- **`review-kicad-import`** (no arguments) [proposed]: run `propose_import_kicad`, then present
  the change set section by section. Ambiguous fixes and introduced errors are for the engineer
  to decide. Apply only on their word.

Prompts are optional in most clients, so the contract is *also* sent as the server's
`instructions` in initialize. That's the one channel every client receives.

## 7. The bundled contract (sent as `instructions`)

> You are a contractor working from the engineer's design in Puffin. `puffin.yaml` is the
> single source of truth.
> 1. Ask when intent is ambiguous. Don't redesign unasked.
> 2. Never edit generated files (they start with `AUTO-GENERATED by Puffin`) or `puffin.yaml`
>    directly. Change the graph only with `propose_edit`, then `apply_edit`. Then run
>    `generate`.
> 3. Always show the engineer the `propose_edit` diff, and get their go-ahead, before
>    `apply_edit`.
> 4. Use `legal_pins` and `query_pins` for pin choices. Don't infer pin functions yourself.
> 5. Never claim an electrical fact (voltage, timing, pin function) without a doc citation
>    (part id + section id from `read_doc_section`) or project data (a tool result). When the
>    docs don't say, say "the docs don't say". A constraint in `puffin.yaml` is the engineer's
>    design limit, not a datasheet fact; say which one you're quoting.
> 6. Flagged pins need an override with a note, recorded with the engineer's agreement.
>    Never add one silently.
> 7. KiCad decides components and nets. Make connectivity changes in KiCad, then
>    `propose_import_kicad`. Graph-side net edits are overwritten by the next import.
> 8. Treat hardware-in-the-loop observations as stronger evidence than your assumptions.

## 8. Testing

- **In-process tests** use the SDK's in-memory client and server pair. They check the schema of
  every tool's output, the error envelope and `isError` for each MCP code, determinism (two
  identical calls give identical JSON), stale proposals after an external write, and proposal
  ids being single-use.
- **Acceptance**, scripted through the in-memory client:
  - Q&A: `query_pins`, `query_net` and `search_docs` give the IMU's pins and its SCK
    constraint (but see open question 1).
  - Move IMU CS: `legal_pins`, then `propose_edit`, `apply_edit`, `generate`. `check` is clean,
    and the header changes exactly the `PUFFIN_SPI2_CS_IMU` line.
- **stdio subprocess test:** see §1.
- **Live check:** a short manual run with Claude Code connected via `claude mcp add`. I'll
  record the transcript summary, but it isn't a CI test.

## 9. Resolutions (review, 2026-11-25)

1. **Stale proposals:** the code is `stale-proposal`; the model must re-propose. Proposals hash the
   project file's content and expire on server restart. Imports use the same propose/apply flow.
   No tool writes arbitrary text or edits generated files (`generate` writes only through the
   generator and writer guards).
2. **External edits:** the server reloads `puffin.yaml` when it changes on disk; every response
   reflects the current file.
3. **Outputs:** structured JSON; issues via `Issue.to_dict` with stable codes; errors are tool
   results with a code and hint, never stack traces (an unexpected exception becomes
   `internal-error` with the exception type and message, logged in full to stderr).
4. **Docs:** keyword search only, results carry part id, doc path, section id, heading, snippet;
   citations are part id + section id.
5. **Confinement:** every path resolves inside the project directory or a part library root;
   anything else is refused with `path-escape`. This covers doc and part lookups (ids such as
   `../../etc/passwd` or symlinks out of a root), generated outputs, and schematics.
6. **History:** every applied proposal appends one line to `.puffin/history.jsonl`:
   `{"time": ISO-8601 UTC, "tool": "apply_edit", "proposal_id", "kind": "edit"|"import", "ops",
   "summary", "sha256": resulting puffin.yaml hash}`. **Recommendation: do not commit it**
   (gitignore it). Git already records every change to `puffin.yaml` with author and time; the
   history file's value is the *local* trail of what the AI did between commits. Committed, an
   append-only file conflicts on every merge of two branches that both applied edits, and its
   timestamps make otherwise identical work differ. `state.json` is different: it must be
   committed because it describes the checked-in schematic. If you want a durable record of AI
   edits, a commit-message trailer per applied proposal is the better vehicle; that is out of
   scope for M3.
7. **Prompts:** the §7 contract ships as the `puffin-contract` prompt and as `instructions`,
   including "never claim an electrical fact without a doc citation or project data; say the
   docs don't say when they don't".
8. **IMU acceptance:** the placeholder stays. The expected answer is pins from the project,
   10 MHz from the project constraint, and an explicit "the IMU part has no datasheet
   (placeholder)". `get_part` exposes `has_datasheet: false` so this comes from data.
9. **Accepted as proposed:** `generate` without a proposal; no `netlist` parameter over MCP;
   `generated` previews in `propose_edit`; deterministic proposal ids.
