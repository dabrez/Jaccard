"""
Embedding providers.

Two backends: OpenAI's hosted API, and a local Ollama server. Ollama needs
no API key and sends no issue text off the machine, which matters when
sweeping a private tracker.

Select with EMBEDDING_PROVIDER=openai|ollama in .env.

Vector dimensions differ per model (text-embedding-3-small is 1536,
nomic-embed-text is 768), and the schema is built around whatever the
configured provider reports, so switching providers means re-embedding
from scratch into a fresh DB.
"""
import os

import httpx

# Model -> dimensions. Declared rather than probed so init_db can build the
# vec0 table without a network call at import time.
_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "nomic-embed-text": 768,
    "nomic-embed-text:latest": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
}

DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

# Embedding an issue body can be slow on a local model with a cold cache.
OLLAMA_TIMEOUT = 120.0

_openai_client = None


def provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()


def model_name() -> str:
    if provider() == "ollama":
        return os.getenv("EMBEDDING_MODEL", DEFAULT_OLLAMA_MODEL)
    return os.getenv("EMBEDDING_MODEL", DEFAULT_OPENAI_MODEL)


def dimensions() -> int:
    """
    Vector width for the configured model, used to build the vec0 table.

    Unknown models must be declared via EMBEDDING_DIMENSIONS rather than
    guessed: a wrong width makes every insert fail on a dimension mismatch.
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


def _embed_openai(text: str) -> list[float]:
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = _openai_client.embeddings.create(
        model=model_name(), input=text
    )
    return response.data[0].embedding


def _embed_ollama(text: str) -> list[float]:
    base = os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
    try:
        r = httpx.post(
            f"{base}/api/embed",
            json={"model": model_name(), "input": text},
            timeout=OLLAMA_TIMEOUT,
        )
        r.raise_for_status()
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {base}. Is `ollama serve` running?"
        ) from exc

    payload = r.json()
    embeddings = payload.get("embeddings")
    if not embeddings:
        # A model that isn't pulled yet reports an error here rather than
        # failing the request outright.
        raise RuntimeError(
            f"Ollama returned no embedding for model {model_name()!r}: "
            f"{payload.get('error', payload)}"
        )
    return embeddings[0]


def embed_text(text: str) -> list[float]:
    name = provider()
    if name == "ollama":
        return _embed_ollama(text)
    if name == "openai":
        return _embed_openai(text)
    raise ValueError(
        f"Unknown EMBEDDING_PROVIDER {name!r}; expected 'openai' or 'ollama'"
    )


def embed_issue(title: str, body: str) -> list[float]:
    """Embed an issue's title and body as a single vector."""
    return embed_text(f"{title}\n\n{body or ''}".strip())
