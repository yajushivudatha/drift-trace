"""Local sentence embeddings, so retrieval has no per-call cost."""

import asyncio
import logging
from collections.abc import Callable

from drifttrace.config import get_settings

log = logging.getLogger(__name__)

Embedder = Callable[[list[str]], list[list[float]]]
_override: Embedder | None = None
_model = None


def set_embedder(embedder: Embedder | None) -> None:
    """Replace the model. Used only by the test suite."""
    global _override
    _override = embedder


def _encode(texts: list[str]) -> list[list[float]]:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        settings = get_settings()
        log.info("Loading embedding model %s", settings.embedding_model)
        _model = SentenceTransformer(settings.embedding_model, device="cpu")
    vectors = _model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


async def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return await asyncio.to_thread(_override or _encode, texts)
