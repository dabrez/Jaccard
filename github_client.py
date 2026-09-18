import os

import httpx

GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def verify_repo(owner: str, repo: str) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repo}",
            headers=_headers(),
        )
        return r.status_code == 200


async def fetch_issues(owner: str, repo: str) -> list[dict]:
    issues: list[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            r = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/issues",
                headers=_headers(),
                params={"state": "all", "per_page": 100, "page": page},
            )
            if r.status_code == 404:
                raise ValueError(f"Repository {owner}/{repo} not found")
            if r.status_code == 403:
                raise ValueError(
                    "GitHub API rate limit exceeded. Set GITHUB_TOKEN to increase limits."
                )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            # GitHub issues endpoint returns PRs too — exclude them
            issues.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                break
            page += 1
    return issues
