from __future__ import annotations

import hashlib
import json
import math
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest
import tiktoken
from pydantic import ValidationError

from core.embedder import FastEmbedBackend, validate_vectors
from core.engine import Condenser
from core.models import ChatMessage, CondenseRequest, MemoryPackage


class FakeEmbeddings:
    model_name = "fake-384"

    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [3.0] + [0.0] * 383
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [self.vector[:] for _ in texts]


def _request(*messages: tuple[str, str], budget: int = 128) -> CondenseRequest:
    return CondenseRequest(
        messages=[ChatMessage(role=role, content=content) for role, content in messages],
        summary_token_budget=budget,
    )


def test_condense_builds_deterministic_extract_and_normalized_chunks() -> None:
    backend = FakeEmbeddings()
    condenser = Condenser(backend)
    request = _request(
        ("user", "My project is called Cedar. Keep the launch date: 2026-05-04."),
        ("assistant", "I will use Cedar and remember the launch date."),
        ("user", "The launch date is 2026-05-04."),
        budget=64,
    )
    session_id = "12345678-1234-5678-1234-567812345678"

    package, chunks = condenser.condense(request, session_id)
    duplicate, duplicate_chunks = condenser.condense(request, session_id)

    encoding = tiktoken.get_encoding("cl100k_base")
    assert package.summary_tokens == duplicate.summary_tokens
    assert package.token_count == len(encoding.encode(package.summary_tokens))
    assert package.token_count <= 64
    assert package.original_token_count == sum(
        len(encoding.encode(message.content)) for message in request.messages
    )
    expected_hash = hashlib.sha256(
        json.dumps(
            [{"role": m.role, "content": m.content} for m in request.messages],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert package.content_sha256 == expected_hash
    assert "Cedar" in package.summary_tokens
    assert package.content_sha256 == duplicate.content_sha256
    assert [chunk.id for chunk in chunks] == [chunk.id for chunk in duplicate_chunks]
    assert all(UUID(chunk.id) for chunk in chunks)
    assert package.vector_state.point_ids == [chunk.id for chunk in chunks]
    assert package.vector_state.embedding_model == "fake-384"
    assert all(math.isclose(sum(v * v for v in chunk.vector), 1.0) for chunk in chunks)
    assert "untrusted session data" in package.instructions
    assert len(backend.calls) == 2


def test_request_rejects_blank_content_and_excess_bytes() -> None:
    with pytest.raises(ValidationError):
        _request(("user", " \n\t "))
    with pytest.raises(ValidationError):
        _request(("user", "x" * (256 * 1024 + 1)))


def test_request_accepts_exact_utf8_byte_limit() -> None:
    request = _request(("user", "é" * (128 * 1024)))
    assert len(request.messages[0].content.encode("utf-8")) == 256 * 1024


def test_request_enforces_message_count_and_budget_bounds() -> None:
    with pytest.raises(ValidationError):
        CondenseRequest(messages=[])
    with pytest.raises(ValidationError):
        _request(("user", "hello"), budget=63)
    with pytest.raises(ValidationError):
        _request(("user", "hello"), budget=4097)
    with pytest.raises(ValidationError):
        CondenseRequest(messages=[ChatMessage(role="user", content="ok") for _ in range(1001)])


@pytest.mark.parametrize(
    "vector",
    [
        [1.0] * 383,
        [0.0] * 384,
        [float("nan")] + [0.0] * 383,
        [float("inf")] + [0.0] * 383,
    ],
)
def test_condense_rejects_invalid_embedding_vectors(vector: list[float]) -> None:
    with pytest.raises(ValueError):
        Condenser(FakeEmbeddings(vector)).condense(
            _request(("user", "remember this")), "12345678-1234-5678-1234-567812345678"
        )


def test_condense_rejects_wrong_embedding_count() -> None:
    class WrongCountBackend:
        def embed(self, texts: list[str]) -> list[list[float]]:
            return []

    with pytest.raises(ValueError, match="incorrect vector count"):
        Condenser(WrongCountBackend()).condense(
            _request(("user", "remember this")), "12345678-1234-5678-1234-567812345678"
        )


def test_validate_vectors_checks_count_and_normalizes() -> None:
    vectors = validate_vectors([[0.0, 4.0] + [0.0] * 382], 1)

    assert vectors[0] == [0.0, 1.0] + [0.0] * 382
    with pytest.raises(ValueError, match="incorrect vector count"):
        validate_vectors([], 1)


def test_fastembed_is_lazy_and_uses_configured_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str | None]] = []

    class FakeModel:
        def __init__(self, **kwargs: str | None) -> None:
            calls.append(kwargs)

        def embed(self, texts: list[str]) -> list[SimpleNamespace]:
            return [SimpleNamespace(tolist=lambda: [1.0] * 384) for _ in texts]

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", r"/opt/fastembed")
    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeModel))
    backend = FastEmbedBackend()

    assert backend._model is None
    backend.embed(["query"])
    assert calls == [{"model_name": "BAAI/bge-small-en-v1.5", "cache_dir": r"/opt/fastembed"}]


def test_chunks_are_token_bounded_and_cover_recent_messages() -> None:
    recent = "The final decision is project-quartz-2026."
    request = _request(
        ("user", " ".join(f"earlyfact{i}" for i in range(800))),
        ("assistant", " ".join(f"response{i}" for i in range(800))),
        ("user", recent),
        budget=64,
    )
    package, chunks = Condenser(FakeEmbeddings()).condense(
        request, "12345678-1234-5678-1234-567812345678"
    )
    encoding = tiktoken.get_encoding("cl100k_base")

    assert chunks
    assert all(len(encoding.encode(chunk.text)) <= 256 for chunk in chunks)
    assert recent in package.summary_tokens
    combined = "\n".join(chunk.text for chunk in chunks)
    assert "earlyfact0" in combined
    assert "response799" in combined
    assert recent in combined


def test_package_rejects_non_utc_timestamp_and_non_uuid_session() -> None:
    with pytest.raises(ValidationError):
        MemoryPackage(
            format="emp",
            version=1,
            session_id="12345678-1234-5678-1234-567812345678",
            created_at="2026-01-01T00:00:00+01:00",
            summary_tokens="",
            token_count=0,
            original_token_count=0,
            instructions="",
            vector_state={
                "collection": "c",
                "embedding_model": "m",
                "dimensions": 384,
                "point_ids": [],
                "query_hints": [],
            },
            content_sha256="0" * 64,
        )


def test_package_rejects_malformed_transcript_digest() -> None:
    with pytest.raises(ValidationError, match="lowercase SHA-256"):
        MemoryPackage(
            format="emp",
            version=1,
            session_id="12345678-1234-5678-1234-567812345678",
            created_at="2026-01-01T00:00:00Z",
            summary_tokens="",
            token_count=0,
            original_token_count=0,
            instructions="",
            vector_state={
                "collection": "c",
                "embedding_model": "m",
                "dimensions": 384,
                "point_ids": [],
                "query_hints": [],
            },
            content_sha256="not-a-digest",
        )
