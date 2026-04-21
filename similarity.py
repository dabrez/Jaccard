import re
from collections import defaultdict

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "him", "his", "how",
    "i", "if", "in", "into", "is", "it", "its", "me", "my", "no", "not",
    "of", "on", "or", "our", "out", "re", "so", "some", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "up", "was", "we", "were", "what", "when", "where",
    "which", "who", "will", "with", "would", "you", "your",
}


def tokenize(text: str) -> set[str]:
    text = text.lower()
    tokens = re.findall(r"\b[a-z0-9][a-z0-9_.-]*\b", text)
    return {t for t in tokens if t not in STOP_WORDS and len(t) > 1}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def search_issues(query: str, issues: list[dict], top_k: int = 20) -> list[dict]:
    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    scored = []
    for issue in issues:
        tokens = tokenize(f"{issue['title']} {issue.get('body') or ''}")
        score = jaccard(q_tokens, tokens)
        if score > 0:
            scored.append({**issue, "score": round(score, 4)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def group_issues(issues: list[dict], threshold: float = 0.2) -> list[dict]:
    n = len(issues)
    if n == 0:
        return []

    token_sets = [
        tokenize(f"{i['title']} {i.get('body') or ''}") for i in issues
    ]

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int):
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            if jaccard(token_sets[i], token_sets[j]) >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    result = []
    for indices in groups.values():
        group_issue_list = [issues[i] for i in indices]
        if len(indices) > 1:
            common = set.intersection(*[token_sets[i] for i in indices])
        else:
            common = set()
        result.append({
            "issues": group_issue_list,
            "common_tokens": sorted(common)[:10],
        })

    result.sort(key=lambda x: len(x["issues"]), reverse=True)
    return result
