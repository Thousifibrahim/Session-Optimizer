from __future__ import annotations

import math
import os
from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastembed import TextEmbedding


MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384


def validate_vectors(vectors: list[list[float]], expected_count: int) -> list[list[float]]:
    if expected_count < 0:
        raise ValueError("expected_count must be nonnegative")
    if len(vectors) != expected_count:
        raise ValueError("embedding backend returned an incorrect vector count")

    normalized_vectors: list[list[float]] = []
    for raw_vector in vectors:
        if len(raw_vector) != DIMENSIONS:
            raise ValueError(f"embedding vectors must have {DIMENSIONS} dimensions")
        vector = [float(value) for value in raw_vector]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding vectors must contain only finite values")
        scale = max(abs(value) for value in vector)
        if scale == 0:
            raise ValueError("embedding vectors must be nonzero")
        scaled = [value / scale for value in vector]
        magnitude = math.sqrt(sum(value * value for value in scaled))
        normalized_vectors.append([value / magnitude for value in scaled])
    return normalized_vectors


class EmbeddingBackend(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedBackend:
    model_name = MODEL_NAME

    def __init__(self) -> None:
        self._model: TextEmbedding | None = None
        self._lock = Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"),
                )
            return [vector.tolist() for vector in self._model.embed(texts)]
