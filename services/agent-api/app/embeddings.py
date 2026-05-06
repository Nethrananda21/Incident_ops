from __future__ import annotations

import hashlib
import logging
import math
import re
from functools import lru_cache

from app.config import get_settings

TOKEN_RE = re.compile(r"[a-z0-9_./:-]+", re.I)
LOGGER = logging.getLogger(__name__)


@lru_cache
def _semantic_model():
    settings = get_settings()
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(settings.embedding_model)
    except Exception as exc:
        LOGGER.warning("semantic embedding model unavailable, using lexical fallback: %s", exc)
        return None


def embed_text(text: str, dim: int | None = None) -> list[float]:
    settings = get_settings()
    model = _semantic_model()
    if model is not None:
        vector = model.encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [float(value) for value in vector.tolist()]
    return lexical_embed_text(text, dim or settings.embedding_dim)


def lexical_embed_text(text: str, dim: int = 384) -> list[float]:
    vector = [0.0] * dim
    for token in TOKEN_RE.findall(text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[idx] += sign
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))
