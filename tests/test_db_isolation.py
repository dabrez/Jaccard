"""
The two implementations must not share a database file.

Both define an `issues` table with different columns, and both create with
IF NOT EXISTS, so pointing them at one file is silently accepted and then
fails on insert with a missing column.
"""
import importlib
import sqlite3

import pytest


def _reload_app_db(monkeypatch, path):
    monkeypatch.setenv("DB_PATH", str(path))
    import app.db as db_module
    importlib.reload(db_module)
    return db_module


def test_defaults_are_different_files(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("SEARCH_DB_PATH", raising=False)

    import app.db as embedding_db
    import database as search_db
    importlib.reload(embedding_db)
    importlib.reload(search_db)

    assert str(embedding_db.DB_PATH) != str(search_db.DB_PATH)


def test_embedding_db_rejects_a_search_database(monkeypatch, tmp_path):
    """Opening the search API's file must fail loudly, not adopt its schema."""
    path = tmp_path / "search.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE repos (id INTEGER PRIMARY KEY, owner TEXT, name TEXT);
        CREATE TABLE issues (
            id INTEGER PRIMARY KEY,
            repo_id INTEGER NOT NULL,
            number INTEGER NOT NULL,
            title TEXT NOT NULL,
            html_url TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    db_module = _reload_app_db(monkeypatch, path)
    with pytest.raises(RuntimeError, match="search API"):
        db_module.init_db()


def test_search_db_rejects_an_embedding_database(monkeypatch, tmp_path):
    path = tmp_path / "embeddings.db"
    db_module = _reload_app_db(monkeypatch, path)
    db_module.init_db()

    monkeypatch.setenv("SEARCH_DB_PATH", str(path))
    import database as search_db
    importlib.reload(search_db)

    with pytest.raises(RuntimeError, match="embedding pipeline"):
        search_db.init_db()


def test_embedding_db_accepts_its_own_database(monkeypatch, tmp_path):
    path = tmp_path / "own.db"
    db_module = _reload_app_db(monkeypatch, path)

    db_module.init_db()
    db_module.init_db()  # idempotent

    issue_id = db_module.upsert_issue(
        "o/r", 1, "Title", "Body", "open", "2024-01-01"
    )
    assert issue_id > 0


def test_search_db_accepts_its_own_database(monkeypatch, tmp_path):
    monkeypatch.setenv("SEARCH_DB_PATH", str(tmp_path / "own_search.db"))
    import database as search_db
    importlib.reload(search_db)

    search_db.init_db()
    search_db.init_db()  # idempotent

    assert search_db.get_repos() == []


def test_guard_allows_a_fresh_file(monkeypatch, tmp_path):
    """An empty database has no issues table, so neither guard should trip."""
    db_module = _reload_app_db(monkeypatch, tmp_path / "fresh.db")
    db_module.init_db()

    monkeypatch.setenv("SEARCH_DB_PATH", str(tmp_path / "fresh_search.db"))
    import database as search_db
    importlib.reload(search_db)
    search_db.init_db()
