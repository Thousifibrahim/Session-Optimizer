import logging
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mangum import Mangum
from pydantic import BaseModel, Field
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.settings import get_settings
from api.storage import Match, PackageStore, QdrantVectors, S3Packages, VectorStore
from core.embedder import MODEL_NAME, FastEmbedBackend, validate_vectors
from core.engine import Condenser
from core.models import CondenseRequest, MemoryPackage

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 1024 * 1024
OPERATION_ERRORS = (
    BotoCoreError,
    ClientError,
    ResponseHandlingException,
    UnexpectedResponse,
    ValueError,
    RuntimeError,
    OSError,
    KeyError,
)


class BodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY_BYTES:
                response = Response(
                    '{"detail":"Request body exceeds 1 MiB"}',
                    status_code=413,
                    media_type="application/json",
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)


@dataclass
class Services:
    condenser: Condenser
    packages: PackageStore
    vectors: VectorStore


@lru_cache(maxsize=1)
def get_services() -> Services:
    settings = get_settings()
    return Services(
        Condenser(FastEmbedBackend(), collection=settings.collection),
        S3Packages(settings),
        QdrantVectors(settings),
    )


bearer = HTTPBearer(auto_error=False)


def authenticate(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    try:
        expected = get_settings().api_token.get_secret_value()
    except OPERATION_ERRORS as exc:
        raise unavailable(exc) from exc
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def resolve_services(_: Annotated[None, Depends(authenticate)]) -> Services:
    try:
        return get_services()
    except OPERATION_ERRORS as exc:
        logger.error("Service initialization failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Memory service unavailable") from exc


ServiceDependency = Annotated[Services, Depends(resolve_services)]
app = FastAPI(title="Session Optimizer", version="0.1.0")
app.add_middleware(BodyLimit)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class SearchResponse(BaseModel):
    session_id: UUID
    matches: list[Match]


def download_headers(response: Response) -> None:
    response.headers["Content-Disposition"] = 'attachment; filename="memory.emp"'
    response.headers["Cache-Control"] = "no-store"


def unavailable(exc: Exception) -> HTTPException:
    logger.error("Memory operation failed: %s", type(exc).__name__)
    return HTTPException(status_code=503, detail="Memory operation failed; retry later")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions", response_model=MemoryPackage, status_code=201)
def create_session(
    request: CondenseRequest, response: Response, services: ServiceDependency
) -> MemoryPackage:
    session_id = str(uuid4())
    try:
        package, chunks = services.condenser.condense(request, session_id)
        services.vectors.put(session_id, chunks)
        services.packages.put(package)
    except OPERATION_ERRORS as exc:
        # Writes span two services; compensating deletes also cover ambiguous write outcomes.
        for store in (services.packages, services.vectors):
            try:
                store.delete(session_id)
            except OPERATION_ERRORS as cleanup_exc:
                logger.error(
                    "Compensation failed for session %s: %s", session_id, type(cleanup_exc).__name__
                )
        raise unavailable(exc) from exc
    download_headers(response)
    return package


@app.get("/sessions/{session_id}", response_model=MemoryPackage)
def get_session(session_id: UUID, response: Response, services: ServiceDependency) -> MemoryPackage:
    try:
        package = services.packages.get(str(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except OPERATION_ERRORS as exc:
        raise unavailable(exc) from exc
    download_headers(response)
    return package


@app.post("/sessions/{session_id}/search", response_model=SearchResponse)
def search_session(
    session_id: UUID, request: SearchRequest, response: Response, services: ServiceDependency
) -> SearchResponse:
    if not request.query.strip():
        raise HTTPException(status_code=422, detail="Query must contain non-whitespace text")
    try:
        package = services.packages.get(str(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except OPERATION_ERRORS as exc:
        raise unavailable(exc) from exc
    expected_model = getattr(services.condenser.backend, "model_name", MODEL_NAME)
    if (
        package.vector_state.collection != services.condenser.collection
        or package.vector_state.embedding_model != expected_model
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session vectors use a different collection or embedding model",
        )
    try:
        vector = validate_vectors(services.condenser.backend.embed([request.query]), 1)[0]
        matches = services.vectors.search(str(session_id), vector, request.limit)
    except OPERATION_ERRORS as exc:
        raise unavailable(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return SearchResponse(session_id=session_id, matches=matches)


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: UUID, services: ServiceDependency) -> Response:
    try:
        # Removing the package first makes any orphan vectors inaccessible through this API.
        services.packages.delete(str(session_id))
        services.vectors.delete(str(session_id))
    except OPERATION_ERRORS as exc:
        raise unavailable(exc) from exc
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


handler = Mangum(app, lifespan="off")
