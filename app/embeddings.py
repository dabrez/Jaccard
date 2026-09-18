"""
Embeddings via a local Ollama server.

No API key, no per-issue cost, and issue text never leaves the machine —
which matters when sweeping a private tracker.

    ollama pull nomic-embed-text

Vector width varies by model (nomic-embed-text is 768), and the schema is
built from whatever the configured model reports, so changing models means
re-embedding into a fresh database.
"""
import os

import httpx

# Model -> vector width. Declared rather than probed so init_db can build the
# vec0 table without a network call at import time.
_DIMENSIONS = {
    "nomic-embed-text": 768,
    "nomic-embed-text:latest": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,
    "bge-m3": 1024,
}

DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_URL = "http://localhost:11434"

# A cold model loading into memory can take a while on the first call.
TIMEOUT = 120.0


def model_name() -> str:
    return os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)


def base_url() -> str:
    return os.getenv("OLLAMA_URL", DEFAULT_URL).rstrip("/")


def dimensions() -> int:
    """
    Vector width for the configured model, used to build the vec0 table.

    An unrecognised model must declare EMBEDDING_DIMENSIONS rather than be
    guessed at: a wrong width fails every insert on a dimension mismatch.
    """
    name = model_name()
    if name in _DIMENSIONS:
        return _DIMENSIONS[name]

    override = os.getenv("EMBEDDING_DIMENSIONS")
    if override:
        return int(override)

    raise ValueError(
        f"Unknown embedding model {name!r}. Set EMBEDDING_DIMENSIONS to its "
        f"vector width, or use one of: {', '.join(sorted(_DIMENSIONS))}"
    )


def embed_text(text: str) -> list[float]:
    url = base_url()
    try:
        r = httpx.post(
            f"{url}/api/embed",
            json={"model": model_name(), "input": text},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {url}. Is `ollama serve` running?"
        ) from exc

    payload = r.json()
    embeddings = payload.get("embeddings")
    if not embeddings:
        # A model that hasn't been pulled reports the problem here rather
        # than failing the request outright.
        raise RuntimeError(
            f"Ollama returned no embedding for model {model_name()!r}: "
            f"{payload.get('error', payload)}. "
            f"Try `ollama pull {model_name()}`."
        )
    return embeddings[0]


def embed_issue(title: str, body: str) -> list[float]:
    """Embed an issue's title and body as a single vector."""
    return embed_text(f"{title}\n\n{body or ''}".strip())
