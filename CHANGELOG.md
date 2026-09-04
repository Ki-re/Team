# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [SemVer](https://semver.org/) for git-tagged versions.

## [Unreleased]

### Security
- **Pre-publication audit before making the repo public.** Two
  independent passes, both clean: a manual regex sweep (API-key-shaped
  strings, AWS keys, PEM blocks, JWTs, the old real gateway IP) across
  every one of the 260 blobs that have ever existed in git history — not
  just the current tree — plus `detect-secrets` over every tracked file.
  Every hit in both was an expected false positive already known from
  prior audits: `api_key: os.environ/VAR` indirection in
  `deploy/litellm.config.yaml` (the keyword "key" triggers the heuristic,
  the file never holds a real value) and the synthetic AWS-key/PEM-header
  fixtures in `tests/unit/test_validate.py`. `.gitignore` confirmed
  correct (`deploy/.env`, `.env`, `*.pem`, `*.key`); only the `.example`
  templates are tracked, and both hold placeholders only.

### Added
- `docs/logo.svg` redesigned — gradients, drop shadow, curved connectors
  on the same hub-and-spoke metaphor (still legible at favicon size), and
  a GitHub repo description set (was empty).
- `team-mcp` (the `pyproject.toml` console-script entry point, installed
  automatically by `pip install -e .`) is now the documented registration
  target instead of `python -m team_mcp.server` — one path instead of a
  binary plus a module argument to get right. Verified live: re-registered
  the real local MCP server with the new command, `claude mcp list`
  confirms `Connected`. `pyproject.toml`'s own `description` generalized
  to match the README's multi-agent framing (was Claude-only).
- Live gateway roster: 3 new entries from 2 new providers, each verified
  with a real completion against the provider's own API first, then
  again through the proxy — `tier-coder` gains `cohere_chat/command-a-03-2025`
  (Cohere's flagship) and `cloudflare/@cf/qwen/qwen2.5-coder-32b-instruct`
  (a dedicated coder model, Cloudflare Workers AI free tier); `tier-fast`
  gains `cohere_chat/command-r7b-12-2024`. Two other candidate providers
  were tested and rejected: **SambaNova** now requires a payment method
  on file for every model (`PAYMENT_METHOD_REQUIRED`, balance_units=0) —
  contradicts what was told to the user based on external research alone
  ("no card needed"); corrected here. **NVIDIA NIM** 404s as "not found
  for account" on every model tried (5/5), despite all being listed in
  its own `/v1/models` catalog — the same catalog-vs-entitlement gap
  already seen with Gemini Pro and Cerebras. Neither added. Not mirrored
  into `deploy/litellm.config.yaml` (lean template since the 1.1.0 trim,
  not a live mirror — see the prior roster-expansion entry above).

- `engine/verify.py::compress_log()`: strips ANSI codes and collapses
  repeated consecutive lines (progress bars, retry noise) before
  truncating subprocess output — replaces four blind `[-3000:]`/`[-2000:]`
  tail-slices (lint/test output in `verify.py`, `_run_repro` in
  `feature.py`, `_check_tests` in `validate.py`) that previously spent
  their character budget on whatever happened to be last, not
  necessarily the actual failure. Inspired by evaluating OmniRoute's
  "RTK" log-compression engine — reimplemented the log-cleaning idea in
  ~15 lines of stdlib regex rather than adopting it wholesale. Its prose
  ("Caveman") half was deliberately skipped: team-mcp's prompts are
  already terse templates plus real code, not verbose human prose, so a
  filler-word pass has nothing worth cutting there.

### Fixed
- **The same report, still failing after the fix above** — turned out the
  fix was real but incomplete. The ledger showed genuine `ReadTimeout`s
  on `tier-coder` in production (a real, transient, provider-side
  degradation — direct probes against the flagged models and against the
  gateway itself succeeded fine minutes later, consistent with this
  project's prior experience with free-tier flakiness). The new
  premium-tier rescue now *routes into* `repair_loop` far more often than
  before — and `repair_loop`'s own `router.coder()` call was never
  wrapped in try/except, unlike every other tier-coder call site in this
  codebase. So exactly while tier-coder was flaky, the rescue path itself
  crashed uncaught, looking identical to the original bug from the
  outside. Audited every `router.*` call site in the codebase for the
  same gap and found four more, all now fixed the same way (retry/record,
  never propagate uncaught):
  - `engine/repair.py::repair_loop` — the tier-coder call itself.
  - `workflows/task.py` — `team_task`'s own tier-coder call (same class
    of bug, different tool).
  - `workflows/ask.py` — the final tier-context synthesis call; now falls
    back to the raw per-chunk digests instead of crashing when they're
    already sitting right there.
  - `workflows/feature.py` — three unwrapped `critic_review()` call sites
    (`_run_new`, `_run_fix`, and `_run_review`'s 3-way `asyncio.gather`,
    which had no `return_exceptions=True` — one downed rubric pass used
    to cancel the other two and crash `kind=review` entirely; now reports
    e.g. "1/3 review passes unavailable" instead).
  - `workflows/epic.py` — `team_epic`'s own node-level `asyncio.gather`
    had the identical no-isolation shape; one node crashing uncaught
    (known bug or not) used to cancel its sibling nodes too. Now a
    crashing node is caught and reported as that node's own failed
    result, siblings unaffected — defense-in-depth alongside the
    specific fixes above, in case some other uncaught path still exists.
  Verified live: `team_feature` and `team_epic` both still complete
  normally end-to-end against the real gateway (unchanged manifest
  shapes), and each fix has a unit test that reproduces the exact crash
  (a fake router that raises `TimeoutError`, matching the real ledger
  entries) and confirms it's now handled instead of propagating.

- **Two real bugs reported by another agent using team-mcp on an
  unrelated project** ("errored out on every directory-shaped
  target_paths call, then gave 'no consensus' even with file-level
  targets — a farm-side issue, not something reformatting the spec
  fixed"). Both confirmed by reading the code and reproducing locally,
  not just from the report:
  - `workflows/feature.py::_read_base_files` handed any `target_paths`
    entry straight to `Path.read_text()` as long as it `.exists()` —
    true for directories too. Reading a directory raises
    (`PermissionError` on Windows, confirmed by reproducing it locally;
    `IsADirectoryError` on POSIX), uncaught anywhere upstream, so a
    directory-shaped `target_paths` crashed the whole MCP tool call with
    no `Manifest` at all — violating the project's own hard rule that
    every failure comes back as a `Manifest`, never an uncaught
    exception. Added `_validate_target_paths()`, checked once at the top
    of `run()` for all four `kind`s, returning a clear error instead.
  - `engine/consensus.py` has computed an `escalate_to_premium` signal
    since it was first written, specifically for the case where no
    candidate satisfies even one other candidate's tests (`winner_id is
    None`) — but nothing ever read that flag. `_run_new` just gave up
    with "no clear consensus winner... needs manual synthesis or
    team_epic," advice that doesn't actually help (`team_epic` runs the
    same algorithm, it wouldn't converge either). `_run_refactor` and
    `_run_fix` already had this right — both fall back to `repair_loop`'s
    existing premium-tier escalation when their own winner-selection
    comes up empty; `_run_new` now does the same. Verified live: the
    normal (winner-found) path still produces the same
    `implemented with N candidates (winner=..., score=...)` manifest as
    before; the rescue path itself is covered by a new unit test that
    forces `winner_id=None` via a monkeypatched `consensus.verify_candidate`
    while leaving `repair.py`'s own verification real (a genuine
    subprocess `pytest` run against a genuine scratch dir, not mocked),
    confirming `_run_new` actually invokes and uses the rescue rather
    than just asserting the return value of a fully-mocked call.

### Added
- `python -m team_mcp.cli usage [--days N]`: a combined token-usage
  report. Investigated whether agy's usage could be synced *into*
  LiteLLM's own spend tables instead (so there'd be one source of truth)
  — no supported endpoint for it on this OSS deployment (`/spend/report`
  exists but is Enterprise-only and 402s here; LiteLLM's `mock_response`
  testing feature can register a fake request but not with real custom
  token counts; direct Postgres writes would need a DB port that isn't
  exposed outside the gateway's own docker network, and would couple this
  project to LiteLLM's internal schema across version upgrades). Merges
  LiteLLM's `GET /user/daily/activity` (free on OSS, unlike the
  Enterprise-only `/spend/report` — everything that went through the
  proxy) with team-mcp's own ledger (`agy:`-prefixed rows only, the sole
  place agy's usage lands) into one printed report instead, since a
  literal data merge isn't available. `GatewayProvider` gains
  `daily_activity()`; `Ledger` gains `spend_summary()` (per-model
  **sums**, not the averages `model_stats()` already provided for
  selftest-style health checks).

### Fixed
- `providers/agy.py`/`router.py`: `premium_review` hardcoded
  `tokens_in=0, tokens_out=0` into the ledger unconditionally, for every
  premium-tier call, agy or fallback alike — the gateway fallback's real
  usage (`resp["usage"]`) was already available and simply discarded, and
  the agy-CLI path had genuinely no usage info because it requested
  `--output-format text`. Switched agy's default invocation to
  `--output-format json` (confirmed live: agy returns
  `{"response": ..., "usage": {"input_tokens": N, "output_tokens": N, ...}}`
  in that mode) and added `_parse_agy_output()` to extract both, with a
  clean fall-back to treating the whole output as plain content (and
  `last_usage=None`, not a fabricated zero) for any other CLI substituted
  in via `TEAM_AGY_CLI_ARGS` that doesn't share that shape. `PremiumProvider`
  gains `last_usage`, threaded into the ledger the same way `last_used`/
  `last_error` already were. Verified live end-to-end (not just against
  mocks): a real `premium_review` call landed real, non-zero token counts
  in `.team_sandbox/ledger.sqlite3`.

### Added
- Live gateway roster: 7 new free-tier model entries added via LiteLLM's
  admin API (`store_model_in_db: true`, no redeploy needed), each
  verified with a real completion against the provider's own API first
  and then again through the proxy — `tier-fast` gains `groq/qwen3.8-27b`
  and `openrouter/liquid/lfm-2.5-2.6b:free`; `tier-coder` gains
  `mistral/devstral-latest` (a coding-agent-specific model, same
  "Experiment" key already used for codestral) and `groq/openai/gpt-oss-120b`
  (same Groq key as the 20b already in the pool, its own independent
  rate-limit bucket); `tier-context` gains `gemini-3.1-flash-lite-preview`
  and `gemini-3-flash-preview` (separate model ids from the stable
  versions already in the pool, so likely separate quota buckets — worth
  watching via `selftest`) and `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`
  (1M context). Not mirrored into `deploy/litellm.config.yaml`, which
  stays the deliberately lean starting template from the 1.1.0 trim
  (the live instance has never been a 1:1 mirror of that file since
  then). `thinkingmachines/inkling:free` was evaluated and rejected: it
  refuses plain chat-completions calls, only usable from an "agentic
  harness." Cerebras re-checked and still hard-402s (unchanged since
  Phase 9); `GEMINI_PRO_API_KEY` in `deploy/.env` confirmed empty/unused.
- README: a "Why LiteLLM" section explaining the provider-indirection
  rationale (one API shape, many swappable providers/models behind it), a
  table of the free-tier providers this project actually uses (Groq,
  OpenRouter, Google AI Studio, Mistral), a disclaimer about free-tier
  volatility, and a note that self-hosted OpenAI-compatible endpoints
  (vLLM, Ollama, etc.) plug into the same four tiers with no code changes.
- README: a "The premium tier: why `agy`, and how to swap it" section
  documenting the new `TEAM_AGY_CLI_ARGS` mechanism (see below) with
  concrete examples for Codex CLI and `claude -p`.
- `providers/agy.py`: `TEAM_AGY_CLI_ARGS`, a pipe-separated argv template
  (`"{prompt}"` substituted as one literal argv element, never
  shell-interpolated) that lets a different subscription CLI be swapped
  in for `agy` via `.env` alone in the common case, no fork needed.
  Defaults to `agy`'s own exact invocation, so existing setups are
  unaffected unless this is set explicitly.

### Changed
- README and the two main diagrams (`architecture.svg`,
  `team_feature_pipeline.svg`) now frame the orchestrator generically ("a
  coding agent" — Claude, Codex, Hermes, or anything else that speaks
  MCP) instead of assuming Claude specifically, while keeping the
  concrete, tested Claude Code registration steps as the documented path.
- Full-repo translation: every remaining Spanish comment, docstring,
  prompt template, and error string across `src/`, `tests/`, and this
  CHANGELOG is now English. LLM-facing prompts (`feature.py`,
  `repair.py`, `docs_sync.py`, `ask.py`) were re-verified live against
  the real gateway after translation, not just syntax-checked — a prompt
  wording change is a behavioral change, not a cosmetic one.
- `.github/workflows/tests.yml`: CI now runs `ruff check` and `mypy` in
  addition to `pytest` — the underlying reason both had gone the whole
  session without genuinely passing (see below) is that nothing made them
  mandatory on every push.

### Fixed
- **Autonomous audit of this session's work** (per explicit request:
  "check your previous work for mistakes... fix them, don't wait for my
  order"). `mypy` had never actually been run in earnest despite being in
  the dev deps: 19 real errors, all fixed — most were `extract_json()`
  (returns `dict | list`) used as if it were always `dict` with no
  narrowing; added `extract_json_dict()` (fails with a clear
  `JsonExtractionError` if the model returns a list where an object was
  expected, instead of the previous cryptic `TypeError`) and migrated
  every site that assumed `dict`. The rest were variables reused with
  different types within the same function scope (`report`, `winner`,
  `c`) — harmless at runtime but fragile; renamed.
  `ruff check` wasn't clean either (stale noqa comments, unsorted
  imports) — fixed too, `ruff`/`mypy`/pytest are all green at once now.
- **Real bug found verifying live after the mypy change** (not caused by
  it): `engine/repair.py::_parse_repair_edits` and several spots in
  `workflows/feature.py` built `FileEdit(path=...)` directly from the
  `path` field the model returns, without normalizing it — a `kind=fix`
  worker copied a path with a subfolder from the `repro_command` (which
  did mention it) instead of using the flat basename the pipeline's
  internal "basename space" expects, and the whole call crashed with
  `EditConflict: ...doesn't exist...` with no manifest, the same class of
  failure already closed for consensus.py in Phase 14. Added
  `_force_basename()` (in `feature.py` and `repair.py`) which forces the
  basename at the boundary where the model's output enters the internal
  space, plus an explicit "no folders" instruction in the `fix`/
  `refactor`/repair prompts that lacked it (`_IMPLEMENT_PROMPT` already
  had it, from the start). Reproduced and verified live twice: the
  scenario that used to crash now fails cleanly with a clear manifest
  (when the `repro_command` references a path that doesn't exist in the
  flattened scratch dir — a real, still-unresolved limitation, documented
  in the code, see below), and the equivalent scenario with a flat-
  basename `repro_command` correctly fixes the bug end to end.

### Security
- Rewrote the entire git history (`git filter-repo`) to scrub every
  remaining occurrence of the author's real private gateway IP from past
  commits — file content and commit messages both — after finding the
  forward-only anonymization from the 1.1.0 cycle hadn't touched history.
  The repo was private with 0 forks/0 stars at the time, confirmed
  immediately before rewriting; a full bundle backup was taken first, and
  a fresh mirror clone of the pushed remote afterward confirmed 0 matches
  across all refs. Tags (`v1.0.0`, `v1.1.0`) were preserved pointing at
  their rewritten commits, and CI passed on the force-pushed history.

### Known limitations (documented, not fixed)
- `kind=fix`: the `repro_command` runs with its cwd in a scratch dir that
  only contains flat basenames, never `target_paths`' real subfolder
  structure. A `repro_command` that references a path with a subfolder
  the way the user would see it (`pytest playground/test_x.py` instead of
  `pytest test_x.py`) fails with "file or directory not found" even when
  the fix is correct. The real fix requires `_run_fix` to preserve real
  paths in its scratch dirs instead of flattening them — this touches the
  convention shared with `kind=new`/`kind=refactor`, and hasn't been done
  without supervision. Documented in `_run_repro` (`feature.py`).

## [1.1.0] - 2026-08-28

Second pass of publication prep: the repo goes from "private and usable
by the author" to "generically installable and understandable by
anyone." The README and user-facing documentation (`docs/DIAGRAMS.md`,
`docs/KB_CONVENTION.md`, the 7 diagrams, the `.env.example` files,
`deploy/litellm.config.yaml`) rewritten in English — the rest of the
project (code comments, this CHANGELOG, the rest of the internal docs)
is now in English too, following the same standard.

### Added
- `LICENSE` (MIT) and packaging metadata in `pyproject.toml` (`license`,
  `readme`, `[project.urls]`) — the repo had no license.
- `docs/logo.svg`: an original logo (central node + 4 connected nodes,
  representing the orchestrator and the model farm). First attempted via
  Gemini (`gemini-3.1-flash-image` and variants) through its direct API —
  blocked with 0 quota on this account's free tier for every image-
  generation model (same pattern already documented for Gemini Pro/
  embeddings/TTS); OpenRouter had no free image model either. An original
  SVG was a reasonable alternative: scales losslessly, no external
  infrastructure dependency.
- `skill/SKILL.md` and `skill/CLAUDE.md`: a portable version (English, no
  author-specific paths or IPs) of the global skill and usage guide —
  previously these only existed locally under `~/.claude/`, outside the
  repo, useless to anyone else who cloned the project.
- "Set up with an AI coding agent" section in the README: a self-
  contained prompt anyone can paste into their own agent so it clones,
  installs, deploys the gateway, registers the MCP server, and installs
  the skill — without asking it for credentials, only asking the human
  for them.
- `.github/workflows/tests.yml`: CI that runs the pytest suite on every
  push/PR — the repo had no automated check at all.
- "Architecture" section in the README with two diagrams (system
  components, the `team_feature` pipeline), and `docs/DIAGRAMS.md` with
  the other 5 (`team_task`, `team_epic`, `team_ask`, `team_validate`,
  `docs_sync`). Hand-authored SVG, not Mermaid — the initial Mermaid
  diagrams turned out unreliable/ugly in several viewers.
- `tests/test_readme.py`, `tests/test_diagrams.py`, `tests/test_diagrams_md.py`:
  check that the README has no real IP, that all 7 SVGs exist and are
  valid XML with `viewBox`/`role`/`aria-label`, and that
  `docs/DIAGRAMS.md` references all 5 tools and the 5 correct image paths.
- Optional `update_docs`/`kb_path` on `team_feature`/`team_epic`: after a
  successful code change, a documentation sub-agent (`docs_sync`) decides
  which files in a markdown knowledge-base (frontmatter + `INDEX.md`,
  same convention as Claude's own memory — see `docs/KB_CONVENTION.md`)
  went stale and updates them. Two passes (cheap selection over the
  index, then a per-file edit with real content, with retry) — a first
  single-pass version failed on the first live test because the model
  had no real text to copy for the `search` block. Only updates existing
  entries, never creates new ones in this version.
- `team_validate`: when `scope` is a directory with `INDEX.md`, adds free
  deterministic checks for invalid YAML frontmatter (blocking), broken
  relative links, and entries with an expired `last_verified`
  (both informational).
- `engine/frontmatter.py`: frontmatter parsing and KB utilities (cheap
  index, broken-link detection, staleness), reused by `docs_sync` and
  `team_validate`.

### Changed
- README rewritten from scratch for a general audience: a real quickstart
  (prerequisites, cloning, deploying your own gateway, configuring,
  registering the MCP server, verifying), a table of the 5 tools, the
  project structure, how to run the tests — no longer assumes the reader
  is the author with their deployment already done.
- `deploy/litellm.config.yaml` trimmed to a shorter, generic template (2
  models per tier instead of up to 8) — the full roster, actually tuned
  to the author's account quotas, still lives on the real server (added
  live via LiteLLM's admin API in Phase 13); this file is now a
  reasonable starting point for a fresh deployment, not an exact mirror
  of that specific instance.
- `tier-coder`: 4 more free OpenRouter models (`poolside/laguna-s-2.1:free`,
  `poolside/laguna-xs-2.1:free`, `minimax/minimax-m3:free`, and
  OpenRouter's own router `openrouter/free`, which randomly distributes
  across ~23 free models in its catalog). Motivated by a real failure
  diagnosed live: ~2h of calls hanging for the full timeout (120s, zero
  output) because a pool of only 4 models had too much risk concentration
  in any single downed backend. Added live via LiteLLM's admin API (no
  redeploy) and verified live before and after: 10/10 real calls healthy
  after the change.

### Fixed
- Anonymized every appearance of the gateway's real private IP
  (`203.0.113.10`) in `README.md`, `.env.example`, `deploy/.env.example`.
  `TEAM_GATEWAY_URL`'s default in `config.py` changes from that IP to
  `http://localhost:4000`, a reasonable generic default for public code
  instead of the original author's specific value.
- **Real bug found by using `team` itself on this very repo**:
  `engine/consensus.py::run_consensus` didn't catch `EditConflict` while
  materializing each candidate's edits in the N×N matrix — a single
  candidate whose `search` didn't match cleanly (ambiguous or
  nonexistent) aborted the ENTIRE `team_feature` call with no manifest,
  instead of just discarding that one cell. Reproduced live against a
  real README with repeated phrases (`the "search" block doesn't appear
  exactly once (occurrences=5)`).

## [1.0.0] - 2026-08-27

First public release. All 5 MCP tools (`team_task`, `team_feature`,
`team_epic`, `team_ask`, `team_validate`) are complete, with no stubs,
and verified live against the real gateway — see `.claude/plans`
(not version-controlled, it's local development state) for the
phase-by-phase detail.

### Added
- MCP core (`server.py`, `router.py`, `sandbox.py`, `ledger.py`,
  `schemas.py`) with a path cage, SQLite telemetry, and `agy` (Antigravity
  CLI) resolution for the premium tier.
- Quality primitives: deterministic verification (`verify.py`), N×N
  cross-validation consensus (`consensus.py`), adversarial critique with
  an anti-false-positive filter (`critic.py`), a bounded repair loop with
  stagnation detection and escalation to `agy` (`repair.py`).
- `team_task`: unambiguous single-file change, with auto-escalation to
  `team_feature` if the deterministic gate fails twice.
- `team_feature`: fan-out + consensus + critique + repair, with
  `kind ∈ {new, refactor, fix, review}`. `refactor` preserves behavior
  via characterization tests; `fix` requires a real `repro_command`
  (red→green, not a test the model makes up).
- `team_ask`: questions about code/logs via map-reduce on `tier-context`,
  with `path:line` citation verification and optional web search
  (Tavily, via LiteLLM's MCP Gateway) for current external context.
- `team_epic`: orchestrates a DAG of nodes in parallel topological waves,
  with a real token budget and a clean stop when it runs out.
- `team_validate`: GO/NO-GO verdict (syntax, tests, secrets, git, lint,
  requirement traceability, architecture review), and a `selftest` mode
  that audits the health of the 4 tiers.
- Global MCP registration (`claude mcp add --scope user`) and a global
  skill (`~/.claude/skills/team/`) so any session/project discovers it.
- Unit test suite (`tests/unit/`, pytest) over all deterministic/local
  logic; live-model code paths are verified manually against the real
  gateway, not in the suite.
- Weekly scheduled health check (`selftest`) via a scheduled task.

### Fixed
- `extract_json`: a greedy regex that could cross over a stray array on
  top of a real JSON object; and no defense against `<think>` blocks from
  reasoning models corrupting the parse.
- `team_feature`'s fan-out: each worker's failures (timeout, 429, broken
  JSON) were indistinguishable under one generic message; now the real
  error from each is propagated and reported.
- Ledger: the `note` column never got filled in on failures or on `agy`'s
  silent degradations to its fallback; now it does, verified live by
  forcing a real error against the gateway.
- `budget=0` in `team_epic` was treated as "unspecified" (falsy) instead
  of forcing the requested immediate stop.
- `Sandbox.workdir_copy`: dead code, badly indented and with no caller,
  removed instead of resurrected.
- Provider roster kept up to date with the real state of free quotas
  (Cerebras dropped after confirming a 402 across the whole account;
  Gemini roster rebuilt from the user's real quota dashboard, not guesses).

[Unreleased]: https://github.com/Ki-re/Team/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Ki-re/Team/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Ki-re/Team/releases/tag/v1.0.0
