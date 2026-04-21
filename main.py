from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db
import github_client as gh
import similarity as sim

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Jaccard", lifespan=lifespan)


class RepoRequest(BaseModel):
    owner: str
    name: str


@app.get("/api/repos")
def list_repos():
    return db.get_repos()


@app.post("/api/repos")
async def add_repo(body: RepoRequest):
    owner = body.owner.strip().lower()
    name = body.name.strip().lower()
    if not owner or not name:
        raise HTTPException(400, "owner and name are required")
    if not await gh.verify_repo(owner, name):
        raise HTTPException(404, f"Repository {owner}/{name} not found on GitHub")
    return db.add_repo(owner, name)


@app.delete("/api/repos/{repo_id}")
def remove_repo(repo_id: int):
    db.delete_repo(repo_id)
    return {"ok": True}


@app.post("/api/repos/{repo_id}/sync")
async def sync_repo(repo_id: int):
    repos = db.get_repos()
    repo = next((r for r in repos if r["id"] == repo_id), None)
    if not repo:
        raise HTTPException(404, "Repo not found")
    try:
        issues = await gh.fetch_issues(repo["owner"], repo["name"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.upsert_issues(repo_id, issues)
    return {"synced": len(issues)}


@app.get("/api/issues/search")
def search(
    q: str = Query(..., min_length=1),
    repo_id: Optional[int] = None,
    state: str = "all",
):
    issues = db.get_issues(repo_id=repo_id, state=state)
    return sim.search_issues(q, issues)


@app.get("/api/issues/groups")
def groups(
    repo_id: Optional[int] = None,
    threshold: float = Query(0.2, ge=0.0, le=1.0),
    state: str = "open",
):
    issues = db.get_issues(repo_id=repo_id, state=state)
    return sim.group_issues(issues, threshold=threshold)


# Must be last — catches all unmatched paths for the SPA
app.mount("/", StaticFiles(directory="static", html=True), name="static")
