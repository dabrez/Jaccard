import os
from openai import OpenAI

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def embed_issue(title: str, body: str) -> list[float]:
    """Embed a GitHub issue's title + body into a 1536-dim vector."""
    text = f"{title}\n\n{body or ''}".strip()
    response = _get_client().embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return response.data[0].embedding
