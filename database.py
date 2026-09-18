import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("jaccard.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                last_synced_at TEXT,
                issue_count INTEGER DEFAULT 0,
                UNIQUE(owner, name)
            );

            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY,
                repo_id INTEGER NOT NULL,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                state TEXT NOT NULL,
                html_url TEXT NOT NULL,
                labels TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (repo_id) REFERENCES repos(id),
                UNIQUE(repo_id, number)
            );
        """)


def add_repo(owner: str, name: str) -> dict:
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO repos (owner, name) VALUES (?, ?)", (owner, name)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        row = conn.execute(
            "SELECT * FROM repos WHERE owner = ? AND name = ?", (owner, name)
        ).fetchone()
        return dict(row)


def get_repos() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM repos ORDER BY owner, name").fetchall()
        return [dict(r) for r in rows]


def delete_repo(repo_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM issues WHERE repo_id = ?", (repo_id,))
        conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
        conn.commit()


def upsert_issues(repo_id: int, issues: list[dict]):
    with get_conn() as conn:
        for issue in issues:
            conn.execute(
                """
                INSERT INTO issues (id, repo_id, number, title, body, state, html_url, labels, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id, number) DO UPDATE SET
                    title = excluded.title,
                    body = excluded.body,
                    state = excluded.state,
                    labels = excluded.labels
                """,
                (
                    issue["id"],
                    repo_id,
                    issue["number"],
                    issue["title"],
                    issue.get("body") or "",
                    issue["state"],
                    issue["html_url"],
                    json.dumps([l["name"] for l in issue.get("labels", [])]),
                    issue["created_at"],
                ),
            )
        now = datetime.now(timezone.utc).isoformat()
        count = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE repo_id = ?", (repo_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE repos SET last_synced_at = ?, issue_count = ? WHERE id = ?",
            (now, count, repo_id),
        )
        conn.commit()


def get_issues(repo_id: int | None = None, state: str = "all") -> list[dict]:
    query = """
        SELECT i.*, r.owner, r.name AS repo_name
        FROM issues i JOIN repos r ON i.repo_id = r.id
    """
    params: list = []
    conditions: list[str] = []
    if repo_id is not None:
        conditions.append("i.repo_id = ?")
        params.append(repo_id)
    if state != "all":
        conditions.append("i.state = ?")
        params.append(state)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY i.created_at DESC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["labels"] = json.loads(d["labels"])
            result.append(d)
        return result
