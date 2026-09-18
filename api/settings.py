import json
import os
from functools import lru_cache

import boto3
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CTX_", extra="forbid")

    api_token: SecretStr = Field(min_length=32, max_length=256)
    bucket: str = Field(min_length=1)
    qdrant_url: str = Field(pattern=r"^https?://")
    qdrant_api_key: SecretStr | None = None
    collection: str = Field(default="session_optimizer", pattern=r"^[a-zA-Z0-9_-]+$")
    s3_endpoint_url: str | None = None
    region: str = "us-east-1"

    @field_validator("api_token")
    @classmethod
    def token_must_be_header_safe(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if not token.isascii() or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
            for character in token
        ):
            raise ValueError("api_token must use URL-safe ASCII characters")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    secret_arn = os.environ.get("CTX_SECRET_ARN")
    if not secret_arn:
        return Settings()
    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    secret = json.loads(response["SecretString"])
    if not isinstance(secret, dict) or set(secret) != {"api_token", "qdrant_api_key"}:
        raise ValueError("Secret must contain exactly api_token and qdrant_api_key")
    return Settings(api_token=secret["api_token"], qdrant_api_key=secret["qdrant_api_key"])
