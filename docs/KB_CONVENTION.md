# Knowledge-base convention for `update_docs`

The standard `docs_sync` (the documentation sub-agent used by
`team_feature`/`team_epic`) expects from a `kb_path`. It's not a new
format invented for this project — deliberately, it's the same pattern
Claude Code's own memory system uses: one file per topic with frontmatter,
plus a lightweight index that's always read first. If your knowledge base
already follows this shape (even if you didn't know it by this name),
there's nothing to migrate.

## Why this shape and not another

- **Plain markdown, no vector DB or embeddings.** For the typical size of
  a project knowledge base (tens to hundreds of files), an index +
  frontmatter filters for relevance just as well as semantic search,
  without adding infrastructure (embedding model, vector store) this
  project avoids everywhere on principle.
- **One file per topic, not a single giant document.** Lets `docs_sync`
  (or any Claude session) decide what to read without loading the whole
  knowledge base into context — exactly the problem a lack of a cheap
  index causes: without one, the only option is reading code to
  reconstruct context by hand.
- **`INDEX.md` is the entry point, not a copy of the content.** One line
  per file, not a summary — if the index duplicates the content, the two
  copies drift apart over time.

## Structure

```
my-project/
└─ knowledge-base/       # or whatever name/location you prefer
   ├─ INDEX.md           # required — without this, docs_sync does nothing
   ├─ architecture.md
   ├─ decisions/
   │  └─ why-postgres.md
   └─ adapters/
      └─ portal-x.md
```

The knowledge base can live as a folder inside the project itself, or as
a separate dedicated repo elsewhere in the organization (useful when
several repos share the same knowledge base). For `docs_sync`, `kb_path`
is just a local path — if it's a dedicated repo, its local clone must be
in `TEAM_SANDBOX_ROOTS` (see `.env.example`) or the sandbox will reject
any write there. If the knowledge base lives in its own repo, its changes
sit as an uncommitted working tree in THAT repo — `docs_sync` never
commits on its own, neither there nor in the main project; committing is
left to whoever is orchestrating.

## `INDEX.md`

One line per topic file, same as `MEMORY.md` in Claude's own memory
system:

```markdown
- [System architecture](architecture.md) — high-level view of the system and its components.
- [Why Postgres](decisions/why-postgres.md) — the decision and the alternatives ruled out.
- [Portal X adapter](adapters/portal-x.md) — field mapping and known limitations.
```

## Frontmatter for each topic file

```markdown
---
name: system-architecture
description: High-level view of the system and its main components.
tags: [architecture, overview]
last_verified: 2026-08-27
---

Normal markdown content...
```

- `name` — a short slug, unique within the knowledge base.
- `description` — one sentence; this is what `docs_sync` reads to decide
  relevance WITHOUT opening the whole file.
- `tags` — optional, short list.
- `last_verified` — ISO date (`YYYY-MM-DD`) of the last time a human or a
  Claude session confirmed the content is still correct. `team_validate`
  warns (doesn't block) once it's past ~180 days.

A `.md` file without frontmatter doesn't break anything, but `docs_sync`
won't index it (it won't show up in the cheap summary passed to the
model) and `team_validate` can't evaluate its staleness.

## What `docs_sync` does with this

Two passes, not one — a first version tried asking for the edit directly
against the index (name + description only, no body) and failed on the
first real test: the model has no way to copy an exact `search` string
from text it never saw.

1. **Cheap selection**: reads `INDEX.md` + the frontmatter of every topic
   file (never the full body at this step) and asks `tier-context`, given
   a summary of the change that was just applied, which entries in the
   index likely went stale.
2. **Targeted edit**: for each selected file, a separate call with its
   REAL, full content, asking for an exact search/replace block — the
   same pattern `team_task` uses for code edits, with one retry if the
   `search` doesn't match, feeding the literal error back.
3. Each edit is validated deterministically before being applied: the
   path must resolve inside `kb_path`, the resulting frontmatter must
   still be valid YAML, and no relative link can end up broken.
4. It's applied through the same `Sandbox` as any other edit in the
   project — respects `TEAM_DRY_RUN` and the path whitelist just like
   everything else.

**Scope of this version: it only updates files that are ALREADY in the
index.** It doesn't create new entries — deciding where a new doc should
live and how it should be structured is a judgment call left, for now, to
whoever is orchestrating (Claude, or a human), not to `docs_sync`. If that
needs automating too, it's a future extension, not something this first
cut promises.

## How to enable it

```python
team_feature(
    spec="...",
    target_paths=["..."],
    update_docs=True,
    kb_path="knowledge-base",  # or the absolute path to a dedicated repo
)
```

Same parameter on `team_epic`, but it syncs once at the end of the whole
plan, not per node.

`team_validate(scope="knowledge-base")` audits the knowledge base itself
(frontmatter, dangling links, staleness) without needing `update_docs` —
it works on any directory with `INDEX.md`, whether `docs_sync` has
touched it or not.
