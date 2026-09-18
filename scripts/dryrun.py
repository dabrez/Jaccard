"""
Score a repo's issues for duplicates without writing anything to GitHub.

Fetches open issues, embeds them, and reports the pairs Jaccard would flag
so the similarity threshold can be checked against real data before the
bot ever comments on anything.

Usage:
  python scripts/dryrun.py owner/repo
  python scripts/dryrun.py owner/repo --limit 200
  python scripts/dryrun.py owner/repo --show-near-misses

Reads GITHUB_TOKEN from .env. Embeddings come from a local Ollama server,
so no API key is involved. Writes to a scratch DB (default dryrun.db) so a
repeat run reuses stored vectors; pass --fresh to re-embed.

Nothing in here posts comments, adds labels, or closes issues.
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

load_dotenv()

from app.db import (  # noqa: E402
    init_db, upsert_issue, upsert_embedding, find_similar, get_conn,
)
from app.embeddings import embed_issue, model_name  # noqa: E402
from app.github import fetch_issues  # noqa: E402
from app.similarity import (  # noqa: E402
    SIMILARITY_THRESHOLD, distance_to_similarity, format_percent,
)

# Pairs below the threshold but above this are worth eyeballing: they show
# whether the cutoff is sitting in a sensible place.
NEAR_MISS_FLOOR = 0.70


def _already_embedded(repo: str) -> set[int]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT i.issue_number
        FROM issues i
        JOIN issue_embeddings e ON e.issue_id = i.id
        WHERE i.repo = ?
    """, (repo,)).fetchall()
    conn.close()
    return {r["issue_number"] for r in rows}


async def embed_all(repo: str, issues: list[dict], skip: set[int]):
    todo = [i for i in issues if i["number"] not in skip]
    if skip:
        print(f"  {len(skip)} already embedded, skipping")
    if not todo:
        print("  Nothing new to embed.")
        return

    print(f"  Embedding {len(todo)} issues locally via {model_name()}")

    for n, issue in enumerate(todo, 1):
        issue_id = upsert_issue(
            repo, issue["number"], issue["title"], issue.get("body") or "",
            "open", issue["created_at"],
        )
        upsert_embedding(issue_id, embed_issue(
            issue["title"], issue.get("body") or ""
        ))
        if n % 25 == 0 or n == len(todo):
            print(f"  [{n}/{len(todo)}]")



def score_pairs(repo: str, issues: list[dict], show_near_misses: bool):
    """Report what the bot would flag, plus near misses for calibration."""
    conn = get_conn()
    id_by_number = {
        r["issue_number"]: r["id"]
        for r in conn.execute(
            "SELECT id, issue_number FROM issues WHERE repo = ?", (repo,)
        ).fetchall()
    }
    conn.close()

    flagged: list[tuple] = []
    near: list[tuple] = []
    seen: set[tuple] = set()

    for issue in issues:
        number = issue["number"]
        issue_id = id_by_number.get(number)
        if issue_id is None:
            continue

        # Read the stored vector back rather than re-embedding.
        conn = get_conn()
        row = conn.execute(
            "SELECT embedding FROM issue_embeddings WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
        conn.close()
        if row is None:
            continue

        import struct
        stored = list(struct.unpack(f"{len(row['embedding']) // 4}f",
                                    row["embedding"]))

        for cand in find_similar(repo, stored, top_k=5,
                                 exclude_issue_id=issue_id):
            other = cand["issue_number"]
            key = tuple(sorted((number, other)))
            if key in seen:
                continue
            seen.add(key)

            sim = distance_to_similarity(cand["distance"])
            entry = (sim, number, issue["title"], other, cand["title"])
            if sim >= SIMILARITY_THRESHOLD:
                flagged.append(entry)
            elif sim >= NEAR_MISS_FLOOR:
                near.append(entry)

    flagged.sort(reverse=True)
    near.sort(reverse=True)

    print(f"\n{'=' * 70}")
    print(f"WOULD FLAG  ({len(flagged)} pairs at threshold "
          f"{format_percent(SIMILARITY_THRESHOLD)})")
    print("=" * 70)
    if not flagged:
        print("  none")
    for sim, a, a_title, b, b_title in flagged:
        print(f"\n  {format_percent(sim)}  #{a} <-> #{b}")
        print(f"        #{a}: {a_title[:62]}")
        print(f"        #{b}: {b_title[:62]}")

    if show_near_misses:
        print(f"\n{'=' * 70}")
        print(f"NEAR MISSES  ({len(near)} pairs between "
              f"{format_percent(NEAR_MISS_FLOOR)} and "
              f"{format_percent(SIMILARITY_THRESHOLD)})")
        print("=" * 70)
        if not near:
            print("  none")
        for sim, a, a_title, b, b_title in near:
            print(f"\n  {format_percent(sim)}  #{a} <-> #{b}")
            print(f"        #{a}: {a_title[:62]}")
            print(f"        #{b}: {b_title[:62]}")

    print(f"\n{'=' * 70}")
    print(f"{len(issues)} issues scored. "
          f"{len(flagged)} would be commented on, "
          f"{len(near)} just below the line.")
    print("Raise the threshold if the flagged pairs look wrong; lower it if "
          "the near misses look like real duplicates.")
    print("=" * 70)


async def main(repo: str, limit: int | None, fresh: bool,
               show_near_misses: bool):
    init_db()

    print(f"Fetching open issues for {repo}...")
    issues = await fetch_issues(repo, state="open", limit=limit)
    print(f"  {len(issues)} open issues")

    if not issues:
        print("Nothing to score.")
        return
    if len(issues) < 2:
        print("Need at least 2 issues to find a duplicate pair.")
        return

    skip = set() if fresh else _already_embedded(repo)
    await embed_all(repo, issues, skip)
    score_pairs(repo, issues, show_near_misses)


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Dry-run duplicate scoring. Never writes to GitHub.")
    p.add_argument("repo", help="owner/repo")
    p.add_argument("--limit", type=int, default=None,
                   help="score at most N issues (keeps embedding cost down)")
    p.add_argument("--fresh", action="store_true",
                   help="re-embed even if vectors are already stored")
    p.add_argument("--show-near-misses", action="store_true",
                   help="also list pairs just below the threshold")
    args = p.parse_args()

    # Keep experiments out of the service's real database.
    os.environ.setdefault("DB_PATH", "dryrun.db")
    print(f"Using DB: {os.environ['DB_PATH']}")

    if not os.getenv("GITHUB_TOKEN"):
        print("\nGITHUB_TOKEN is not set. For public repos you can use:")
        print("  GITHUB_TOKEN=$(gh auth token) python scripts/dryrun.py ...")
        sys.exit(1)

    asyncio.run(main(args.repo, args.limit, args.fresh,
                     args.show_near_misses))
