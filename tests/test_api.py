import importlib
import json
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from api.app import MAX_BODY_BYTES, Services, app
from api.settings import Settings
from api.storage import Match
from core.engine import Condenser
from core.models import Chunk, MemoryPackage

app_module = importlib.import_module("api.app")
TOKEN = "test-only-token-with-at-least-32-characters"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PAYLOAD = {
    "messages": [{"role": "user", "content": "Decision: deploy Lambda behind API Gateway."}],
    "summary_token_budget": 64,
}


class Backend:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 383 for _ in texts]


class Packages:
    def __init__(self) -> None:
        self.items: dict[str, MemoryPackage] = {}
        self.fail_put = False
        self.fail_delete = False

    def put(self, package: MemoryPackage) -> None:
        self.items[package.session_id] = package
        if self.fail_put:
            raise RuntimeError("Simulated ambiguous package write")

    def get(self, session_id: str) -> MemoryPackage:
        return self.items[session_id]

    def delete(self, session_id: str) -> None:
        if self.fail_delete:
            raise RuntimeError("Simulated package delete failure")
        self.items.pop(session_id, None)


class Vectors:
    def __init__(self) -> None:
        self.items: dict[str, list[Chunk]] = {}
        self.fail_put = False
        self.fail_delete = False

    def put(self, session_id: str, chunks: list[Chunk]) -> None:
        self.items[session_id] = chunks
        if self.fail_put:
            raise RuntimeError("Simulated ambiguous vector write")

    def search(self, session_id: str, vector: list[float], limit: int) -> list[Match]:
        assert len(vector) == 384
        return [
            Match(point_id=chunk.id, score=1.0, text=chunk.text, index=chunk.index)
            for chunk in self.items.get(session_id, [])[:limit]
        ]

    def delete(self, session_id: str) -> None:
        if self.fail_delete:
            raise RuntimeError("Simulated vector delete failure")
        self.items.pop(session_id, None)


@pytest.fixture
def context(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Packages, Vectors]]:
    packages, vectors = Packages(), Vectors()
    services = Services(Condenser(Backend()), packages, vectors)
    settings = Settings(
        api_token=SecretStr(TOKEN), bucket="test-bucket", qdrant_url="http://localhost:6333"
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    monkeypatch.setattr(app_module, "get_services", lambda: services)
    with TestClient(app) as client:
        yield client, packages, vectors


def test_roundtrip_search_and_delete(context: tuple[TestClient, Packages, Vectors]) -> None:
    client, packages, vectors = context
    created = client.post("/sessions", json=PAYLOAD, headers=HEADERS)
    assert created.status_code == 201, created.text
    assert created.headers["content-disposition"] == 'attachment; filename="memory.emp"'
    assert created.headers["cache-control"] == "no-store"
    package = MemoryPackage.model_validate(created.json())
    session_id = package.session_id
    assert session_id in packages.items and session_id in vectors.items
    fetched = client.get(f"/sessions/{session_id}", headers=HEADERS)
    assert fetched.json() == created.json()
    searched = client.post(
        f"/sessions/{session_id}/search",
        json={"query": "deployment", "limit": 1},
        headers=HEADERS,
    )
    assert searched.status_code == 200
    assert set(searched.json()) == {"session_id", "matches"}
    assert set(searched.json()["matches"][0]) == {"point_id", "score", "text", "index"}
    assert "Lambda" in searched.json()["matches"][0]["text"]
    assert client.delete(f"/sessions/{session_id}", headers=HEADERS).status_code == 204
    assert not packages.items and not vectors.items
    assert client.delete(f"/sessions/{session_id}", headers=HEADERS).status_code == 204
    assert client.get(f"/sessions/{session_id}", headers=HEADERS).status_code == 404


@pytest.mark.parametrize("token", [None, "wrong"])
def test_authentication(context: tuple[TestClient, Packages, Vectors], token: str | None) -> None:
    client, packages, vectors = context
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    assert client.post("/sessions", json=PAYLOAD, headers=headers).status_code == 401
    assert not packages.items and not vectors.items
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/sessions/12345678-1234-5678-1234-567812345678", None),
        (
            "POST",
            "/sessions/12345678-1234-5678-1234-567812345678/search",
            {"query": "decision"},
        ),
        ("DELETE", "/sessions/12345678-1234-5678-1234-567812345678", None),
    ],
)
def test_every_memory_route_requires_authentication(
    context: tuple[TestClient, Packages, Vectors],
    method: str,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    client, packages, vectors = context
    response = client.request(method, path, json=payload)
    assert response.status_code == 401
    assert not packages.items and not vectors.items


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": []},
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "tool", "content": "text"}]},
        {**PAYLOAD, "summary_token_budget": 63},
        {"messages": [{"role": "user", "content": "x" * (256 * 1024 + 1)}]},
    ],
)
def test_input_validation(
    context: tuple[TestClient, Packages, Vectors], payload: dict[str, object]
) -> None:
    client, packages, vectors = context
    assert client.post("/sessions", json=payload, headers=HEADERS).status_code == 422
    assert not packages.items and not vectors.items


def test_body_limit(context: tuple[TestClient, Packages, Vectors]) -> None:
    client, _, _ = context
    response = client.post("/sessions", content=b"x" * (MAX_BODY_BYTES + 1), headers=HEADERS)
    assert response.status_code == 413


@pytest.mark.parametrize("failing_store", ["package", "vector"])
def test_compensation(context: tuple[TestClient, Packages, Vectors], failing_store: str) -> None:
    client, packages, vectors = context
    if failing_store == "package":
        packages.fail_put = True
    else:
        vectors.fail_put = True
    assert client.post("/sessions", json=PAYLOAD, headers=HEADERS).status_code == 503
    assert not packages.items and not vectors.items


def test_cleanup_failure_is_visible(
    context: tuple[TestClient, Packages, Vectors], caplog: pytest.LogCaptureFixture
) -> None:
    client, packages, vectors = context
    packages.fail_put = True
    vectors.fail_delete = True
    assert client.post("/sessions", json=PAYLOAD, headers=HEADERS).status_code == 503
    assert "Compensation failed" in caplog.text
    assert "Decision:" not in caplog.text


def test_failed_delete_can_be_retried(context: tuple[TestClient, Packages, Vectors]) -> None:
    client, _, vectors = context
    session_id = client.post("/sessions", json=PAYLOAD, headers=HEADERS).json()["session_id"]
    vectors.fail_delete = True
    assert client.delete(f"/sessions/{session_id}", headers=HEADERS).status_code == 503
    assert (
        client.post(
            f"/sessions/{session_id}/search", json={"query": "deployment"}, headers=HEADERS
        ).status_code
        == 404
    )
    vectors.fail_delete = False
    assert client.delete(f"/sessions/{session_id}", headers=HEADERS).status_code == 204
    assert not vectors.items


def test_session_isolation_and_query_validation(
    context: tuple[TestClient, Packages, Vectors],
) -> None:
    client, _, _ = context
    first = client.post("/sessions", json=PAYLOAD, headers=HEADERS).json()["session_id"]
    second = client.post(
        "/sessions",
        json={"messages": [{"role": "user", "content": "Only this session uses Modal."}]},
        headers=HEADERS,
    ).json()["session_id"]
    result = client.post(
        f"/sessions/{second}/search", json={"query": "deployment"}, headers=HEADERS
    )
    assert "Modal" in result.json()["matches"][0]["text"]
    assert "Lambda" not in json.dumps(result.json())
    for query in ("", " ", "x" * 2001):
        assert (
            client.post(
                f"/sessions/{first}/search", json={"query": query}, headers=HEADERS
            ).status_code
            == 422
        )
    assert (
        client.post(
            f"/sessions/{uuid4()}/search", json={"query": "deployment"}, headers=HEADERS
        ).status_code
        == 404
    )
    assert client.get("/sessions/not-a-uuid", headers=HEADERS).status_code == 422


def test_invalid_query_vectors_fail_explicitly(
    context: tuple[TestClient, Packages, Vectors], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = context
    session_id = client.post("/sessions", json=PAYLOAD, headers=HEADERS).json()["session_id"]
    monkeypatch.setattr(Backend, "embed", lambda self, texts: [[float("nan")] * 384])
    assert (
        client.post(
            f"/sessions/{session_id}/search", json={"query": "deployment"}, headers=HEADERS
        ).status_code
        == 503
    )
