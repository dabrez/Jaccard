import os
import pytest
import tempfile

# Use a temp DB for tests
@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    # Re-import db to pick up the new env var
    import importlib
    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    yield db_module


def test_upsert_and_retrieve_issue(temp_db):
    issue_id = temp_db.upsert_issue(
        "owner/repo", 1, "Add dark mode", "Please add dark mode", "open", "2024-01-01"
    )
    assert issue_id > 0


def test_upsert_is_idempotent(temp_db):
    id1 = temp_db.upsert_issue("owner/repo", 1, "Title", "Body", "open", "2024-01-01")
    id2 = temp_db.upsert_issue("owner/repo", 1, "Title updated", "Body", "open", "2024-01-01")
    assert id1 == id2


def test_save_and_retrieve_duplicate_pair(temp_db):
    temp_db.upsert_issue("owner/repo", 1, "Canonical", "", "open", "2024-01-01")
    temp_db.upsert_issue("owner/repo", 2, "Duplicate", "", "open", "2024-01-01")

    temp_db.save_duplicate_pair("owner/repo", canonical=1, duplicate=2)

    canonical = temp_db.get_canonical_for("owner/repo", 2)
    assert canonical == 1

    duplicates = temp_db.get_duplicates_of("owner/repo", 1)
    assert 2 in duplicates


def test_get_canonical_for_nonexistent(temp_db):
    assert temp_db.get_canonical_for("owner/repo", 999) is None


def test_mark_issue_closed(temp_db):
    temp_db.upsert_issue("owner/repo", 5, "Issue", "", "open", "2024-01-01")
    temp_db.mark_issue_closed("owner/repo", 5)

    conn = temp_db.get_conn()
    row = conn.execute(
        "SELECT state FROM issues WHERE repo='owner/repo' AND issue_number=5"
    ).fetchone()
    assert row["state"] == "closed"
