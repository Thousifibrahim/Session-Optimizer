# Session Optimizer

A small, serverless session-memory API: condense chat into a token-budgeted
extractive recap, index local embeddings, and export a portable `.emp` JSON file.
No paid LLM API is required. MIT licensed.

**Project guide:** [Session Optimizer website](https://thousifibrahim.github.io/Session-Optimizer/).
Open [index.html](index.html) directly for an offline version of the guide.
GitHub Pages hosts only this static guide, not the Python API.
Contributions and issue reports are welcome through the
[GitHub repository](https://github.com/Thousifibrahim/Session-Optimizer).

**Status: pre-release reference implementation.** Package and API compatibility are
provisional. The default shared-token deployment is for one trusted user or team,
not a public multi-tenant memory product. See [security boundaries](SECURITY.md)
and the [launch checklist](docs/use-cases.md#before-a-public-launch).

This preserves **selected historical context**, not an LLM's hidden state.
Uploading the recap works without integrations; deep recall needs an authenticated
HTTP tool. Embeddings are search indexes, not directly readable model memories.

## Architecture

```text
Chat export / webhook client
          |
     API Gateway HTTP API
          |
     Lambda container (FastAPI + Mangum)
          |-- core: paragraph-aware chunks + token-budgeted recap
          |         FastEmbed BAAI/bge-small-en-v1.5 (384-dimensional cosine)
          |-- Qdrant: session-scoped vectors and source passages
          `-- S3: private portable JSON package

Next session: upload recap -> optionally search original passages via API
```

Requests are HTTP events and run synchronously; there is no queue or background
worker disguised as an asynchronous pipeline. An `all-MiniLM-L6-v2`-class small
ONNX embedding model is used through FastEmbed. LangChain's standalone text
splitter handles paragraph-aware chunking without a full agent framework.
The three reusable boundaries are:

| Module | Responsibility |
| --- | --- |
| [core](core/) | Pure package/chunk contracts, measured token budgets, embedding adapter |
| [api](api/) | Authentication, orchestration, S3/Qdrant persistence, Lambda adapter |
| [infrastructure](infrastructure/) | Local Compose and AWS Terraform |

[skills](skills/) describes portable integration; [tests](tests/) exercises the
contracts with deterministic embeddings and isolated storage doubles.

## Who this is for

- **Developers:** carry decisions and pending work between project chats or model
  vendors; retrieve original excerpts rather than trusting a compressed recap.
- **Research and study:** preserve questions, hypotheses, reading notes and next
  steps without paying for a third-party summarization model.
- **Support teams:** prepare sanitized shift handoffs across chats, provided the
  team explicitly shares access to a single deployment.
- **Local-first builders:** run the tokenization and ONNX embedding core with
  your own storage adapters or disposable local development services.

It is **not** a lossless archive, an autonomous agent, an account system, an
encrypted vault, or a hosted public service. More workflows and explicit
non-goals are in [use cases](docs/use-cases.md).

## Local quickstart (PowerShell)

Prerequisites: Python 3.12, Docker with Linux containers, and internet access for
dependency/model downloads. No AWS account is needed locally.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item infrastructure\.env.example infrastructure\.env
# Edit infrastructure\.env: replace the token placeholder with a random credential.
docker compose --env-file infrastructure\.env -f infrastructure\docker-compose.yml up --build -d
Invoke-RestMethod http://localhost:8000/health
```

The image downloads the model and tokenizer **at build time**. This takes time and
disk space but avoids model downloads on a Lambda cold start. Only the API is
bound to localhost; the Moto S3 emulator and Qdrant have no host ports.
Moto ignores its dummy AWS credentials and keeps objects in memory. Local Compose
is an **ephemeral development environment**, not durable long-term storage.
Restarting Moto loses packages; recreating the stack loses all local context.
AWS deployment uses real private S3 for durability.

```powershell
$token = Read-Host "Enter the CTX_API_TOKEN from your local .env"
$headers = @{ Authorization = "Bearer $token" }
Invoke-WebRequest http://localhost:8000/sessions -Method Post `
  -Headers $headers -ContentType application/json `
  -Body (Get-Content examples\session.json -Raw) -OutFile memory.emp
$memory = Get-Content memory.emp -Raw | ConvertFrom-Json
Invoke-RestMethod "http://localhost:8000/sessions/$($memory.session_id)/search" `
  -Method Post -Headers $headers -ContentType application/json `
  -Body '{"query":"What deployment did we choose?","limit":3}'
```

Swagger is at `http://localhost:8000/docs`. GET `/sessions/{session_id}` downloads
the persisted package; DELETE removes both package and vectors and is retryable.
`/health` is liveness only, not a storage readiness test.

Stop containers with `docker compose --env-file infrastructure\.env -f
infrastructure\docker-compose.yml down`; local context is disposable.

### Standalone Python

```python
from pathlib import Path
from uuid import uuid4

from core.embedder import FastEmbedBackend
from core.engine import Condenser
from core.models import ChatMessage, CondenseRequest

request = CondenseRequest(
    messages=[ChatMessage(role="user", content="Decision: use private S3 storage.")],
    summary_token_budget=128,
)
package, chunks = Condenser(FastEmbedBackend()).condense(request, str(uuid4()))
Path("memory.emp").write_text(package.model_dump_json(indent=2), encoding="utf-8")
```

This path creates a recap and vectors **without persisting them**. For later
retrieval, save through the API or provide your own storage adapters. Package
references do not magically persist standalone vectors.

## Package and API contract

POST `/sessions` accepts `messages` (1-1000 role/content objects) and
`summary_token_budget` (64-4096, default 512). Total content is capped at 256 KiB
UTF-8; the entire HTTP body is capped at 1 MiB. Blank messages are rejected.
The response is JSON with attachment filename `memory.emp`:

```json
{
  "format": "emp",
  "version": 1,
  "session_id": "<UUID>",
  "created_at": "<UTC timestamp>",
  "summary_tokens": "<extractive text>",
  "token_count": 128,
  "original_token_count": 2500,
  "instructions": "<untrusted-data and tool-use warning>",
  "vector_state": {
    "collection": "session_optimizer",
    "embedding_model": "BAAI/bge-small-en-v1.5",
    "dimensions": 384,
    "point_ids": ["<session-specific UUID>"],
    "query_hints": ["<selected source hints>"]
  },
  "content_sha256": "<hash of canonical transcript>"
}
```

`token_count` is the measured recap length in `cl100k_base` tokens, **not** the
whole package length and not every vendor's tokenizer. Metadata overhead and
point IDs can dominate small recaps. The budget is not a guaranteed compression
ratio. The hash fingerprints transcript content; it is not a package signature,
encryption key, content-addressed retrieval capability, or authentication token.

POST `/sessions/{id}/search` accepts a nonblank `query` (max 2000 characters) and
`limit` (1-20); returns `{session_id, matches: [{point_id, score, text, index}]}`.
Search always filters on the session ID. Vectors are finite, nonzero,
384-dimensional and normalized; collection dimension/distance mismatches fail
explicitly. A session created with a different collection/model returns 409 and
must be reindexed instead of silently querying an incompatible index. No raw
vectors or credentials enter the portable file.

All memory routes require a bearer token of at least 32 characters. Errors:
401 bad/missing token, 404 absent session, 413 oversized body, 422 invalid input,
409 stale model/collection, 503 failed storage/model operation. Tokens must use
URL-safe ASCII. Operation errors are logged by type without logging chat contents
or credentials. `/health` is public liveness only; it is not storage readiness.

## AWS deployment (explicit, never automatic on push)

Requires Terraform 1.10+, AWS CLI, Docker, an AWS account, an existing Qdrant HTTPS
endpoint, and a Secrets Manager JSON secret containing exactly `api_token` and
`qdrant_api_key`. The token must have at least 32 characters. Do not pass secret
values to Terraform; it stores only the secret ARN.

1. Create a dedicated, private, encrypted, versioned S3 **Terraform state bucket**
   separately. Enable access controls and grant your deploy role the state object
   read/write permissions and lockfile Get/Put/Delete permissions. Do not reuse the
   session-package bucket for state.
2. Bootstrap the ECR repository and package bucket:

   ```powershell
   Set-Location infrastructure\terraform
   terraform init -backend-config="bucket=YOUR_STATE_BUCKET" `
     -backend-config="key=session-optimizer/terraform.tfstate" `
     -backend-config="region=us-east-1" -backend-config="encrypt=true"
   terraform apply
   $repo = terraform output -raw repository_url
   ```

3. From the workspace root, build a unique immutable tag and push it:

   ```powershell
   aws ecr get-login-password --region us-east-1 |
     docker login --username AWS --password-stdin ($repo.Split('/')[0])
   docker build --platform linux/amd64 --provenance=false -t "${repo}:v0.1.0" .
   docker push "${repo}:v0.1.0"
   $digest = aws ecr describe-images --region us-east-1 --repository-name session-optimizer `
     --image-ids imageTag=v0.1.0 --query "imageDetails[0].imageDigest" --output text
   ```

4. Return to the Terraform directory:

   ```powershell
   terraform apply -var="image_digest=$digest" `
     -var="qdrant_url=https://YOUR_QDRANT_HOST" `
     -var="secret_arn=YOUR_SECRET_ARN"
   terraform output -raw api_url
   ```

Keep deployment variables in your local invocation/configuration on subsequent
plans. **Omitting `image_digest` later plans removal of the API and Lambda**;
null is only the bootstrap mode. Use `secret_kms_key_arn` if the secret has a
customer-managed KMS key.

Terraform provides private encrypted S3, TLS-only bucket policy, package expiry,
immutable scanned ECR, narrowly scoped Lambda IAM, CloudWatch logs, Lambda
concurrency limits and API throttling. Qdrant and the state bucket/secret are
explicit external prerequisites, not managed as fake serverless AWS resources.
The Lambda has 2048 MiB and a 28-second timeout below the HTTP integration timeout.
Benchmark large transcripts/cold starts in your account; the size cap alone does
not guarantee requests finish in time. For long sessions, an SQS/job-status
architecture is a future extension, not implemented here.

### CI/CD

CI runs tests, lint, strict type checks, Terraform formatting and validation.
Mocked Terraform tests cover bootstrap/deployment gating, public access blocks,
immutable image selection, timeout ordering and exclusion of secret values.
The manual deploy workflow first repeats Python checks, builds an image and
applies its immutable digest. Set a protected GitHub `production` environment
with required reviewers and these variables:

`AWS_REGION`, `PROJECT_NAME` (default project is `session-optimizer`),
`DEPLOY_ROLE_ARN`, `TF_STATE_BUCKET`, `TF_STATE_KEY`, `QDRANT_URL`,
`CTX_SECRET_ARN`, and optionally `CTX_SECRET_KMS_KEY_ARN`.

Bootstrap Terraform state/ECR before using this workflow. Configure AWS OIDC trust
restricted to your repository's **production environment**, not a wildcard repo.
The role needs ECR push, Terraform-managed resource permissions, `iam:PassRole`
restricted to the Lambda role, and state/lockfile access. No AWS access keys belong
in GitHub variables. Review every production plan; this starter is not an
organization-specific IAM or compliance baseline.

## Validation

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\ruff check .
.\.venv\Scripts\ruff format --check .
.\.venv\Scripts\python -m mypy
terraform -chdir=infrastructure\terraform init -backend=false
terraform -chdir=infrastructure\terraform validate
terraform -chdir=infrastructure\terraform test
```

`pytest` runs core/API cases and the realistic end-to-end handoff using real
in-memory Qdrant plus Moto S3; all network/cloud boundaries remain local. The
FastEmbed smoke test downloads/uses the actual ONNX model and is separate from CI.
Compose and AWS have their own validation; `docker compose config` does not build
or start containers. An AWS plan, approval, domain, account-specific Qdrant and
live deployment smoke test remain operator responsibilities.

## Privacy, consistency and tradeoffs

- **Single trusted deployment:** one shared token can access all sessions. Session
  filtering is not tenant authorization. Add verified user identity and ownership
  before exposing this as a multi-tenant service.
- **Data sensitivity:** Qdrant holds original chunk text as well as embeddings.
  The recap and query hints can contain personal or secret data. Redact exports
  before upload; no automatic redaction is claimed. AWS encryption at rest is
  not end-to-end encryption, and local container storage is not encrypted by this project.
- **Prompt injection:** historical messages are untrusted, including `system`
  messages. File warnings help integration but cannot force an LLM to comply.
  Only trusted host instructions and configured tools govern retrieval.
- **Dual writes:** successful creation means both stores acknowledged the write.
  Failures trigger compensating deletes and explicit 503 responses; cleanup
  failures are logged. There is no distributed transaction. A process kill or
  timeout can leave orphans; repeated POSTs make new sessions, not idempotent jobs.
- **Deletion/expiry:** retry DELETE after a 503. S3 lifecycle expiry makes a session
  inaccessible but does not clean Qdrant; use API deletion before expiry or run an
  operator reconciliation job. No scheduled reconciler is shipped.
- **Cost:** small local ONNX embeddings avoid LLM charges, not hosting charges.
  Qdrant, S3, Lambda, logs and ECR can still cost money. Containers/model cold
  starts are not measured performance claims.
- **Portability:** the recap is model-independent text; coordinates remain tied
  to the configured embedding model and vector collection. Changing models
  requires a new collection and reindexing.

## Project and release guides

- [Use cases and operational limitations](docs/use-cases.md)
- [Security policy and disclosure](SECURITY.md)
- [Contributor setup and test expectations](CONTRIBUTING.md)
- [Integration details for chat UIs](skills/README.md)
- [Change history and provisional versioning](CHANGELOG.md)

No public multi-tenant launch should proceed until identity/ownership, quotas,
abuse monitoring, deletion/reconciliation, privacy review, service operations,
and deployment smoke tests are implemented and independently reviewed.
