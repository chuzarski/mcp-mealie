# Hosted, read/write mcp-mealie: design

Status: **draft, awaiting approval**. No code has been written.

## Intent

Run this server as a long-lived HTTP service so remote agents (through an MCP
gateway) can manage a Mealie library: import and tag recipes, edit existing
ones, plan meals, build shopping lists. Reads and writes both.

Writes are the point, so the work is mostly about making writes safe enough to
leave switched on, and giving the client enough information to ask a human
before anything destructive.

## Goals

1. Streamable HTTP transport at `/mcp`, selected by environment variable;
   stdio stays the default and keeps working unchanged.
2. Every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`,
   `idempotentHint`) so a client can gate edits and deletes.
3. `update_recipe` stops destroying data when it edits ingredients or
   instructions (see "Lossless list updates").
4. A container image (arm64 and amd64, non-root, health check) suitable for a
   private network behind a reverse proxy.
5. Path-reading tools cannot be used as a remote file-read primitive.

## Non-goals

- Authentication in the server. The reverse proxy owns client auth; the server
  is meant to sit on a private network.
- New Mealie features, more tools, or wider API coverage.
- Per-tool filtering beyond what `MEALIE_READ_ONLY` already does.
- Multi-user or multi-instance support: one Mealie, one token per process.

## Anti-goals (failures even if tests pass)

- A write that silently loses data a caller did not ask to change.
- A write retried after a transport error (invariant 1).
- Raw Mealie JSON, credentials, or the Mealie token reaching a response or log.
- Output schemas or `structuredContent` coming back (invariant 4).
- Anything in the public repo that names a specific household, host, or token.

## Constraints

- AGENTS.md invariants all hold; the fast gate passes; `tests/test_docs.py`
  agrees with README, config and CHANGELOG changes.
- Tool count stays 43, so the README count assertions do not move.
- Mealie 2.0+ compatibility claim is unchanged (CI matrix 2.8.0, 3.25.1, 3.31.0).
- No new dependency unless stdlib cannot do it. FastMCP 4 already ships the HTTP
  transport.
- Tests are offline outside `tests/integration/`. Running anything, including
  the Docker integration suite, needs the owner's approval first.

## Approaches considered (edit fidelity)

- **A. Structured round trip.** Return raw ingredients and steps; callers send
  them back whole. Faithful, but 2-3k tokens per edit and one dropped field
  loses data silently. Rejected: fights the token-efficiency requirement.
- **B. Targeted edit tools** (`edit_ingredient`, `add_step`, ...). Safest and
  cheapest per edit, but ~6 more tools whose docstrings are paid on every
  request, and whole-recipe rewrites are awkward. Not chosen; remains the
  fallback if C proves flaky.
- **C. Lossless whole-list replace (chosen).** Keep the existing signature.
  The server matches incoming lines to existing items and reuses the original
  objects for unchanged ones. No new tools, no extra tokens.

## Current defect being fixed

`get_recipe` flattens ingredients to display strings and steps to
`"title: text"`. `update_recipe` replaces the whole list, so an agent must send
those strings back (`recipes.py` `update_recipe`, `_ingredient_payload`,
`_instruction_payload`). On the way back:

- ingredient strings are re-parsed, so food/unit IDs are re-guessed and
  unmatched foods fold into the note;
- every `referenceId` is regenerated;
- step titles are merged into step text;
- step `ingredientReferences` and step ids are dropped.

Editing one ingredient therefore rewrites all of them, irreversibly.

## Lossless list updates (design)

For `ingredients`, `instructions` (and `notes`, which are `{title,text}` and
already round-trip, so only if tests show loss):

1. `update_recipe` already fetches the current recipe. Keep its raw
   `recipeIngredient` / `recipeInstructions`.
2. Compute the display form of each existing item with the same functions
   `shape.ingredient` / `shape.instruction` use, since that is what the caller
   saw.
3. For each incoming item that is a string:
   - match against the existing item at the **same position** first;
   - otherwise against the first **unused** existing item with identical display
     text (handles reordering and duplicates deterministically);
   - on a match, emit the existing raw item (passed through `writable`-style
     trimming only for fields Mealie rejects), untouched;
   - otherwise treat it as new or edited: ingredients go through the parser as
     today, steps become `{title:"", text, ingredientReferences:[]}`.
4. Structured dict items from `parse_ingredients` are handled exactly as today.
5. **Mass-loss guard.** If the update would leave an existing list empty, or
   drop more than half of four or more items, refuse with a message naming the
   counts, unless the caller passes `confirm_replace_all=true`. Builder may
   tune the thresholds; the guard itself is a requirement.
6. The tool result states what was preserved vs rewritten (counts only) so the
   caller can see a rewrite happened.

Known residual loss, documented in the tool docstring in one line: an
*edited* line loses its step links and ingredient IDs (it is re-parsed).

Shaper-level helper(s) go in `shape.py` (pure, no I/O); the matching logic is
pure and unit-tested with fixtures, including duplicate lines, reordering,
a Mealie 3.30+ recipe carrying step durations/substitutions, and an
unresolved-food ingredient.

## Hosting design

- **Transport.** `MEALIE_MCP_TRANSPORT` = `stdio` (default) | `http`;
  `MEALIE_MCP_HOST` (default `127.0.0.1`), `MEALIE_MCP_PORT` (default `8000`).
  Path fixed at `/mcp`. Validated in `config.py` like the others and added to
  the doc-sync check. The container image sets host `0.0.0.0`.
- **Startup.** The existing probe still runs before the transport starts and
  still exits 2 on failure, so a misconfigured container fails loudly.
- **Health.** A `/health` route that reports process liveness only; it does
  not call Mealie, and never echoes configuration. Whether it should also
  reflect Mealie reachability is the builder's call (default: no).
- **Host/Origin.** Verify what FastMCP 4's HTTP app enforces and document it.
  If it does not validate `Host`/`Origin`, add an allowlist setting
  (`MEALIE_MCP_ALLOWED_HOSTS`) so DNS-rebinding protection is not left to the
  proxy alone. To be settled by reading FastMCP 4 source before coding.
- **Remote file read.** In `http` mode, `upload_recipe_image` and
  `import_recipe_from_images` are not registered (same mechanism as
  read-only: not registered, rather than refused). Mealie's own URL-based
  `set_recipe_image` stays.
- **Logging.** Stderr only, no headers or bodies. HTTP access logging, if
  enabled by the server, must not log `Authorization`.
- **Image.** Multi-stage build from the locked dependencies, `uv`-based,
  non-root user, no `.env` baked in, arm64 + amd64, `HEALTHCHECK` against
  `/health`. Secrets (`MEALIE_API_TOKEN`) arrive via environment or file at
  run time, never in the image or repo.

## Tool annotations

Add annotations at registration: reads `readOnlyHint=true`; creates and
updates `destructiveHint=false`; updates that overwrite (`update_recipe`,
`set_recipe_image`, `upload_recipe_image`) `destructiveHint=true`; all deletes
`destructiveHint=true`; `idempotentHint=true` only where genuinely idempotent
(set-style updates, not creates). A test walks every registered tool and
asserts it has annotations consistent with its `READ_TOOLS` / `WRITE_TOOLS`
membership, so a new tool cannot ship unlabelled. Annotations are
`openWorldHint` where a tool makes Mealie fetch external URLs.
Check these do not reintroduce `structuredContent` or output schemas.

## Testing

- Shaper/matcher: `tests/test_shape.py` with fixtures (lowest layer).
- `update_recipe` end to end: `tests/test_tools.py` through an in-process
  client with `respx`, asserting the PATCH body preserves untouched items byte
  for byte and the guard refuses mass loss.
- Config: `tests/test_config.py` for the new variables.
- Annotations and HTTP-mode tool set: `tests/test_tools.py`.
- HTTP transport smoke test in-process (no network beyond loopback), if
  FastMCP offers a test client for it.
- `tests/integration/` gets a round-trip test (create, edit one ingredient,
  assert the others keep their IDs and step links) that runs only with
  `MEALIE_INTEGRATION=1`, **after approval**.
- Full gate for anything touching `client.py` or endpoints; fast gate otherwise.

## Docs and changelog

README (tool table untouched; env table, HTTP section), `docs/ARCHITECTURE.md`
(lossless matching, HTTP mode, remote-file-read decision), CHANGELOG
`## [Unreleased]`, HOWTO hosting section. Own the fork's identity in docs
without household specifics.

## Hand-back to the gateway owner

Image/build context, port, env names, volumes (none expected), health check,
network needs (private network shared with the proxy, plus reach to Mealie),
DNS note. Delivered as a short file once the image builds.

## Decided in conversation vs my calls

Decided with the owner: fork and host it over HTTP; read/write for all of
recipes, meal plans and shopping lists; approach C for edit fidelity.

My calls (correct any): annotations on all 43 tools; env-var transport
selection with stdio default; `/health`; disabling path-based image tools in
HTTP mode; mass-loss guard with `confirm_replace_all`; Host/Origin allowlist
only if FastMCP lacks it; no server-side auth.

## Left to the builder

Exact guard thresholds; matching tie-breaks beyond those stated; image base
and build tooling; whether `/health` checks Mealie; annotation edge cases;
exact docstring wording (keep it terse: docstrings are paid on every request).

## Open before coding

- Approval to run tests, and separately the integration suite, against a
  throwaway Mealie.
- Network plan for the container (needs the gateway owner).
- Whether to publish the branch and when (owner asks first).
