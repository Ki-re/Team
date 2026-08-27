---
name: team
description: Delegates code implementation, log/repo analysis, or review to the free model farm of the team-mcp project, via the "team" MCP already registered globally. Use it when the task would eat a real chunk of your own context — not for trivial one-line edits.
---

There's an MCP server called **team** registered globally (`claude mcp
list` should show it `Connected` in any project). It delegates heavy work
to a farm of free/cheap models (Groq, OpenRouter, Mistral, Gemini, ...)
through a self-hosted LiteLLM gateway, using fan-out + cross-validation
consensus + adversarial critique + bounded repair — never a single small
model without a safety net. Code lives at
https://github.com/Ki-re/Team.

The full guide (all 5 tools, how to read the manifest, what to do if the
gateway isn't responding) lives in `~/.claude/CLAUDE.md`, which loads on
every session start — not duplicated here to avoid the two copies
drifting apart. Quick reference for the 5 tools:

- `team_task(instruction, target_path)` — 1 file, unambiguous change.
- `team_feature(spec, target_paths, kind?, repro_command?, update_docs?, kb_path?)`
  — the main one; `kind ∈ {new, refactor, fix, review}`. `fix` requires a
  real `repro_command`.
- `team_epic(plan, budget?, update_docs?, kb_path?)` — DAG of nodes
  `{id, spec, target_paths, depends_on?}`, executed in parallel
  topological waves.
- `team_ask(question, scope_paths, allow_web_search?)` — questions
  without writing anything; `allow_web_search=True` when the answer needs
  current external context.
- `team_validate(scope, spec_original?, selftest?)` — GO/NO-GO verdict;
  `selftest=True` diagnoses the health of the 4 tiers instead of
  validating code. If `scope` is a knowledge-base directory (has
  `INDEX.md`), it adds free frontmatter/links/staleness checks.

Every tool returns a **compact manifest** (JSON), never the full code or
intermediate results — reading it is enough, no need to re-read the files
it changed unless the manifest itself flags a failure to diagnose.

**If the project already has a markdown knowledge base** (a folder with
`INDEX.md` + one file per topic with frontmatter — the same convention as
your own memory), pass `update_docs=True, kb_path="path/to/kb"` to
`team_feature`/`team_epic` so that, after a successful change, existing KB
files that went stale get updated (it never creates new entries on its
own). The knowledge base can be a folder in the project itself or a
dedicated repo elsewhere in the organization — in that second case, its
local clone needs to be in `TEAM_SANDBOX_ROOTS`. Full detail in
`docs/KB_CONVENTION.md` in the MCP's repo.

If something fails with a connection error: the gateway is a self-hosted
service and may be down. Diagnose with
`python -m team_mcp.cli probe --provider gateway` from the repo. Consider
scheduling a periodic `team_validate(selftest=True)` (e.g. weekly) so
tier degradation gets caught before it blocks real work.
