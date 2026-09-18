import os
import hmac
import hashlib
import logging

import httpx

GITHUB_API = "https://api.github.com"

log = logging.getLogger(__name__)


def _headers() -> dict:
    token = os.environ["GITHUB_TOKEN"]
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify GitHub's X-Hub-Signature-256 header.

    Fails closed. An unset GITHUB_WEBHOOK_SECRET used to return True, which
    made the insecure path the default: .env ships the secret empty, so a
    deployed service would act on any unsigned request that reached it, and
    this bot comments on and closes issues.

    Running without a secret now requires ALLOW_UNSIGNED_WEBHOOKS=1, so it
    is a deliberate local-development choice rather than an accident.
    """
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        if os.getenv("ALLOW_UNSIGNED_WEBHOOKS", "").strip() == "1":
            log.warning(
                "GITHUB_WEBHOOK_SECRET is unset and ALLOW_UNSIGNED_WEBHOOKS=1: "
                "accepting an unverified webhook. Never do this in production."
            )
            return True
        log.error(
            "Rejecting webhook: GITHUB_WEBHOOK_SECRET is not set. Set it to "
            "the secret configured on the GitHub webhook, or set "
            "ALLOW_UNSIGNED_WEBHOOKS=1 for local development."
        )
        return False

    if not signature:
        log.warning("Rejecting webhook: no X-Hub-Signature-256 header")
        return False

    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def post_comment(repo: str, issue_number: int, body: str):
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json={"body": body}, headers=_headers())
        r.raise_for_status()


async def add_label(repo: str, issue_number: int, label: str):
    """Add a label to an issue, creating it first if it doesn't exist."""
    await _ensure_label_exists(repo, label)
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, json={"labels": [label]}, headers=_headers())
        r.raise_for_status()


async def close_issue(repo: str, issue_number: int):
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}"
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            url,
            json={"state": "closed", "state_reason": "duplicate"},
            headers=_headers(),
        )
        r.raise_for_status()


async def fetch_issues(repo: str, state: str = "open", per_page: int = 100,
                       limit: int | None = None) -> list[dict]:
    """
    Fetch a repo's issues (paginated), excluding pull requests.

    The /issues endpoint returns PRs mixed in, and on PR-heavy repos they
    dominate: microsoft/autogen returns 90 PRs per 100 items. Passing a limit
    stops paging as soon as that many real issues are collected, instead of
    walking every page of PRs first.
    """
    issues: list[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            url = f"{GITHUB_API}/repos/{repo}/issues"
            r = await client.get(
                url,
                params={"state": state, "per_page": per_page, "page": page},
                headers=_headers(),
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            issues.extend(i for i in batch if "pull_request" not in i)
            if limit is not None and len(issues) >= limit:
                return issues[:limit]
            if len(batch) < per_page:
                break
            page += 1
    return issues


async def _ensure_label_exists(repo: str, label: str):
    url = f"{GITHUB_API}/repos/{repo}/labels"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{url}/{label}", headers=_headers()
        )
        if r.status_code == 404:
            await client.post(
                url,
                json={"name": label, "color": "e4e669",
                      "description": "Possible duplicate issue"},
                headers=_headers(),
            )
