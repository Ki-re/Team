<p align="center">
  <img src="docs/logo.svg" width="88" height="88" alt="Team logo">
</p>

<h1 align="center">Team</h1>

<p align="center">
  An MCP server that lets a coding agent (Claude, Codex, Hermes, ...) delegate real implementation work to a farm of free/cheap LLMs.
</p>

<p align="center">
  <a href="https://github.com/Ki-re/Team/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/Ki-re/Team/actions/workflows/tests.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
</p>

A coding agent acts **only** as the orchestrator: it plans and delegates.
All the actual implementation — writing code, analyzing logs, reviewing
changes — runs on a farm of free or cheap models behind a self-hosted
LiteLLM gateway, through this local MCP server. Claude (Desktop or Code)
is the tested, documented orchestrator (see [Register the MCP
server](#4-register-the-mcp-server-with-claude-code) below), but nothing
about the design is Claude-specific: any agent that can speak MCP —
Codex, Hermes, or anything else with its own registration step — plans
and delegates the exact same way.

The interesting part isn't "connect some cheap models" — small models
produce mediocre code on their own. The value is in the *process* wrapped
around them: N-way generation, deterministic verification, cross-validation
consensus, adversarial critique, and bounded repair loops. Quality comes
from the pipeline, not from any single model.

See [CHANGELOG.md](CHANGELOG.md) for the version history.

## Why LiteLLM

The MCP server never talks to a specific provider's SDK directly — it only
ever speaks one API shape (OpenAI-compatible `/v1/chat/completions`) to
one local endpoint, the LiteLLM gateway. LiteLLM's job is to sit between
that one endpoint and however many real providers/models you actually
want behind it, each registered under one of the four logical tier names
(`tier-fast`, `tier-coder`, `tier-context`, `tier-premium`).

That indirection is what makes running on free or very cheap models
practical instead of a maintenance burden:

- **Swapping or adding a model is a config change, not a code change.**
  Every tier in [deploy/litellm.config.yaml](deploy/litellm.config.yaml)
  can hold several entries from different providers and families — round-
  robin between free-tier API keys, cascading fallbacks when one is
  exhausted or degraded, and diversity that the cross-validation consensus
  matrix in [`engine/consensus.py`](src/team_mcp/engine/consensus.py)
  actively benefits from (two correlated models producing the same wrong
  answer is worse than two independent models disagreeing).
- **Strong, freely-usable coder models exist today** — DeepSeek, Qwen
  Coder, GLM, and others regularly show up with $0 pricing on OpenRouter's
  catalog (subject to change, see the table and disclaimer below); this
  project's own `tier-coder` pool has run on exactly that kind of model.
  Nothing stops you from pointing a tier at a paid model instead if you'd
  rather trade quota headroom for money.
- **Self-hosted models plug into the same four tiers, with no code
  changes.** LiteLLM talks to self-hosted, OpenAI-compatible endpoints
  (vLLM, Ollama, LM Studio, text-generation-webui, and similar) the same
  way it talks to a cloud provider — just another `model_list` entry
  pointing at your own URL instead of a hosted API. That means the farm
  scales horizontally in a very literal sense: add a GPU box running vLLM
  with a large open-weight model, register it under `tier-coder`, and it
  participates in the fan-out and consensus matrix exactly like any other
  worker, free-tier or not.

### Providers with free-tier models (the ones this project uses)

These are the providers actually wired up in
[deploy/litellm.config.yaml](deploy/litellm.config.yaml) and
[deploy/.env.example](deploy/.env.example) today — not an exhaustive
market survey, just what's verified working in this setup.

| Provider | Used for | What's free | Notes |
|---|---|---|---|
| [Groq](https://console.groq.com) | `tier-fast` | Generous free-tier rate limits on hosted open-weight models (e.g. `gpt-oss-20b`, Qwen) | Very low latency; good fit for small, atomic tasks. |
| [OpenRouter](https://openrouter.ai) | `tier-coder`, `tier-context` | A large, changing catalog of `:free`-suffixed models (DeepSeek, Qwen Coder, GLM, and others come and go) — including `openrouter/openrouter/free`, OpenRouter's own router that spreads calls across its free catalog at random | Check [openrouter.ai/models](https://openrouter.ai/models?max_price=0) for what's currently free — this list is the most volatile of the four. |
| [Google AI Studio](https://aistudio.google.com) (Gemini API) | `tier-context`, `tier-premium` fallback | Free-tier quota on Flash-class Gemini models, with large context windows | Pro-class models often show 0 quota on a fresh key even though they're listed — that's usually a billing/entitlement gap, not a broken model name. |
| [Mistral](https://console.mistral.ai) (La Plateforme) | `tier-coder` | A free "Experiment" tier with rate-limited access to models like Codestral, intended for evaluation/non-commercial use | Read Mistral's current terms before relying on this for anything beyond experimentation. |

### Disclaimer

Free tiers, rate limits, and pricing change often and without notice —
providers rename, retire, or start charging for models regularly. The
table above reflects what worked while this project was built and is not
a guarantee of current availability, pricing, or terms of service for any
provider. Verify a model's actual status directly against the provider
before depending on it, and read each provider's terms yourself,
especially around commercial use — nothing here is legal or financial
advice about what a given free tier allows.

## Architecture

Hand-authored SVG diagrams (not Mermaid — it didn't render consistently
everywhere). Diagrams for the other workflows (`team_task`, `team_epic`,
`team_ask`, `team_validate`, `docs_sync`) live in
[docs/DIAGRAMS.md](docs/DIAGRAMS.md).

### Components
How the pieces fit together: the orchestrating agent only orchestrates,
`team-mcp` talks to the gateway and to an optional subscription
coding-agent CLI, and every write goes through a sandbox.

<img src="docs/diagrams/architecture.svg" alt="Diagram of Team's components" width="900">

### The `team_feature` pipeline
Fan-out across N workers, a free deterministic gate, cross-validation
consensus, adversarial critique, and a bounded repair loop with escalation
to the premium tier as a last resort — the project's main unit of work.

<img src="docs/diagrams/team_feature_pipeline.svg" alt="team_feature pipeline" width="720">

## The premium tier: why `agy`, and how to swap it

`tier-premium` (adversarial critique, last-resort repair) is invoked two
ways: through the LiteLLM gateway like any other tier (a plain API
fallback), or — the default, preferred path — via a subprocess call to a
**subscription-based coding-agent CLI**, `agy` (Antigravity CLI) out of
the box. The reasoning is entitlement, not capability: a paid coding-agent
subscription is a flat monthly plan, not metered per-token billing, so it
usually has far more real headroom than a shared free-tier API key. It
makes sense to spend that specific capacity on the hardest calls — final
critique, the repair attempt that's already failed a few times — instead
of burning shared free-tier quota on them.

`agy` is a reference implementation, not a requirement. `providers/agy.py`
is a thin, swappable subprocess wrapper: to use a different subscription
CLI — Codex CLI, Claude Code's own `claude -p`, Hermes, or anything else
that takes a prompt on argv and prints a plain-text answer — you don't
need to fork any code in most cases. Two environment variables in `.env`
are enough:

```bash
TEAM_AGY_PATH=/path/to/that/clis/binary
# "{prompt}" is substituted as ONE argv element — never interpolated into
# a shell string, so quotes/newlines in the prompt need no escaping.
TEAM_AGY_CLI_ARGS="exec|{prompt}"              # e.g. Codex CLI
TEAM_AGY_CLI_ARGS="-p|{prompt}|--output-format|text"   # e.g. claude -p
```

Leave `TEAM_AGY_CLI_ARGS` unset to keep `agy`'s own invocation (the
default). If your CLI's output needs real parsing (JSON, a login
handshake, stdin piping) rather than "print text to stdout," that's the
one case that does need a small fork of `providers/agy.py` — the module's
docstring walks through exactly what to change. If you don't want a
subscription CLI in the loop at all, leave `TEAM_AGY_PATH` empty and the
premium tier falls back to the API entries in
`deploy/litellm.config.yaml` automatically — including, per the [Why
LiteLLM](#why-litellm) section above, a self-hosted model if you'd rather
not depend on any external provider for the premium tier either.

## The five tools

The orchestrating agent only ever sees five entry points, graded by task
complexity — each one is a complete pipeline that returns a compact
manifest, never intermediate results or raw generated code:

| Tool | Use it for | Notes |
|---|---|---|
| `team_task` | A small, unambiguous change to one file | No premium tier. Escalates itself to `team_feature` if the deterministic gate fails twice. |
| `team_feature` | The main workhorse | `kind=new` (default), `refactor` (preserves behavior via generated characterization tests), `fix` (requires a `repro_command` that fails today and must exit 0 after the fix — a real test, not one the model invents), or `review` (critique only, no code generated). |
| `team_epic` | A multi-task plan with dependencies | DAG of nodes, executed in parallel topological waves, with a real token budget and clean cutoff. |
| `team_ask` | Questions about code or logs, read-only | Map-reduce with citation verification (`path:line`); `allow_web_search=True` for current external context. |
| `team_validate` | Final GO/NO-GO verdict | Deterministic checks (syntax, tests, secrets, lint) block first, with no model involved; `selftest=True` audits the health of all four tiers instead. |

Optional documentation sync: pass `update_docs=True` and `kb_path=...` to
`team_feature`/`team_epic` and, after a successful change, a small
sub-agent updates any *existing* files in a markdown knowledge base
(frontmatter + `INDEX.md` — see [docs/KB_CONVENTION.md](docs/KB_CONVENTION.md))
that the change made stale. It never invents new entries on its own.
`team_validate` can also audit a knowledge base directory by itself
(broken frontmatter, dangling links, stale entries) without `update_docs`.

## Quickstart

### Prerequisites

- Python 3.11+
- [Docker](https://docs.docker.com/get-docker/) + Docker Compose, on whatever host will run the gateway (your own
  machine, a small VPS, a home server — anything reachable from where
  your coding agent runs)
- API keys for at least a couple of free-tier providers (e.g.
  [Groq](https://console.groq.com), [OpenRouter](https://openrouter.ai))
- [Claude Code](https://claude.com/claude-code) or Claude Desktop

### 1. Clone and install

```bash
git clone https://github.com/Ki-re/Team.git
cd Team
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env
```

### 2. Deploy the gateway

`deploy/` has everything needed to run LiteLLM + Postgres + Redis in
Docker. Deploy it anywhere reachable from your machine:

```bash
cd deploy
cp .env.example .env   # fill in at least one provider's API key
docker compose up -d
```

First boot takes a few minutes (database migrations + registering the
model list) — that's normal, not a hang; `docker logs -f team-litellm`
shows progress. Add or change API keys later either by editing
`deploy/.env` and redeploying, or live from the web UI at
`http://<gateway-host>:4000/ui` with no restart needed
(`store_model_in_db: true` is already set for this).

`deploy/litellm.config.yaml` ships as a working starting template with
two providers per tier — free-tier model availability shifts often, so
treat the exact model list as a starting point, verify what's actually
live for your own keys, and run `selftest` (below) after any change.

### 3. Configure the MCP server

Edit `.env` in the repo root:

```bash
TEAM_GATEWAY_URL=http://<gateway-host>:4000
TEAM_GATEWAY_KEY=<the master key or a virtual key from deploy/.env>
```

### 4. Register the MCP server with Claude Code

`pip install -e .` (step 1) also installs a `team-mcp` entry-point script
inside the venv — point Claude at that directly, no `-m` module path to
get wrong:

```bash
claude mcp add team --scope user -- "/absolute/path/to/Team/.venv/bin/team-mcp"
claude mcp list   # should show "team ... Connected"
```

On Windows, that's `.venv\Scripts\team-mcp.exe`. The path must point at
*this repo's* virtual environment — that's where `team_mcp` is installed
in editable mode. `.env` always loads from this repo's root regardless of
which project Claude has open when it invokes the server.

For Claude Desktop, the equivalent manual entry in its JSON config:

```json
{
  "mcpServers": {
    "team": {
      "command": "/absolute/path/to/Team/.venv/bin/team-mcp"
    }
  }
}
```

### 5. Verify

```bash
curl http://<gateway-host>:4000/health/liveliness
python -m team_mcp.cli run team_validate --selftest
```

`selftest` exercises all four tiers for real and reports which ones are
healthy — the actual source of truth for whether your setup works, not
just whether the gateway process is up.

## Set up with an AI coding agent

If you're already working inside Claude Code (or another coding agent
capable of running shell commands and editing files), you can hand it the
whole setup instead of doing it by hand. Paste this — step 4 below uses
`claude mcp add`, the concrete, tested registration path for Claude Code;
if your agent is something else (Codex CLI, Hermes, etc.), swap that one
line for whatever your agent's own MCP-registration command is, the rest
of the setup is agent-agnostic:

```text
Set up the "team" MCP server for me from https://github.com/Ki-re/Team:

1. Clone the repo, create a Python 3.11+ virtualenv inside it, and run
   `pip install -e ".[dev]"`.
2. Copy .env.example to .env. Ask me for TEAM_GATEWAY_URL and
   TEAM_GATEWAY_KEY once I've deployed the gateway (step 3) — don't guess
   them.
3. Explain that deploy/ contains a docker-compose.yml for the LiteLLM
   gateway, and ask me where I want to run it (this machine, a remote
   server I have SSH access to, something already running). Help me copy
   deploy/.env.example to deploy/.env and fill in at least one provider
   API key, then run `docker compose up -d` there.
4. Once the gateway is reachable, register this MCP server globally:
   `claude mcp add team --scope user -- <path-to-repo>/.venv/<bin-or-Scripts>/team-mcp`
   then confirm with `claude mcp list` that it shows "Connected".
5. Copy skill/SKILL.md from this repo to ~/.claude/skills/team/SKILL.md
   (create the directory if needed) so future sessions discover it
   automatically, and copy skill/CLAUDE.md's contents into my global
   ~/.claude/CLAUDE.md (append, don't overwrite anything already there).
6. Run `python -m team_mcp.cli run team_validate --selftest` and report
   which tiers are healthy.

Ask me for any secret or credential instead of inventing one, and don't
commit .env or deploy/.env anywhere.
```

The referenced `skill/SKILL.md` and `skill/CLAUDE.md` are the same
condensed usage guide Claude Code's own skill system reads — see
[Documentation](#documentation) below for how they fit together.

## Project structure

```
deploy/              docker-compose.yml + litellm.config.yaml for the gateway
skill/                portable skill + CLAUDE.md content for other agents to install
docs/                 diagrams, knowledge-base convention, changelog detail
src/team_mcp/         the MCP server (Python)
  providers/           gateway.py (LiteLLM HTTP) · agy.py (coding-agent CLI) · router.py
  engine/              verify · consensus · critic · repair · schemas · sandbox · ledger · frontmatter
  workflows/           team_task · team_feature · team_epic · team_ask · team_validate · docs_sync
tests/                 pytest suite (deterministic/local logic only — see below)
playground/            scratch files used for manual live verification during development
```

## Testing

```bash
pytest tests/ -q
```

The suite covers deterministic, local logic only — sandbox path
whitelisting, the verification gate, consensus scoring, JSON extraction,
frontmatter parsing, the `team_epic` DAG, `team_validate`'s checks, and so
on. Code paths that call live models (the fan-out itself, critique,
map-reduce) are deliberately not mocked here; they're verified manually
against a real gateway during development, since mocking `httpx` or
spending real API quota on every test run would be the wrong trade-off at
this project's scale.

## Documentation

- [docs/DIAGRAMS.md](docs/DIAGRAMS.md) — diagrams for every workflow.
- [docs/KB_CONVENTION.md](docs/KB_CONVENTION.md) — the markdown
  knowledge-base format `docs_sync` and `team_validate` understand.
- [skill/SKILL.md](skill/SKILL.md) / [skill/CLAUDE.md](skill/CLAUDE.md) —
  the portable usage guide any Claude Code session can install (see the
  agent setup prompt above).
- [CHANGELOG.md](CHANGELOG.md) — version history.

## Contributing

Issues and pull requests are welcome. There's no formal process yet —
open an issue for anything non-trivial before sending a large PR, and
keep the test suite green (`pytest tests/ -q`).

## License

[MIT](LICENSE)
