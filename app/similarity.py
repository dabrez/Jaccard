import math

# sqlite-vec returns L2 distance for float vectors.
# We convert to a 0–1 similarity score for display.
# Threshold: flag pairs where similarity >= this value.
SIMILARITY_THRESHOLD = 0.82


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
