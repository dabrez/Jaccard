import sqlite3
import sqlite_vec
import struct
import os
from pathlib import Path

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
            UNIQUE(repo, issue_number)
        );

        CREATE TABLE IF NOT EXISTS duplicate_pairs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            repo            TEXT NOT NULL,
            canonical_number INTEGER NOT NULL,
            duplicate_number INTEGER NOT NULL,
            confirmed_at    TEXT,
            UNIQUE(repo, canonical_number, duplicate_number)
        );
    """)

    # Create the vec table separately (sqlite-vec syntax)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS issue_embeddings
        USING vec0(
            issue_id INTEGER PRIMARY KEY,
            embedding FLOAT[1536]
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
    conn = get_conn()
    conn.execute("""
        INSERT INTO issue_embeddings (issue_id, embedding)
        VALUES (?, ?)
        ON CONFLICT(issue_id) DO UPDATE SET embedding=excluded.embedding
    """, (issue_id, serialize_embedding(embedding)))
    conn.commit()
    conn.close()


def find_similar(repo: str, embedding: list[float], top_k: int = 5,
                 exclude_issue_id: int | None = None) -> list[dict]:
    conn = get_conn()
    vec_bytes = serialize_embedding(embedding)

    rows = conn.execute("""
        SELECT
            i.issue_number,
            i.title,
            i.state,
            e.distance
        FROM issue_embeddings e
        JOIN issues i ON i.id = e.issue_id
        WHERE i.repo = ?
          AND i.state = 'open'
          AND e.issue_id != ?
        ORDER BY e.distance
        LIMIT ?
    """, (repo, exclude_issue_id or -1, top_k)).fetchall()

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


def get_duplicates_of(repo: str, canonical_number: int) -> list[int]:
    """Return all duplicate issue numbers for a canonical issue."""
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
