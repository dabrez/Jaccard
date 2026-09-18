import os
import hmac
import hashlib
import httpx

GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    token = os.environ["GITHUB_TOKEN"]
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify GitHub's X-Hub-Signature-256 header."""
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return True  # Skip verification in dev if secret not set
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


async def fetch_issues(repo: str, state: str = "open",
                       per_page: int = 100) -> list[dict]:
    """Fetch all issues for a repo (paginated). Excludes pull requests."""
    issues = []
    page = 1
    async with httpx.AsyncClient() as client:
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
            # GitHub returns PRs in the issues endpoint — filter them out
            issues.extend(i for i in batch if "pull_request" not in i)
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
