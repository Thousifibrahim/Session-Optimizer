from typing import Protocol
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from api.settings import Settings
from core.embedder import DIMENSIONS
from core.models import Chunk, MemoryPackage

MAX_PACKAGE_BYTES = 1024 * 1024


class Match(BaseModel):
    model_config = {"extra": "forbid"}

    point_id: str
    score: float = Field(ge=-1, le=1)
    text: str = Field(min_length=1, max_length=16_000)
    index: int = Field(ge=0)


class PackageStore(Protocol):
    def put(self, package: MemoryPackage) -> None: ...
    def get(self, session_id: str) -> MemoryPackage: ...
    def delete(self, session_id: str) -> None: ...


class VectorStore(Protocol):
    def put(self, session_id: str, chunks: list[Chunk]) -> None: ...
    def search(self, session_id: str, vector: list[float], limit: int) -> list[Match]: ...
    def delete(self, session_id: str) -> None: ...


def package_key(session_id: str) -> str:
    return f"sessions/{UUID(session_id)}.emp"


class S3Packages:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.bucket
        self.client = boto3.client(
            "s3",
            region_name=settings.region,
            endpoint_url=settings.s3_endpoint_url,
            config=Config(
                connect_timeout=2,
                read_timeout=6,
                retries={"mode": "standard", "max_attempts": 2},
            ),
        )

    def put(self, package: MemoryPackage) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=package_key(package.session_id),
            Body=package.model_dump_json().encode("utf-8"),
            ContentType="application/json",
            IfNoneMatch="*",
        )

    def get(self, session_id: str) -> MemoryPackage:
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=package_key(session_id))
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                raise KeyError(session_id) from exc
            raise
        if result.get("ContentLength", 0) > MAX_PACKAGE_BYTES:
            raise ValueError("Stored memory package exceeds 1 MiB")
        with result["Body"] as body:
            payload = body.read(MAX_PACKAGE_BYTES + 1)
        if len(payload) > MAX_PACKAGE_BYTES:
            raise ValueError("Stored memory package exceeds 1 MiB")
        return MemoryPackage.model_validate_json(payload)

    def delete(self, session_id: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=package_key(session_id))


class QdrantVectors:
    def __init__(self, settings: Settings) -> None:
        self.collection = settings.collection
        self.client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
            timeout=10,
        )
        if not self.client.collection_exists(self.collection):
            try:
                self.client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(
                        size=DIMENSIONS, distance=models.Distance.COSINE
                    ),
                )
            except UnexpectedResponse as exc:
                if exc.status_code != 409:
                    raise
        config = self.client.get_collection(self.collection).config.params.vectors
        if not isinstance(config, models.VectorParams):
            raise ValueError("Collection must use one unnamed vector")
        if config.size != DIMENSIONS or config.distance != models.Distance.COSINE:
            raise ValueError("Collection must use 384-dimensional cosine vectors")
        self.client.create_payload_index(
            self.collection, "session_id", field_schema=models.PayloadSchemaType.KEYWORD, wait=True
        )

    @staticmethod
    def session_filter(session_id: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(key="session_id", match=models.MatchValue(value=session_id))
            ]
        )

    def put(self, session_id: str, chunks: list[Chunk]) -> None:
        self.client.upsert(
            self.collection,
            points=[
                models.PointStruct(
                    id=chunk.id,
                    vector=chunk.vector,
                    payload={"session_id": session_id, "text": chunk.text, "index": chunk.index},
                )
                for chunk in chunks
            ],
            wait=True,
        )

    def search(self, session_id: str, vector: list[float], limit: int) -> list[Match]:
        points = self.client.query_points(
            self.collection,
            query=vector,
            query_filter=self.session_filter(session_id),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        ).points
        matches: list[Match] = []
        for point in points:
            if point.payload is None:
                raise ValueError("Stored vector is missing its payload")
            try:
                matches.append(
                    Match.model_validate(
                        {
                            "point_id": str(point.id),
                            "score": point.score,
                            "text": point.payload["text"],
                            "index": point.payload["index"],
                        }
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Stored vector has an invalid payload") from exc
        return matches

    def delete(self, session_id: str) -> None:
        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(filter=self.session_filter(session_id)),
            wait=True,
        )
