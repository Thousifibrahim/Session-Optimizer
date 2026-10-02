import io
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from moto import mock_aws
from pydantic import SecretStr
from qdrant_client import QdrantClient, models

from api.settings import Settings
from api.storage import MAX_PACKAGE_BYTES, QdrantVectors, S3Packages, package_key
from core.engine import Condenser
from core.models import ChatMessage, CondenseRequest


class Backend:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 383 for _ in texts]


def settings() -> Settings:
    return Settings(
        api_token=SecretStr("test-only-token-with-at-least-32-characters"),
        bucket="test-bucket",
        qdrant_url="http://localhost:6333",
    )


def test_s3_roundtrip_and_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test-only",
        aws_secret_access_key="test-only",
    )
    monkeypatch.setattr("api.storage.boto3.client", lambda *args, **kwargs: client)
    store = S3Packages(settings())
    session_id = str(uuid4())
    package, _ = Condenser(Backend()).condense(
        CondenseRequest(messages=[ChatMessage(role="user", content="Decision: use S3.")]),
        session_id,
    )
    data = package.model_dump_json().encode("utf-8")
    key = {"Bucket": "test-bucket", "Key": f"sessions/{session_id}.emp"}
    with Stubber(client) as stubber:
        stubber.add_response(
            "put_object",
            {},
            {**key, "Body": data, "ContentType": "application/json", "IfNoneMatch": "*"},
        )
        stubber.add_response(
            "get_object", {"Body": StreamingBody(io.BytesIO(data), len(data))}, key
        )
        stubber.add_response("delete_object", {}, key)
        stubber.add_client_error("get_object", service_error_code="NoSuchKey", expected_params=key)
        store.put(package)
        assert store.get(session_id) == package
        store.delete(session_id)
        with pytest.raises(KeyError):
            store.get(session_id)
        stubber.assert_no_pending_responses()


def test_qdrant_real_local_filter_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    client = QdrantClient(":memory:")
    monkeypatch.setattr("api.storage.QdrantClient", lambda **kwargs: client)
    store = QdrantVectors(settings())
    first, second = str(uuid4()), str(uuid4())
    for session_id, content in [(first, "Use Lambda."), (second, "Use Modal.")]:
        _, chunks = Condenser(Backend()).condense(
            CondenseRequest(messages=[ChatMessage(role="user", content=content)]),
            session_id,
        )
        store.put(session_id, chunks)
    vector = Backend().embed(["query"])[0]
    assert "Lambda" in store.search(first, vector, 5)[0].text
    assert "Modal" in store.search(second, vector, 5)[0].text
    store.delete(first)
    assert store.search(first, vector, 5) == []
    assert len(store.search(second, vector, 5)) == 1
    client.close()


def test_collection_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    client = QdrantClient(":memory:")
    client.create_collection(
        "session_optimizer",
        vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE),
    )
    monkeypatch.setattr("api.storage.QdrantClient", lambda **kwargs: client)
    with pytest.raises(ValueError, match="384-dimensional cosine"):
        QdrantVectors(settings())
    client.close()


def test_package_keys_do_not_allow_traversal() -> None:
    with pytest.raises(ValueError):
        package_key("../../private")


@mock_aws
def test_oversized_s3_package_is_rejected_before_parsing() -> None:
    store = S3Packages(settings())
    store.client.create_bucket(Bucket="test-bucket")
    session_id = str(uuid4())
    store.client.put_object(
        Bucket="test-bucket",
        Key=package_key(session_id),
        Body=b"x" * (MAX_PACKAGE_BYTES + 1),
    )
    with pytest.raises(ValueError, match="exceeds 1 MiB"):
        store.get(session_id)


def test_invalid_qdrant_payload_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = QdrantClient(":memory:")
    monkeypatch.setattr("api.storage.QdrantClient", lambda **kwargs: client)
    store = QdrantVectors(settings())
    session_id = str(uuid4())
    client.upsert(
        "session_optimizer",
        points=[
            models.PointStruct(
                id=str(uuid4()),
                vector=[1.0] + [0.0] * 383,
                payload={"session_id": session_id, "text": 12, "index": -1},
            )
        ],
        wait=True,
    )
    with pytest.raises(ValueError, match="invalid payload"):
        store.search(session_id, [1.0] + [0.0] * 383, 1)
    client.close()


@mock_aws
def test_s3_emulator_persistence_and_conditional_write() -> None:
    store = S3Packages(settings())
    store.client.create_bucket(Bucket="test-bucket")
    session_id = str(uuid4())
    package, _ = Condenser(Backend()).condense(
        CondenseRequest(messages=[ChatMessage(role="user", content="Decision: keep S3 private.")]),
        session_id,
    )
    store.put(package)
    assert store.get(session_id) == package
    with pytest.raises(ClientError) as error:
        store.put(package)
    assert error.value.response["Error"]["Code"] == "PreconditionFailed"
    assert store.get(session_id) == package
    store.delete(session_id)
    store.delete(session_id)
    with pytest.raises(KeyError):
        store.get(session_id)
