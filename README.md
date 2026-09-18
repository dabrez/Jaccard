# Jaccard

Finds and manages duplicate GitHub issues using embedding similarity.

Jaccard sweeps an existing issue backlog for clusters of likely duplicates,
lets a maintainer confirm them with a comment command, and then keeps the
confirmed relationships maintained — when a canonical issue closes, its
duplicates close with it.

## Project trajectory

This started as a creation-time duplicate detector: a webhook service that
embedded each new issue and commented with similar ones.

In June 2026, partway through building it, GitHub
[shipped that feature natively](https://github.blog/changelog/2026-06-18-duplicate-detection-and-issue-fields-mcp-support-for-github-issues/) —
duplicate suggestions now appear inline in the issue creation form, before
an issue is ever filed. GitHub also runs an official
[Similar Issues AI](https://github.com/apps/similar-issues-ai) app that
comments with similar issues on creation. No install, no API key, no hosting,
and structurally earlier in the flow than a webhook can reach.

That's the price of working at the edge: sometimes the platform ships your
feature first. So the project moved to the two halves GitHub's feature
doesn't cover.

**Existing backlogs.** GitHub checks at creation time, which does nothing for
issues already filed. Those backlogs are large — pytorch/pytorch carries
~14,000 open issues, rust-lang/rust ~11,000. Nobody is deduplicating them.

**Confirmed relationships over time.** Detection is a one-shot suggestion.
Once a maintainer confirms "#412 is a duplicate of #89", that link is worth
keeping: closing #89 should close #412. Surveying the field, existing tools
either auto-close aggressively or only suggest and forget; a reviewed,
persistent duplicate graph was the gap.

## Two implementations

The repo currently holds two approaches to the same problem, with no shared
code between them.

**Token similarity + web UI** (`main.py`, `similarity.py`, `static/`) — the
implementation the project is named for. Computes Jaccard similarity over
tokenized issue text: no embeddings, no API keys, no model. Exposes a REST
API with a browser frontend for searching and grouping issues. Fast, free,
and good at catching duplicates that reuse the same wording.

**Semantic embeddings** (`app/`, `scripts/`) — embeds issues and compares
them by vector distance, so it matches duplicates that share no vocabulary
("connection pooling" against "pgbouncer"). Runs as a webhook service plus
a backlog sweep tool. Needs either an OpenAI key or a local Ollama model.

They are complementary rather than redundant, and unifying them is open
work. Run `uvicorn main:app` for the search UI, `uvicorn app.main:app` for
the webhook service.

## What the embedding pipeline does

**Backlog sweep** (`scripts/sweep.py`) — walks a repo's open issues, embeds
them, and groups them into duplicate clusters. Duplicates are treated as
transitive: if A matches B and B matches C, all three are reported as one
cluster even when A and C score below the threshold against each other. The
oldest issue in each cluster is suggested as canonical. Read-only.

**Confirmation commands** — a maintainer comments `/duplicate of #89` to
record a link, or `/not-duplicate` to dismiss a suggestion.

**Cascade close** — when a canonical issue closes, Jaccard closes the issues
confirmed as its duplicates. Confirmation chains are followed transitively
(#3 → #2 → #1 closes all three) and confirmation cycles terminate safely.

**Creation-time detection** — still present in the webhook service, now
largely redundant with GitHub's native feature. Useful if you want the
similarity scores recorded, or the `/duplicate` graph fed automatically.

## Setup

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env
```

## Embedding provider

Either OpenAI's hosted API or a local Ollama model. Local costs nothing and
keeps issue text on your machine, which matters for a private tracker.

```bash
# local (no API key)
ollama pull nomic-embed-text
# then in .env:
#   EMBEDDING_PROVIDER=ollama
#   EMBEDDING_MODEL=nomic-embed-text

# hosted
#   EMBEDDING_PROVIDER=openai
#   OPENAI_API_KEY=sk-...
```

**Thresholds do not transfer between models.** Measured on real issue
titles, `nomic-embed-text` scored a genuine duplicate pair at 0.639 and
unrelated pairs around 0.41, so OpenAI's 0.82 cutoff would miss every
duplicate it finds. Defaults are per provider (OpenAI 0.82, Ollama 0.62);
calibrate for your repo with `scripts/dryrun.py --show-near-misses`.

Vector width also differs (1536 for `text-embedding-3-small`, 768 for
`nomic-embed-text`) and the schema is built from the configured model, so
switching providers means re-embedding into a fresh database.

A note on model choice: `qwen3-embedding` performed badly here, scoring an
unrelated pair (0.547) above a genuinely related one (0.454). Qwen3
embedding models expect a task-specific instruction prefix, and without it
the output is unreliable for this kind of comparison.

## Sweeping a backlog

```bash
# Scan 500 issues, show clusters
GITHUB_TOKEN=$(gh auth token) venv/bin/python scripts/sweep.py \
    microsoft/autogen --limit 500

# Tune the cutoff, export for review
venv/bin/python scripts/sweep.py microsoft/autogen --threshold 0.85
venv/bin/python scripts/sweep.py microsoft/autogen --format csv > clusters.csv
venv/bin/python scripts/sweep.py microsoft/autogen --format markdown > report.md
```

Embeddings are cached in `sweep.db`, so re-running only embeds new issues.
Cost runs about a cent per 500 issues on `text-embedding-3-small`.

To check where the similarity threshold should sit for a given repo,
`scripts/dryrun.py` reports flagged pairs alongside near misses just below
the cutoff.

## Running the service

```bash
venv/bin/python scripts/init_repo.py owner/repo      # backfill embeddings
venv/bin/uvicorn app.main:app --reload               # serve /webhook
```

Point a repo webhook at `/webhook` for `issues` and `issue_comment` events,
with `GITHUB_WEBHOOK_SECRET` set to the same secret.

## Layout

| Path | Purpose |
| --- | --- |
| `main.py` | Search API + web UI (token similarity) |
| `similarity.py` | Jaccard token similarity |
| `database.py` | SQLite store for the search API |
| `github_client.py` | GitHub client for the search API |
| `static/` | Web frontend |
| `app/main.py` | Webhook handlers, cascade close |
| `app/db.py` | SQLite schema, vector search, duplicate-chain resolution |
| `app/embeddings.py` | Embedding providers (OpenAI, Ollama) |
| `app/similarity.py` | Embedding distance-to-similarity, thresholding |
| `app/github.py` | GitHub REST client |
| `app/commands.py` | `/duplicate` and `/not-duplicate` parsing |
| `scripts/sweep.py` | Backlog clustering |
| `scripts/dryrun.py` | Threshold calibration |
| `scripts/init_repo.py` | One-time embedding backfill |

Storage is SQLite with [sqlite-vec](https://github.com/asg017/sqlite-vec) for
vector search, so there's no external vector database to run. With Ollama as
the provider, the whole pipeline runs locally with no API keys.

## Tests

```bash
venv/bin/python -m pytest
```

## Status

Verified by tests, against the live GitHub API, and end-to-end with local
Ollama embeddings. Not yet exercised: the OpenAI embedding path, and the
webhook service running against a real repo.

Known gaps:

- Single-tenant. Comments post as the owner of `GITHUB_TOKEN` rather than a
  bot, and each repo needs its own webhook configured by hand. A GitHub App
  conversion would fix both.
- `/not-duplicate` acknowledges but does not suppress, so a dismissed pair
  can be flagged again.
- `SIMILARITY_THRESHOLD` (0.82) is untuned; calibrate per repo with
  `scripts/dryrun.py`.
- Confirmation applies a `duplicate` label, which most large repos do not
  use — they rely on GitHub's native duplicate state reason instead.
