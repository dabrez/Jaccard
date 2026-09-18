"""
Tests for duplicate-chain resolution, which drives cascade close.

Confirmations arrive one pair at a time and maintainers can confirm in
either direction, so the graph is not guaranteed to be a clean tree.
"""
import importlib

import pytest


@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    yield db_module


def _issues(db, *numbers, state="open", repo="r/r"):
    for n in numbers:
        db.upsert_issue(repo, n, f"Issue {n}", "", state, "2024-01-01")


def test_direct_duplicates(db):
    _issues(db, 1, 2, 3)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=2)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=3)

    assert db.get_duplicate_chain("r/r", 1) == [2, 3]


def test_chain_is_followed_transitively(db):
    """#3 -> #2 -> #1, so closing #1 must reach #3."""
    _issues(db, 1, 2, 3)
    db.save_duplicate_pair("r/r", canonical=2, duplicate=3)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=2)

    assert db.get_duplicate_chain("r/r", 1) == [2, 3]


def test_deep_chain(db):
    _issues(db, *range(1, 7))
    for n in range(1, 6):
        db.save_duplicate_pair("r/r", canonical=n, duplicate=n + 1)

    assert db.get_duplicate_chain("r/r", 1) == [2, 3, 4, 5, 6]


def test_cycle_terminates(db):
    """Two issues confirmed as duplicates of each other must not recurse."""
    _issues(db, 1, 2)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=2)
    db.save_duplicate_pair("r/r", canonical=2, duplicate=1)

    assert db.get_duplicate_chain("r/r", 1) == [2]


def test_longer_cycle_terminates(db):
    _issues(db, 1, 2, 3)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=2)
    db.save_duplicate_pair("r/r", canonical=2, duplicate=3)
    db.save_duplicate_pair("r/r", canonical=3, duplicate=1)

    assert db.get_duplicate_chain("r/r", 1) == [2, 3]


def test_canonical_never_includes_itself(db):
    _issues(db, 1, 2)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=2)

    assert 1 not in db.get_duplicate_chain("r/r", 1)


def test_closed_duplicates_excluded_by_default(db):
    """Avoids re-closing and double-commenting on an already-closed issue."""
    _issues(db, 1, 2)
    db.upsert_issue("r/r", 3, "Issue 3", "", "closed", "2024-01-01")
    db.save_duplicate_pair("r/r", canonical=1, duplicate=2)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=3)

    assert db.get_duplicate_chain("r/r", 1) == [2]
    assert db.get_duplicate_chain("r/r", 1, open_only=False) == [2, 3]


def test_diamond_returns_each_issue_once(db):
    """#2 and #3 both point at #1, and both claim #4."""
    _issues(db, 1, 2, 3, 4)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=2)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=3)
    db.save_duplicate_pair("r/r", canonical=2, duplicate=4)
    db.save_duplicate_pair("r/r", canonical=3, duplicate=4)

    assert db.get_duplicate_chain("r/r", 1) == [2, 3, 4]


def test_other_repos_are_not_traversed(db):
    _issues(db, 1, 2)
    _issues(db, 2, repo="other/repo")
    db.save_duplicate_pair("r/r", canonical=1, duplicate=2)
    db.save_duplicate_pair("other/repo", canonical=1, duplicate=2)

    assert db.get_duplicate_chain("r/r", 1) == [2]


def test_issue_with_no_duplicates(db):
    _issues(db, 1)
    assert db.get_duplicate_chain("r/r", 1) == []


def test_unknown_issue(db):
    assert db.get_duplicate_chain("r/r", 999) == []


def test_pairs_referencing_unknown_issues_are_skipped(db):
    """A pair can name an issue the bot never stored; don't emit it."""
    _issues(db, 1)
    db.save_duplicate_pair("r/r", canonical=1, duplicate=42)

    assert db.get_duplicate_chain("r/r", 1) == []
