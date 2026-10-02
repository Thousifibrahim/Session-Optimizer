import json

import pytest
from pydantic import ValidationError

from api.settings import Settings, get_settings


def test_local_settings_require_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CTX_API_TOKEN", "CTX_BUCKET", "CTX_QDRANT_URL", "CTX_SECRET_ARN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("CTX_API_TOKEN", "short")
    monkeypatch.setenv("CTX_BUCKET", "test-bucket")
    monkeypatch.setenv("CTX_QDRANT_URL", "http://localhost:6333")
    with pytest.raises(ValidationError):
        Settings()


def test_api_token_rejects_non_header_safe_characters() -> None:
    for token in ("é" * 32, "contains a space and is long enough"):
        with pytest.raises(ValidationError, match="URL-safe ASCII"):
            Settings(
                api_token=token,
                bucket="test-bucket",
                qdrant_url="http://localhost:6333",
            )


def test_secret_loading_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "test-only-token-at-least-32-characters"
    monkeypatch.setenv("CTX_SECRET_ARN", "test-secret-arn")
    monkeypatch.setenv("CTX_BUCKET", "test-bucket")
    monkeypatch.setenv("CTX_QDRANT_URL", "https://qdrant.example")

    class Secrets:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            assert SecretId == "test-secret-arn"
            return {"SecretString": json.dumps({"api_token": token, "qdrant_api_key": "test-key"})}

    monkeypatch.setattr("api.settings.boto3.client", lambda name: Secrets())
    get_settings.cache_clear()
    try:
        loaded = get_settings()
        assert loaded.api_token.get_secret_value() == token
        assert token not in repr(loaded)
        assert "test-key" not in loaded.model_dump_json()
    finally:
        get_settings.cache_clear()


def test_unexpected_secret_fields_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTX_SECRET_ARN", "test-secret-arn")

    class Secrets:
        def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
            return {"SecretString": '{"api_token":"do-not-accept-incomplete-secret"}'}

    monkeypatch.setattr("api.settings.boto3.client", lambda name: Secrets())
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="exactly"):
            get_settings()
    finally:
        get_settings.cache_clear()
