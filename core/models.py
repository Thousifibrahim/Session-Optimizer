from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    vector: list[float]
    index: int


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("message content must be valid UTF-8") from exc
        return value


class CondenseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=1000)
    summary_token_budget: int = Field(default=512, ge=64, le=4096)

    @model_validator(mode="after")
    def content_must_fit_input_limit(self) -> CondenseRequest:
        try:
            total_bytes = sum(len(message.content.encode("utf-8")) for message in self.messages)
        except UnicodeEncodeError as exc:
            raise ValueError("message content must be valid UTF-8") from exc
        if total_bytes > 256 * 1024:
            raise ValueError("total message content must not exceed 256 KiB")
        return self


class VectorState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection: str
    embedding_model: str
    dimensions: int
    point_ids: list[str] = Field(max_length=1500)
    query_hints: list[str] = Field(max_length=3)

    @field_validator("point_ids")
    @classmethod
    def point_ids_must_be_unique_uuids(cls, values: list[str]) -> list[str]:
        try:
            normalized = [str(UUID(value)) for value in values]
        except (ValueError, AttributeError) as exc:
            raise ValueError("point_ids must contain UUIDs") from exc
        if len(set(normalized)) != len(normalized):
            raise ValueError("point_ids must be unique")
        return normalized

    @field_validator("query_hints")
    @classmethod
    def query_hints_must_be_bounded(cls, values: list[str]) -> list[str]:
        if any(not hint.strip() or len(hint) > 240 for hint in values):
            raise ValueError("query_hints must contain 1-240 characters")
        return values

    @field_validator("dimensions")
    @classmethod
    def dimensions_must_match_embedding_model(cls, value: int) -> int:
        if value != 384:
            raise ValueError("dimensions must be 384")
        return value


class MemoryPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["emp"]
    version: Literal[1]
    session_id: str
    created_at: datetime
    summary_tokens: str
    token_count: int
    original_token_count: int
    instructions: str
    vector_state: VectorState
    content_sha256: str

    @field_validator("session_id")
    @classmethod
    def session_id_must_be_uuid(cls, value: str) -> str:
        try:
            return str(UUID(value))
        except (ValueError, AttributeError) as exc:
            raise ValueError("session_id must be a UUID") from exc

    @field_validator("created_at")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_at must be UTC-aware")
        return value

    @field_validator("token_count", "original_token_count")
    @classmethod
    def token_counts_must_be_nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("token counts must be nonnegative")
        return value

    @field_validator("content_sha256")
    @classmethod
    def transcript_hash_must_be_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        return value
