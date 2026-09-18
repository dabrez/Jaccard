"""
Sweep an existing issue backlog for duplicate clusters.

GitHub's own duplicate detection runs at issue-creation time, so it cannot
help with issues already in the tracker. Large projects carry thousands:
pytorch/pytorch has ~14k open, rust-lang/rust ~11k. This walks that existing
backlog and groups issues into clusters of likely duplicates.

Reports only. Nothing here posts comments, applies labels, or closes issues;
confirmation stays with a maintainer via `/duplicate of #N`.

Usage:
  python scripts/sweep.py owner/repo
  python scripts/sweep.py owner/repo --limit 500 --threshold 0.85
  python scripts/sweep.py owner/repo --format markdown > report.md
  python scripts/sweep.py owner/repo --format csv > clusters.csv

Reads GITHUB_TOKEN from .env. Embeddings come from a local Ollama
server, so no API key is involved.
"""
import argparse
import asyncio
import csv
import os
import struct
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

# How many neighbours to consider per issue when building clusters. Clusters
# grow by transitive merging, so this caps work per issue, not cluster size.
NEIGHBOURS_PER_ISSUE = 10


class _Clusters:
    """
    Union-find over issue numbers.

    Duplicates are transitive in practice: if A matches B and B matches C,
    all three describe one problem even when A and C score below the
    threshold against each other. Grouping beats reporting loose pairs,
    which is what a maintainer would otherwise have to assemble by hand.
    """

    def __init__(self):
        self._parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Keep the lower issue number as the root: the oldest issue is
            # the natural canonical candidate.
            lo, hi = sorted((ra, rb))
            self._parent[hi] = lo

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for number in self._parent:
            out.setdefault(self.find(number), []).append(number)
        return {root: sorted(members) for root, members in out.items()
                if len(members) > 1}


def _stored_numbers(repo: str) -> set[int]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT i.issue_number
        FROM issues i
        JOIN issue_embeddings e ON e.issue_id = i.id
        WHERE i.repo = ?
    """, (repo,)).fetchall()
    conn.close()
    return {r["issue_number"] for r in rows}


def embed_backlog(repo: str, issues: list[dict], fresh: bool):
    skip = set() if fresh else _stored_numbers(repo)
    todo = [i for i in issues if i["number"] not in skip]

    if skip and not fresh:
        print(f"  {len(skip)} already embedded")
    if not todo:
        print("  Nothing new to embed")
        return

    print(f"  Embedding {len(todo)} issues locally via {model_name()}")
    for n, issue in enumerate(todo, 1):
        issue_id = upsert_issue(
            repo, issue["number"], issue["title"], issue.get("body") or "",
            "open", issue["created_at"],
        )
        upsert_embedding(
            issue_id, embed_issue(issue["title"], issue.get("body") or "")
        )
        if n % 50 == 0 or n == len(todo):
            print(f"  [{n}/{len(todo)}]")
        if n % 200 == 0:
            time.sleep(1)


def _load_vectors(repo: str) -> dict[int, tuple[int, list[float]]]:
    """Map issue number -> (row id, stored embedding)."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT i.id, i.issue_number, e.embedding
        FROM issues i
        JOIN issue_embeddings e ON e.issue_id = i.id
        WHERE i.repo = ? AND i.state = 'open'
    """, (repo,)).fetchall()
    conn.close()
    return {
        r["issue_number"]: (
            r["id"],
            list(struct.unpack(f"{len(r['embedding']) // 4}f", r["embedding"])),
        )
        for r in rows
    }


def build_clusters(repo: str, threshold: float):
    vectors = _load_vectors(repo)
    titles = _titles(repo)
    clusters = _Clusters()
    # Best score seen for each merged pair, for reporting confidence.
    pair_scores: dict[tuple[int, int], float] = {}

    for number, (issue_id, vector) in vectors.items():
        for cand in find_similar(repo, vector,
                                 top_k=NEIGHBOURS_PER_ISSUE,
                                 exclude_issue_id=issue_id):
            sim = distance_to_similarity(cand["distance"])
            if sim < threshold:
                continue
            other = cand["issue_number"]
            clusters.union(number, other)
            key = tuple(sorted((number, other)))
            pair_scores[key] = max(pair_scores.get(key, 0.0), sim)

    groups = clusters.groups()
    # Strongest clusters first: biggest, then highest-scoring.
    ordered = sorted(
        groups.items(),
        key=lambda kv: (len(kv[1]), max(
            (s for p, s in pair_scores.items()
             if p[0] in kv[1] and p[1] in kv[1]), default=0.0)),
        reverse=True,
    )
    return ordered, pair_scores, titles, len(vectors)


def _titles(repo: str) -> dict[int, str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT issue_number, title FROM issues WHERE repo = ?", (repo,)
    ).fetchall()
    conn.close()
    return {r["issue_number"]: r["title"] for r in rows}


def _cluster_confidence(members, pair_scores) -> float:
    scores = [s for p, s in pair_scores.items()
              if p[0] in members and p[1] in members]
    return max(scores) if scores else 0.0


def report_text(repo, ordered, pair_scores, titles, scanned, threshold):
    print(f"\n{'=' * 72}")
    print(f"DUPLICATE CLUSTERS — {repo}")
    print(f"{scanned} open issues scanned at threshold "
          f"{format_percent(threshold)}")
    print("=" * 72)

    if not ordered:
        print("\nNo clusters found. Try a lower --threshold.")
        return

    for root, members in ordered:
        conf = _cluster_confidence(members, pair_scores)
        print(f"\n[{len(members)} issues, up to {format_percent(conf)} "
              f"similar]  suggested canonical: #{root}")
        for m in members:
            marker = "*" if m == root else " "
            print(f"  {marker} #{m}: {titles.get(m, '?')[:64]}")

    total = sum(len(m) for _, m in ordered)
    print(f"\n{'=' * 72}")
    print(f"{len(ordered)} clusters covering {total} issues "
          f"({total - len(ordered)} candidates for closure)")
    print("Confirm any pair with:  /duplicate of #N")
    print("=" * 72)


def report_markdown(repo, ordered, pair_scores, titles, scanned, threshold):
    print(f"# Duplicate clusters — {repo}\n")
    print(f"{scanned} open issues scanned at a "
          f"{format_percent(threshold)} similarity threshold.\n")
    if not ordered:
        print("No clusters found.")
        return
    total = sum(len(m) for _, m in ordered)
    print(f"Found **{len(ordered)} clusters** covering **{total} issues** — "
          f"{total - len(ordered)} are candidates for closure.\n")
    for root, members in ordered:
        conf = _cluster_confidence(members, pair_scores)
        print(f"### #{root} — {len(members)} issues "
              f"(up to {format_percent(conf)} similar)\n")
        for m in members:
            note = " *(suggested canonical)*" if m == root else ""
            print(f"- #{m}: {titles.get(m, '?')}{note}")
        print()


def report_csv(repo, ordered, pair_scores, titles, scanned, threshold):
    w = csv.writer(sys.stdout)
    w.writerow(["cluster_canonical", "issue_number", "title",
                "cluster_size", "cluster_confidence"])
    for root, members in ordered:
        conf = _cluster_confidence(members, pair_scores)
        for m in members:
            w.writerow([root, m, titles.get(m, ""), len(members),
                        f"{conf:.4f}"])


async def main(repo, limit, threshold, fresh, fmt):
    init_db()
    print(f"Fetching open issues for {repo}...")
    issues = await fetch_issues(repo, state="open", limit=limit)
    print(f"  {len(issues)} open issues")
    if len(issues) < 2:
        print("Need at least 2 issues to find duplicates.")
        return

    embed_backlog(repo, issues, fresh)
    print("Clustering...")
    ordered, pair_scores, titles, scanned = build_clusters(repo, threshold)

    {"text": report_text, "markdown": report_markdown,
     "csv": report_csv}[fmt](
        repo, ordered, pair_scores, titles, scanned, threshold)


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Cluster an existing backlog by duplicate similarity. "
                    "Read-only: never writes to GitHub.")
    p.add_argument("repo", help="owner/repo")
    p.add_argument("--limit", type=int, default=None,
                   help="scan at most N issues")
    p.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD,
                   help=f"similarity cutoff (default {SIMILARITY_THRESHOLD})")
    p.add_argument("--fresh", action="store_true",
                   help="re-embed even if vectors are stored")
    p.add_argument("--format", choices=["text", "markdown", "csv"],
                   default="text")
    args = p.parse_args()

    os.environ.setdefault("DB_PATH", "sweep.db")
    if args.format == "text":
        print(f"Using DB: {os.environ['DB_PATH']}")

    if not os.getenv("GITHUB_TOKEN"):
        print("GITHUB_TOKEN is not set. Try:")
        print("  GITHUB_TOKEN=$(gh auth token) python scripts/sweep.py ...")
        sys.exit(1)

    asyncio.run(main(args.repo, args.limit, args.threshold, args.fresh,
                     args.format))
