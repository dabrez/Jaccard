import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Header
from dotenv import load_dotenv

load_dotenv()

from app.db import (
    init_db, upsert_issue, upsert_embedding, find_similar,
    save_duplicate_pair, get_canonical_for, get_duplicates_of,
    mark_issue_closed,
)
from app.embeddings import embed_issue
from app.similarity import filter_candidates
from app.github import (
    verify_webhook_signature, post_comment, add_label,
    close_issue,
)
from app.commands import parse_comment, DuplicateCommand, NotDuplicateCommand

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

DUPLICATE_LABEL = "possible-duplicate"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Jaccard", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
):
    payload = await request.body()

    if not verify_webhook_signature(payload, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if x_github_event not in ("issues", "issue_comment"):
        return {"ignored": True}

    data = await request.json()
    action = data.get("action")
    repo = data["repository"]["full_name"]

    if x_github_event == "issues":
        if action == "opened":
            asyncio.create_task(_handle_issue_opened(repo, data["issue"]))
        elif action == "closed":
            asyncio.create_task(_handle_issue_closed(repo, data["issue"]))
        elif action in ("edited", "reopened"):
            asyncio.create_task(_handle_issue_upsert(repo, data["issue"]))

    elif x_github_event == "issue_comment":
        if action == "created":
            asyncio.create_task(_handle_comment(repo, data))

    return {"received": True}


async def _handle_issue_opened(repo: str, issue: dict):
    number = issue["number"]
    title = issue["title"]
    body = issue.get("body") or ""
    created_at = issue["created_at"]

    log.info(f"[{repo}] New issue #{number}: {title!r}")

    # Store the issue
    issue_id = upsert_issue(repo, number, title, body, "open", created_at)

    # Embed and store
    embedding = embed_issue(title, body)
    upsert_embedding(issue_id, embedding)

    # Find similar open issues
    similar_raw = find_similar(repo, embedding, top_k=5,
                               exclude_issue_id=issue_id)
    candidates = filter_candidates(similar_raw)

    if not candidates:
        log.info(f"[{repo}] No similar issues found for #{number}")
        return

    # Post a comment on the new issue
    lines = [
        "**Possible duplicate issues detected** — a maintainer can confirm with `/duplicate of #N` or dismiss with `/not-duplicate`.\n",
    ]
    for c in candidates:
        lines.append(
            f"- #{c['issue_number']} ({c['similarity_pct']} similar): "
            f"_{c['title']}_"
        )
    lines.append(
        "\n_Powered by [Jaccard](https://github.com/dabrez/Jaccard)_"
    )
    await post_comment(repo, number, "\n".join(lines))
    await add_label(repo, number, DUPLICATE_LABEL)

    log.info(
        f"[{repo}] Flagged #{number} with {len(candidates)} candidate(s)"
    )


async def _handle_issue_upsert(repo: str, issue: dict):
    """Re-embed an issue when its title/body is edited or it's reopened."""
    number = issue["number"]
    title = issue["title"]
    body = issue.get("body") or ""
    state = issue["state"]
    created_at = issue["created_at"]

    issue_id = upsert_issue(repo, number, title, body, state, created_at)
    embedding = embed_issue(title, body)
    upsert_embedding(issue_id, embedding)
    log.info(f"[{repo}] Re-embedded #{number} (action: {state})")


async def _handle_issue_closed(repo: str, issue: dict):
    number = issue["number"]
    mark_issue_closed(repo, number)
    log.info(f"[{repo}] Issue #{number} closed")

    # If this is a canonical issue, close all its confirmed duplicates too
    duplicates = get_duplicates_of(repo, number)
    if duplicates:
        log.info(
            f"[{repo}] Cascading close to duplicates: {duplicates}"
        )
        for dup_number in duplicates:
            await post_comment(
                repo, dup_number,
                f"Closing as duplicate — canonical issue #{number} was closed."
            )
            await close_issue(repo, dup_number)
            mark_issue_closed(repo, dup_number)


async def _handle_comment(repo: str, data: dict):
    comment_body = data["comment"]["body"]
    issue_number = data["issue"]["number"]
    commenter = data["comment"]["user"]["login"]

    command = parse_comment(comment_body)
    if command is None:
        return

    log.info(
        f"[{repo}] Command on #{issue_number} by @{commenter}: {command}"
    )

    if isinstance(command, DuplicateCommand):
        canonical = command.canonical_number
        duplicate = issue_number

        # Don't allow self-referential pairs
        if canonical == duplicate:
            await post_comment(
                repo, duplicate,
                "An issue cannot be a duplicate of itself."
            )
            return

        save_duplicate_pair(repo, canonical, duplicate)

        await post_comment(
            repo, duplicate,
            f"Confirmed duplicate of #{canonical}. "
            f"This issue will be closed when #{canonical} is closed."
        )
        await add_label(repo, duplicate, "duplicate")
        await post_comment(
            repo, canonical,
            f"Issue #{duplicate} has been confirmed as a duplicate of this issue."
        )

    elif isinstance(command, NotDuplicateCommand):
        # Just acknowledge — maintainer is clearing the suggestion
        await post_comment(
            repo, issue_number,
            "Understood — not a duplicate. The `possible-duplicate` label can be removed manually."
        )
