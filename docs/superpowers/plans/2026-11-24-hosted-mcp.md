# Hosted, read/write mcp-mealie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve this MCP server over streamable HTTP with labelled tools and an `update_recipe` that no longer destroys ingredient and step data.

**Architecture:** Keep the stdio entry point; add an env-selected HTTP transport in `server.py`. Fix `update_recipe` by aligning incoming list items to the existing raw items (pure helper in `shape.py`) and reusing raw objects for unchanged ones. Annotate tools from one table applied in the existing `SendResultsOnce` middleware, so 43 decorators stay untouched.

**Tech Stack:** Python 3.11-3.13, FastMCP >=4 (lock 4.1.6), httpx, respx, pytest, ruff, mypy, Docker.

**Spec:** `docs/superpowers/specs/2026-11-24-hosted-mcp-design.md` (approved). Executors read it too.

## Global Constraints

- AGENTS.md invariants 1-10 hold (writes never retried; read-only mode does not register write tools; no raw Mealie JSON to callers; no output schemas / `structuredContent`; `check_segment()` on interpolated IDs; stderr-only logging, no credentials in logs; one shared semaphore).
- Tool count stays **43**; `READ_TOOLS` / `WRITE_TOOLS` in `tests/test_tools.py` are unchanged.
- Fast gate must pass before any task is called done: `uv run --extra dev ruff check . && uv run --extra dev ruff format --check . && uv run --extra dev mypy && uv run --extra dev pytest`.
- No new dependency. Unit tests offline.
- `CHANGELOG.md` `## [Unreleased]` entry; `tests/test_docs.py` must agree with README/config changes. Never relax it.
- Public repo: no household names, hostnames, tokens, or `HANDOFF.local.md` content in any committed file.
- Commit only when the owner asks. Each "Checkpoint" step means: stage the files; commit only if the owner has said to. Commit messages end with the attribution line the session provides.
- **Approval gates (ask the owner first):** `uv sync` (installs from network), running the fast gate / pytest, `docker build`/`run`, `scripts/integration.sh`. Nothing below runs until approved.

## Review Focus

1. Reordered ingredient list containing two identical lines: each existing item is reused once, none duplicated or dropped (Task 2).
2. Edited ingredient whose food Mealie cannot resolve: other items keep IDs; the edited line is re-parsed, not lost (Task 3).
3. Update on a recipe that has no existing items, or sends an empty list to an empty recipe: the mass-loss guard must not fire (Task 3).
4. `MEALIE_READ_ONLY=true` together with HTTP transport: write tools and the two path-reading tools are all absent; stdio keeps the path tools (Task 5).
5. Request with an unexpected `Host`/`Origin` header, and `/health` output: rejected or allowlisted per Task 1's finding; `/health` never contains the token or URL (Task 5).

---

### Task 1: Confirm FastMCP 4 APIs (research, no product code)

**Files:** Create `docs/superpowers/plans/fastmcp-notes.local.md` (git-excluded, scratch; add to `.git/info/exclude`).

**Interfaces:** Produces answers Tasks 4-5 depend on.

- [ ] **Step 1: Get approval, then `uv sync --extra dev`.**
- [ ] **Step 2: In the installed `fastmcp` source, answer in the notes file, with file/line:** (a) how to run streamable HTTP with host, port, path `/mcp` (`mcp.run(transport=..., host=, port=, path=)`?); (b) how to add a plain GET route (`custom_route`?); (c) whether the HTTP app validates `Host`/`Origin` and how to configure an allowlist; (d) the field and type for tool annotations on a `Tool` object (`annotations: ToolAnnotations`?) and whether `model_copy(update=...)` in `on_list_tools` carries it to clients; (e) how to remove or skip registering a tool.
- [ ] **Step 3: Update Tasks 4-5 below if any answer differs from what they assume.** Expected: no code change, notes file only.

### Task 2: Pure alignment helper in `shape.py`

**Files:** Modify `src/mealie_mcp/shape.py`; Test `tests/test_shape.py`.

**Interfaces:**
- Produces: `align_existing(incoming: list[Any], existing: list[dict], display: Callable[[dict], str]) -> list[int | None]`. For each incoming item returns the index into `existing` to reuse, or `None`. Non-string incoming items (dicts) always return `None`.

- [ ] **Step 1: Write failing tests** (`tests/test_shape.py`), asserting:
  - `test_align_same_position_reuses` : incoming equal to existing displays -> `[0,1,2]`.
  - `test_align_reordered` : swapped two lines -> `[1,0,2]`.
  - `test_align_duplicates_use_each_existing_once` : existing displays `["a","a","b"]`, incoming `["a","a","a"]` -> `[0,1,None]`.
  - `test_align_edited_line_is_none` : one changed string -> `None` at that position, neighbours matched.
  - `test_align_dict_items_never_reused`.
  - `test_align_ingredient_and_instruction_display`: with `display=shape.ingredient` on `tests/fixtures` recipe ingredients and `display=shape.instruction` on its steps, round-tripping `recipe_detail(...)` output aligns every item (`[0..n-1]`). Use an existing fixture; add one recipe with a titled step and an unresolved-food ingredient if none has them.
- [ ] **Step 2: Run `uv run --extra dev pytest tests/test_shape.py -k align` (after approval); expect FAIL (undefined).**
- [ ] **Step 3: Implement `align_existing`.** Same-position match first (display equal and index unused), then first unused existing with equal display; no I/O.
- [ ] **Step 4: Run the same command; expect PASS.**
- [ ] **Step 5: Checkpoint:** stage `shape.py`, `tests/test_shape.py`, any new fixture.

### Task 3: Lossless `update_recipe` with mass-loss guard

**Files:** Modify `src/mealie_mcp/tools/recipes.py` (`update_recipe`, `_ingredient_payload`, `_instruction_payload` callers); Test `tests/test_tools.py`.

**Interfaces:**
- Consumes: `shape.align_existing`, `shape.ingredient`, `shape.instruction`.
- Produces: `update_recipe(..., confirm_replace_all: bool = False)`; result gains `"lists": {"ingredients": {"kept": int, "rewritten": int}, "instructions": {...}}` only for lists that were passed.
- New private helpers in `recipes.py`: `_merge_list(incoming, existing, display, rewrite) -> tuple[list[dict], int, int]` returning the final payload list plus kept/rewritten counts; `_check_mass_loss(kind: str, existing_count: int, kept: int, confirm: bool) -> None` raising `ToolError`.

- [ ] **Step 1: Write failing tests** through an in-process `Client` with `respx` (copy the pattern of existing `update_recipe` tests), asserting on the PATCH body:
  - `test_update_ingredients_reuses_unchanged_items_verbatim`: recipe with 3 ingredients carrying `referenceId`, `food.id`, `unit.id`; caller sends the 3 display strings with the middle edited. PATCH `recipeIngredient[0]` and `[2]` equal the raw originals; parser called for exactly one line; result `lists.ingredients == {"kept": 2, "rewritten": 1}`.
  - `test_update_instructions_keeps_titles_and_references`: titled step with `ingredientReferences`; unchanged string -> raw step (title, id, references) preserved; changed string -> `{"title":"","text":...,"ingredientReferences":[]}`.
  - `test_update_edited_unresolved_food_goes_through_parser`: parser returns a food without id; item lands as note text, siblings untouched (Review Focus 2).
  - `test_update_reordered_duplicates` (Review Focus 1) at tool level.
  - `test_guard_refuses_emptying_a_list`: existing 5, send `[]` -> `ToolError` naming "5"; no PATCH made.
  - `test_guard_refuses_dropping_more_than_half_of_four_or_more`: existing 4, send 1 kept -> refused; existing 3, send 1 -> allowed.
  - `test_guard_bypassed_with_confirm_replace_all`.
  - `test_guard_not_fired_on_empty_recipe` (Review Focus 3): existing `[]`, send `[]` or new lines -> succeeds.
- [ ] **Step 2: Run `pytest tests/test_tools.py -k "update_ingredients or update_instructions or update_edited or reordered or guard"`; expect FAIL.**
- [ ] **Step 3: Implement.** In `update_recipe`, when `ingredients` / `instructions` is passed: `_merge_list` uses `align_existing` against the already-fetched `current` raw lists; matched items are emitted unchanged, unmatched go through `_ingredient_payload` (one parser call for the batch) / `_instruction_payload`. Guard applies to ingredients and instructions only; "drop" means existing items not reused. Writes are not retried (unchanged path).
- [ ] **Step 4: Update the `update_recipe` docstring in one added line: an edited line loses its step links and ingredient IDs; unchanged lines are preserved; mention `confirm_replace_all`.** Keep terse (AGENTS.md token rule).
- [ ] **Step 5: Run the Step 2 command; expect PASS, then the full `tests/test_tools.py`.**
- [ ] **Step 6: Checkpoint:** stage `recipes.py`, `tests/test_tools.py`.

### Task 4: Tool annotations from one table

**Files:** Create `src/mealie_mcp/annotations.py`; Modify `src/mealie_mcp/server.py` (`SendResultsOnce.on_list_tools`); Test `tests/test_tools.py`.

**Interfaces:**
- Produces: `TOOL_HINTS: dict[str, dict[str, bool]]` mapping every tool name (all 43) to its `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`. `hints_for(name: str) -> dict[str, bool]` raising `KeyError` on unknown.

Rules for the table: reads `readOnlyHint=True`; creates/updates/imports `destructiveHint=False`; `update_recipe`, `update_*`, `set_recipe_image`, `upload_recipe_image`, `bulk_tag_recipes` and every `delete_*` `destructiveHint=True`; `idempotentHint=True` only for set-style updates (not creates, adds, imports, `random_meal_plan`, `mark_recipe_made`); `openWorldHint=True` for `import_recipe_from_url`, `set_recipe_image`, `check_recipe_links`. `manage_taxonomy`: `readOnlyHint=False, destructiveHint=True` in write mode, `readOnlyHint=True` in read-only mode (middleware needs the `read_only` flag, so `SendResultsOnce.__init__(self, read_only: bool = False)`).

- [ ] **Step 1: Write failing tests:**
  - `test_every_tool_has_annotations`: for write and read-only servers, every listed tool has non-null `annotations`.
  - `test_table_covers_exactly_the_tool_names`: `set(TOOL_HINTS) == READ_TOOLS | WRITE_TOOLS`.
  - `test_read_tools_are_read_only_and_write_tools_are_not` (except `manage_taxonomy` per mode).
  - `test_deletes_are_destructive`: names starting `delete_` all `destructiveHint=True`.
  - `test_no_structured_content_or_output_schema_after_annotating`: a call result has `structured_content is None` and listed tools have `output_schema is None`.
- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement `annotations.py`; apply in `on_list_tools` with `model_copy(update={"output_schema": None, "annotations": ...})` using the field confirmed in Task 1.**
- [ ] **Step 4: Run the tests; expect PASS. Update README tool-doc section with one sentence on hints (table unchanged).**
- [ ] **Step 5: Checkpoint.**

### Task 5: HTTP transport, `/health`, path-tool gating, host protection

**Files:** Modify `src/mealie_mcp/config.py`, `src/mealie_mcp/server.py`, `src/mealie_mcp/tools/recipes.py` (`register` gains `allow_local_files: bool = True`); Test `tests/test_config.py`, `tests/test_tools.py`.

**Interfaces:**
- `Config` gains `transport: str = "stdio"` (`"stdio"|"http"`), `host: str = "127.0.0.1"`, `port: int = 8000`, `allowed_hosts: tuple[str, ...] = ()`; env names `MEALIE_MCP_TRANSPORT`, `MEALIE_MCP_HOST`, `MEALIE_MCP_PORT`, `MEALIE_MCP_ALLOWED_HOSTS` (comma-separated). Invalid transport or port raises `ConfigError` like existing parsers.
- `build_server(config)` registers `upload_recipe_image` and `import_recipe_from_images` only when `config.transport == "stdio"`.
- `main()` runs `build_server(config).run(...)` over HTTP at path `/mcp` per Task 1's API when `transport == "http"`; the startup probe still runs first and exits 2 on failure.
- `/health` returns `{"status":"ok"}` only; no Mealie call.
- If Task 1 found no Host/Origin validation, `allowed_hosts` is enforced (reject others with 403 or 421); if FastMCP validates natively, pass `allowed_hosts` through and note it in docs.

- [ ] **Step 1: Write failing tests:**
  - `test_config.py`: defaults; each variable parsed; bad transport / port / non-int port raise `ConfigError`; allowlist splits and strips.
  - `test_tools.py`: `test_http_mode_drops_path_tools` (transport http -> both names absent, 41 tools; stdio -> present, 43); `test_http_read_only_leaves_only_read_tools` (Review Focus 4: names == `READ_TOOLS`).
  - HTTP smoke (FastMCP in-process HTTP test client or ASGI transport per Task 1): `/health` returns 200 `{"status":"ok"}` and the body contains neither the token (`"tok"`) nor the base URL; `/mcp` initialize succeeds with an allowed Host; a disallowed Host is rejected (Review Focus 5). No sockets beyond loopback.
- [ ] **Step 2: Run `pytest tests/test_config.py tests/test_tools.py -k "http or transport or health"`; expect FAIL.**
- [ ] **Step 3: Implement config fields, `build_server` gating, `main` transport branch, health route, host protection.**
- [ ] **Step 4: Run Step 2 command and full `pytest tests/test_config.py tests/test_tools.py`; expect PASS. Run `pytest tests/test_docs.py`; expect it to FAIL until Task 7 documents the new variables (record, do not relax).**
- [ ] **Step 5: Checkpoint.**

### Task 6: Container image

**Files:** Create `Dockerfile`, `.dockerignore`; Test: manual build and run (needs approval).

**Interfaces:** Produces image `mcp-mealie` exposing port 8000, env `MEALIE_MCP_TRANSPORT=http`, `MEALIE_MCP_HOST=0.0.0.0`, entrypoint `mcp-mealie`, `HEALTHCHECK` GET `/health`.

- [ ] **Step 1: Write `Dockerfile`:** multi-stage from locked deps (`uv sync --frozen --no-dev`), slim Python 3.12 base, non-root user, no `.env`/secrets copied (`.dockerignore` excludes `.env*`, `HANDOFF.local.md`, `docs/superpowers/plans/*.local.md`, `.git`, `.venv`). Health check uses Python `urllib` (no curl in slim).
- [ ] **Step 2: With approval, `docker buildx build --platform linux/arm64 -t mcp-mealie:dev .`; expect success. (amd64 build verified in CI, not here.)**
- [ ] **Step 3: With approval, run it against a throwaway Mealie from `scripts/integration.sh` setup (not any live instance); expect `/health` 200 and `docker inspect` health `healthy`.**
- [ ] **Step 4: Verify `docker run --rm mcp-mealie:dev id -u` is not 0.**
- [ ] **Step 5: Checkpoint.**

### Task 7: Docs, changelog, doc-sync

**Files:** Modify `README.md`, `docs/ARCHITECTURE.md`, `docs/HOWTO.md`, `CHANGELOG.md`, `CONTRIBUTING.md` (only if its docs-to-update table says so); Test `tests/test_docs.py`.

- [ ] **Step 1: Document the four new env vars (names and defaults exactly as in Task 5), the HTTP mode, the container, annotations, `confirm_replace_all`, the lossless-update behavior and its residual loss, and the path-tool decision in ARCHITECTURE.** No household specifics.
- [ ] **Step 2: Add `## [Unreleased]` entries (Added: HTTP transport, annotations, image; Fixed: `update_recipe` data loss on ingredient/step edits).**
- [ ] **Step 3: Run `pytest tests/test_docs.py`; expect PASS.**
- [ ] **Step 4: Checkpoint.**

### Task 8: Integration round trip and final gates

**Files:** Modify `tests/integration/` (new `test_update_roundtrip.py`).

- [ ] **Step 1: Write the test (runs only with `MEALIE_INTEGRATION=1`):** create a recipe with parsed ingredients and titled steps with ingredient references; call `update_recipe` changing one ingredient and one step; read back raw (`full=true`) and assert the untouched ingredients keep `referenceId` and food/unit IDs and untouched steps keep title and `ingredientReferences`; assert the guard refuses emptying.
- [ ] **Step 2: Fast gate; expect all green.**
- [ ] **Step 3: With approval, full gate: `uv run --extra dev pre-commit run --all-files`, `./scripts/integration.sh`, then with `MEALIE_TEST_VERSION=v3.25.1` and `v2.8.0`; expect all pass.** Report any failure with output; do not weaken assertions.
- [ ] **Step 4: Write the gateway hand-back (image, port, env names, health check, network needs) to `HANDOFF-RESULT.local.md` (git-excluded).**
- [ ] **Step 5: Checkpoint.**
