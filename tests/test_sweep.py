"""
Tests for backlog-sweep clustering.

Duplicates are transitive: if A matches B and B matches C, all three
describe one problem even when A and C fall below the threshold against
each other. These cover that merging behaviour.
"""
from scripts.sweep import _Clusters


def test_pair_forms_a_cluster():
    c = _Clusters()
    c.union(1, 2)
    assert c.groups() == {1: [1, 2]}


def test_transitive_merge():
    """A-B and B-C must collapse into one cluster, not two."""
    c = _Clusters()
    c.union(5, 3)
    c.union(3, 9)
    assert c.groups() == {3: [3, 5, 9]}


def test_long_chain_collapses():
    c = _Clusters()
    for a, b in [(1, 2), (2, 3), (3, 4), (4, 5)]:
        c.union(a, b)
    assert c.groups() == {1: [1, 2, 3, 4, 5]}


def test_unrelated_clusters_stay_separate():
    c = _Clusters()
    c.union(1, 2)
    c.union(10, 11)
    assert c.groups() == {1: [1, 2], 10: [10, 11]}


def test_oldest_issue_becomes_canonical():
    """The lowest number is the natural canonical candidate."""
    c = _Clusters()
    c.union(99, 50)
    c.union(50, 7)
    assert list(c.groups()) == [7]


def test_singletons_are_not_reported():
    c = _Clusters()
    c.find(42)
    c.union(1, 2)
    assert c.groups() == {1: [1, 2]}


def test_union_is_idempotent():
    c = _Clusters()
    c.union(1, 2)
    c.union(2, 1)
    c.union(1, 2)
    assert c.groups() == {1: [1, 2]}


def test_no_clusters_when_nothing_matches():
    assert _Clusters().groups() == {}
