import math
import os

# sqlite-vec returns L2 distance for float vectors.
# We convert to a 0–1 similarity score for display.

# Thresholds are not portable between embedding models. Measured on real
# issue titles, nomic-embed-text scored a genuine duplicate pair at 0.639
# and unrelated pairs around 0.41, so 0.62 sits between the two. Switching
# models means re-measuring; calibrate with scripts/dryrun.py.
DEFAULT_THRESHOLD = 0.62


def _threshold() -> float:
    override = os.getenv("SIMILARITY_THRESHOLD")
    return float(override) if override else DEFAULT_THRESHOLD


# Read at import for the module-level constant callers already use; the
# scripts pass an explicit --threshold when calibrating.
SIMILARITY_THRESHOLD = _threshold()


def distance_to_similarity(distance: float) -> float:
    """Convert L2 distance to a 0–1 cosine-like similarity score."""
    # For normalized vectors, L2 distance d and cosine similarity s relate as:
    # s = 1 - (d^2 / 2)
    return max(0.0, 1.0 - (distance ** 2) / 2)


def format_percent(similarity: float) -> str:
    return f"{round(similarity * 100)}%"


def filter_candidates(similar: list[dict]) -> list[dict]:
    """
    Take raw DB results and return only those above the threshold,
    with a human-readable similarity score added.
    """
    results = []
    for row in similar:
        sim = distance_to_similarity(row["distance"])
        if sim >= SIMILARITY_THRESHOLD:
            results.append({
                "issue_number": row["issue_number"],
                "title": row["title"],
                "similarity": sim,
                "similarity_pct": format_percent(sim),
            })
    # Already ordered by distance (ascending = most similar first)
    return results
