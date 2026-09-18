import sqlite3
import sqlite_vec
import struct
import os
from pathlib import Path

from app.embeddings import dimensions

DB_PATH = os.getenv("DB_PATH", "jaccard.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS issues (
            id          INTEGER PRIMARY KEY,
            repo        TEXT NOT NULL,
            issue_number INTEGER NOT NULL,
            title       TEXT NOT NULL,
            body        TEXT,
            state       TEXT NOT NULL DEFAULT 'open',
            created_at  TEXT,
            -- Unused until the GitHub App conversion; present so existing
            -- embeddings don't need regenerating at that point.
            installation_id INTEGER,
            UNIQUE(repo, issue_number)
        );

        CREATE TABLE IF NOT EXISTS duplicate_pairs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            repo            TEXT NOT NULL,
            canonical_number INTEGER NOT NULL,
            duplicate_number INTEGER NOT NULL,
            confirmed_at    TEXT,
            installation_id INTEGER,
            UNIQUE(repo, canonical_number, duplicate_number)
        );

        CREATE INDEX IF NOT EXISTS idx_issues_repo_state
            ON issues(repo, state);
    """)

    # Vector width follows the configured embedding model, so the table is
    # built per provider rather than fixed at OpenAI's 1536.
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS issue_embeddings
        USING vec0(
            issue_id INTEGER PRIMARY KEY,
            embedding FLOAT[{dimensions()}]
        )
    """)
    conn.commit()
    conn.close()


def serialize_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def upsert_issue(repo: str, issue_number: int, title: str, body: str,
                 state: str, created_at: str) -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO issues (repo, issue_number, title, body, state, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo, issue_number) DO UPDATE SET
            title=excluded.title,
            body=excluded.body,
            state=excluded.state
    """, (repo, issue_number, title, body or "", state, created_at))
    issue_id = cur.lastrowid
    # On conflict, lastrowid is 0 — fetch it explicitly
    if issue_id == 0:
        row = conn.execute(
            "SELECT id FROM issues WHERE repo=? AND issue_number=?",
            (repo, issue_number)
        ).fetchone()
        issue_id = row["id"]
    conn.commit()
    conn.close()
    return issue_id


def upsert_embedding(issue_id: int, embedding: list[float]):
    # vec0 virtual tables don't support ON CONFLICT ("UPSERT not implemented
    # for virtual table"), so replace the row explicitly.
    conn = get_conn()
    conn.execute("DELETE FROM issue_embeddings WHERE issue_id = ?", (issue_id,))
    conn.execute("""
        INSERT INTO issue_embeddings (issue_id, embedding)
        VALUES (?, ?)
    """, (issue_id, serialize_embedding(embedding)))
    conn.commit()
    conn.close()


# vec0 applies the repo/state filters AFTER running KNN, so asking for exactly
# top_k neighbours can come back short (or empty) once another repo's issues are
# filtered out. No fixed over-fetch is safe: enough unrelated issues will always
# crowd the window. Instead widen k until we have top_k survivors or we've
# scanned everything stored.
KNN_INITIAL_K = 50
KNN_GROWTH = 4


def find_similar(repo: str, embedding: list[float], top_k: int = 5,
                 exclude_issue_id: int | None = None) -> list[dict]:
    conn = get_conn()
    vec_bytes = serialize_embedding(embedding)
    total = conn.execute("SELECT count(*) FROM issue_embeddings").fetchone()[0]

    k = max(KNN_INITIAL_K, top_k)
    rows: list = []
    while True:
        k = min(k, total) if total else k
        rows = conn.execute("""
            SELECT
                i.issue_number,
                i.title,
                i.state,
                e.distance
            FROM issue_embeddings e
            JOIN issues i ON i.id = e.issue_id
            WHERE e.embedding MATCH ?
              AND k = ?
              AND i.repo = ?
              AND i.state = 'open'
              AND e.issue_id != ?
            ORDER BY e.distance
            LIMIT ?
        """, (vec_bytes, k, repo, exclude_issue_id or -1, top_k)).fetchall()

        # Enough survivors, or we've already searched every stored vector.
        if len(rows) >= top_k or k >= total:
            break
        k *= KNN_GROWTH

    conn.close()
    return [dict(r) for r in rows]


def save_duplicate_pair(repo: str, canonical: int, duplicate: int):
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO duplicate_pairs
            (repo, canonical_number, duplicate_number, confirmed_at)
        VALUES (?, ?, ?, datetime('now'))
    """, (repo, canonical, duplicate))
    conn.commit()
    conn.close()


def get_canonical_for(repo: str, issue_number: int) -> int | None:
    """If this issue is a duplicate, return its canonical issue number."""
    conn = get_conn()
    row = conn.execute("""
        SELECT canonical_number FROM duplicate_pairs
        WHERE repo=? AND duplicate_number=?
    """, (repo, issue_number)).fetchone()
    conn.close()
    return row["canonical_number"] if row else None


def get_duplicate_chain(repo: str, canonical_number: int,
                        open_only: bool = True) -> list[int]:
    """
    Return every issue that resolves to this canonical one, following chains.

    Confirmations arrive one pair at a time, so #3 may be a duplicate of #2
    while #2 is a duplicate of #1. Closing #1 should reach #3 too, which a
    single-level lookup misses.

    Maintainers can also confirm pairs in both directions, producing cycles.
    The recursive CTE tracks visited numbers in a path string so a cycle
    terminates instead of recursing forever.
    """
    conn = get_conn()
    rows = conn.execute("""
        WITH RECURSIVE chain(number, path) AS (
            SELECT ?, ',' || ? || ','
            UNION
            SELECT p.duplicate_number,
                   c.path || p.duplicate_number || ','
            FROM duplicate_pairs p
            JOIN chain c ON p.canonical_number = c.number
            WHERE p.repo = ?
              AND c.path NOT LIKE '%,' || p.duplicate_number || ',%'
        )
        SELECT DISTINCT c.number
        FROM chain c
        JOIN issues i
          ON i.repo = ? AND i.issue_number = c.number
        WHERE c.number != ?
          AND (? = 1 OR i.state = 'open')
        ORDER BY c.number
    """, (canonical_number, canonical_number, repo, repo,
          canonical_number, 0 if open_only else 1)).fetchall()
    conn.close()
    return [r["number"] for r in rows]


def get_duplicates_of(repo: str, canonical_number: int) -> list[int]:
    """Direct duplicates only. Prefer get_duplicate_chain for cascades."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT duplicate_number FROM duplicate_pairs
        WHERE repo=? AND canonical_number=?
    """, (repo, canonical_number)).fetchall()
    conn.close()
    return [r["duplicate_number"] for r in rows]


def mark_issue_closed(repo: str, issue_number: int):
    conn = get_conn()
    conn.execute("""
        UPDATE issues SET state='closed'
        WHERE repo=? AND issue_number=?
    """, (repo, issue_number))
    conn.commit()
    conn.close()
