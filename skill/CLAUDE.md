# MCP "team" — a model farm for saving your own context/quota

There's an MCP server called **team** registered globally (available in
any project). It delegates heavy work to a farm of free/cheap models
(Groq, OpenRouter, Mistral, Gemini, ...) through a self-hosted LiteLLM
gateway, using fan-out + cross-validation consensus + adversarial critique
+ bounded repair — never a single small model without a safety net. The
code lives wherever you cloned https://github.com/Ki-re/Team.

## When to use it

Use it for substantial implementation, analysis, or review work —
anything that would eat a real chunk of your own context if you did it
yourself. **Don't use it** for trivial one-line edits, quick reads, or
anything already faster to resolve directly with your own tools
(Read/Edit/Grep) than by orchestrating a gateway call.

## The 5 tools (in increasing order of complexity)

- **`team_task(instruction, target_path)`** — a small, unambiguous change
  in 1 file (rename, regex, docstring, trivial fix). If the deterministic
  gate fails twice, it escalates itself to `team_feature` and says so in
  the manifest.
- **`team_feature(spec, target_paths, kind?, repro_command?, update_docs?, kb_path?)`**
  — the main tool. `kind`:
  - `new` (default): implement something new.
  - `refactor`: preserves behavior via characterization tests generated
    before touching anything; if the refactor breaks them, it's rejected
    with no appeal.
  - `fix`: **requires `repro_command`**, a real shell command that fails
    TODAY (non-zero exit) and must exit 0 after the fix. Not a test the
    model invents — the actual proof.
  - `review`: doesn't generate code, only critique (3 passes with
    different rubrics: correctness/security/simplicity), deduplicated.
- **`team_epic(plan, budget?, update_docs?, kb_path?)`** — a multi-task
  plan with dependencies (DAG), orchestrates `team_feature` over each
  node.
- **`team_ask(question, scope_paths, allow_web_search?)`** — questions
  about code/logs without writing anything, map-reduce with citation
  verification (`path:line`). `allow_web_search=True` for current
  external context (a library's version, an API's docs).
- **`team_validate(scope, spec_original?, selftest?)`** — closing
  GO/NO-GO verdict. If `scope` is a directory with `INDEX.md` (a
  knowledge base, see below), it adds free frontmatter/dangling-link/
  staleness checks with zero token cost.

## Markdown knowledge bases (`update_docs`/`kb_path`)

If the project already keeps a markdown knowledge base (a folder with
`INDEX.md` + one file per topic with frontmatter — the SAME convention as
your own memory system: `name`/`description`/`tags`/`last_verified`), pass
`update_docs=True` and `kb_path="path/to/kb"` to `team_feature`/
`team_epic`. After a successful code change, a cheap sub-agent decides
which EXISTING files in the knowledge base went stale and updates them —
it never creates new entries on its own (that stays your call). The
knowledge base can be a folder in this same repo, or a separate dedicated
repo elsewhere in the organization — in that second case, its local clone
path needs to be in `TEAM_SANDBOX_ROOTS` or the write gets rejected. See
`docs/KB_CONVENTION.md` in the MCP's repo for the full standard.

## How to read the response

Every tool returns a **compact manifest** (JSON: `files_changed`,
`tests_status`, `critic_findings_open`, `summary`, `escalated_from`,
`provider_used`), never the full generated code or intermediate results.
Reading the manifest is enough — no need to re-read the files it changed
unless the manifest itself points at a failure you want to diagnose.

If `tests_status` is `"red"` or the `summary` describes a failure, don't
blindly retry the same call: read the reason (it usually comes with the
literal error) and adjust the spec, or if `escalated_from` shows up, use
the higher-tier tool it suggests.

## Sandbox

Writes are limited to the current working directory (whichever project
you have open) plus whatever `TEAM_SANDBOX_ROOTS` says in the MCP's own
`.env`. If a call fails with "path outside the whitelist", the
`target_path`/`target_paths` pointed outside the current project.

## If the gateway isn't responding

The gateway is a self-hosted service, not managed by Anthropic. If calls
fail with connection errors, the server is probably down or missing API
keys for that tier — not necessarily a bug in the MCP itself. Diagnose it
with `python -m team_mcp.cli probe --provider gateway` from the MCP's
repo.
