from app.similarity import distance_to_similarity, filter_candidates, SIMILARITY_THRESHOLD


def test_zero_distance_is_perfect_similarity():
    assert distance_to_similarity(0.0) == 1.0


def test_high_distance_clamps_to_zero():
    assert distance_to_similarity(10.0) == 0.0


def test_typical_similar_distance():
    # L2 distance of ~0.3 between normalized vectors → high similarity
    sim = distance_to_similarity(0.3)
    assert sim > 0.9


def test_filter_keeps_above_threshold():
    candidates = [
        {"issue_number": 1, "title": "Add dark mode", "distance": 0.1},
        {"issue_number": 2, "title": "Dark theme support", "distance": 0.2},
        {"issue_number": 3, "title": "Unrelated issue", "distance": 1.5},
    ]
    results = filter_candidates(candidates)
    numbers = [r["issue_number"] for r in results]
    assert 1 in numbers
    assert 2 in numbers
    assert 3 not in numbers


def test_filter_adds_similarity_pct():
    candidates = [
        {"issue_number": 5, "title": "Test", "distance": 0.1},
    ]
    results = filter_candidates(candidates)
    assert "similarity_pct" in results[0]
    assert "%" in results[0]["similarity_pct"]


def test_filter_empty_input():
    assert filter_candidates([]) == []
