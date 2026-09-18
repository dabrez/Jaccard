"""
Regression tests for the sqlite-vec query layer.

These cover the failure modes the original implementation hit: a plain
ORDER BY that never computes a distance, ON CONFLICT against a virtual
table, and KNN filters that apply after the neighbour search.
"""
import importlib

import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import app.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    yield db_module


def _vec(*values) -> list[float]:
    """
    Pad a few leading values out to the configured model's width.

    Derived rather than hardcoded: the schema is built from the embedding
    model, so a fixed width here breaks whenever the model changes.
    """
    from app.embeddings import dimensions
    width = dimensions()
    return list(values) + [0.0] * (width - len(values))


def _add(db, repo, number, title, embedding, state="open"):
    issue_id = db.upsert_issue(repo, number, title, "", state, "2024-01-01")
    db.upsert_embedding(issue_id, embedding)
    return issue_id


def test_find_similar_returns_usable_distances(temp_db):
    """A plain scan leaves distance NULL; only a MATCH query computes it."""
    _add(temp_db, "a/b", 1, "Dark mode", _vec(1, 0, 0))

    results = temp_db.find_similar("a/b", _vec(1, 0, 0), top_k=5)

    assert len(results) == 1
    assert results[0]["distance"] is not None
    assert results[0]["distance"] == pytest.approx(0.0, abs=1e-5)


def test_find_similar_orders_by_closeness(temp_db):
    _add(temp_db, "a/b", 1, "Near", _vec(0.9, 0.1, 0))
    _add(temp_db, "a/b", 2, "Far", _vec(0, 0, 1))

    results = temp_db.find_similar("a/b", _vec(1, 0, 0), top_k=5)

    assert [r["issue_number"] for r in results] == [1, 2]


def test_find_similar_excludes_other_repos(temp_db):
    _add(temp_db, "other/repo", 1, "Exact match elsewhere", _vec(1, 0, 0))
    _add(temp_db, "a/b", 2, "Ours", _vec(0.8, 0.2, 0))

    results = temp_db.find_similar("a/b", _vec(1, 0, 0), top_k=5)

    assert [r["issue_number"] for r in results] == [2]


def test_find_similar_excludes_closed_issues(temp_db):
    _add(temp_db, "a/b", 1, "Closed", _vec(1, 0, 0), state="closed")
    _add(temp_db, "a/b", 2, "Open", _vec(0.8, 0.2, 0))

    results = temp_db.find_similar("a/b", _vec(1, 0, 0), top_k=5)

    assert [r["issue_number"] for r in results] == [2]


def test_find_similar_excludes_the_query_issue(temp_db):
    own_id = _add(temp_db, "a/b", 1, "Self", _vec(1, 0, 0))
    _add(temp_db, "a/b", 2, "Other", _vec(0.8, 0.2, 0))

    results = temp_db.find_similar(
        "a/b", _vec(1, 0, 0), top_k=5, exclude_issue_id=own_id
    )

    assert [r["issue_number"] for r in results] == [2]


def test_nearest_neighbours_in_other_repos_do_not_crowd_out_results(temp_db):
    """
    vec0 filters after KNN. With 60 closer issues in another repo and no
    over-fetch, the target repo's own issues fall outside the k window and
    the query returns nothing.
    """
    for n in range(100, 160):
        _add(temp_db, "noisy/repo", n, f"Noise {n}", _vec(1, 0, 0))
    _add(temp_db, "a/b", 1, "Ours", _vec(0.9, 0.1, 0))

    results = temp_db.find_similar("a/b", _vec(1, 0, 0), top_k=5)

    assert [r["issue_number"] for r in results] == [1]


def test_find_similar_respects_top_k(temp_db):
    for n in range(1, 11):
        _add(temp_db, "a/b", n, f"Issue {n}", _vec(1 - n * 0.01, n * 0.01, 0))

    results = temp_db.find_similar("a/b", _vec(1, 0, 0), top_k=3)

    assert len(results) == 3


def test_find_similar_with_no_issues(temp_db):
    assert temp_db.find_similar("a/b", _vec(1, 0, 0), top_k=5) == []


def test_reembedding_replaces_the_stored_vector(temp_db):
    """vec0 rejects ON CONFLICT, so re-embedding must delete then insert."""
    issue_id = _add(temp_db, "a/b", 1, "Original", _vec(1, 0, 0))

    temp_db.upsert_embedding(issue_id, _vec(0, 1, 0))

    conn = temp_db.get_conn()
    count = conn.execute(
        "SELECT count(*) FROM issue_embeddings WHERE issue_id = ?", (issue_id,)
    ).fetchone()[0]
    conn.close()
    assert count == 1

    # The new vector should now be the closer match.
    results = temp_db.find_similar("a/b", _vec(0, 1, 0), top_k=1)
    assert results[0]["distance"] == pytest.approx(0.0, abs=1e-5)
