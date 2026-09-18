"""
One-time script to embed all existing open issues for a repo.

Usage:
  python scripts/init_repo.py owner/repo

Reads OPENAI_API_KEY, GITHUB_TOKEN, and optionally DB_PATH from .env
"""
import asyncio
import sys
import time
import os

# Make sure app/ is importable when running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.db import init_db, upsert_issue, upsert_embedding
from app.embeddings import embed_issue
from app.github import fetch_issues


async def main(repo: str):
    init_db()
    print(f"Fetching open issues for {repo}...")
    issues = await fetch_issues(repo, state="open")
    print(f"Found {len(issues)} issues. Embedding...")

    for i, issue in enumerate(issues, 1):
        number = issue["number"]
        title = issue["title"]
        body = issue.get("body") or ""
        created_at = issue["created_at"]

        issue_id = upsert_issue(
            repo, number, title, body, "open", created_at
        )
        embedding = embed_issue(title, body)
        upsert_embedding(issue_id, embedding)

        print(f"  [{i}/{len(issues)}] #{number}: {title[:60]}")

        # Be polite to the OpenAI rate limiter — ~60 req/min on free tier
        if i % 50 == 0:
            print("  Pausing 60s for rate limits...")
            time.sleep(60)

    print(f"\nDone. {len(issues)} issues embedded into {os.getenv('DB_PATH', 'jaccard.db')}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/init_repo.py owner/repo")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
