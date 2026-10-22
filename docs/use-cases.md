# Use cases and worked workflows

The API keeps a **selected, searchable record of prior context**. It does not
reconstruct model state, automate decisions, or guarantee a complete recap.

## 1. Carry a project handoff to a new assistant

Useful for a developer who changes chat windows or LLM vendors but wants key
decisions and the option to check their original wording.

1. Export a session with `system`, `user` and `assistant` text messages.
2. Replace any sensitive values before upload.
3. POST the export to `/sessions`; keep the returned `.emp` file.
4. Upload that recap in the new chat. Use the matching session ID with the
   configured bearer-authenticated `/search` Action when a detail is missing.
5. Confirm important decisions against retrieved excerpts or the original
   source; vector ranking is not verification.

Run the end-to-end example in `examples/session.json` and
`tests/test_scenarios.py::test_real_s3_and_qdrant_handoff_lifecycle`.

## 2. Research or study notebook

Keep a concise recap of research questions, decisions and open follow-ups; search
older discussion before repeating work. The sample transcript uses a declared
deployment choice and a follow-up benchmark task. A user should independently
check cited facts against source material: the service does not crawl URLs or
validate claims.

## 3. Support or incident handoff

Store sanitized troubleshooting conversations for a later support shift. Omit
customer identifiers, credentials, access tokens, logs containing personal data,
and regulated records unless your organization has approved the complete storage
and deletion design. Use one deployment per trusted team; this API has no
per-customer permissions.

## 4. Local/offline-first experimentation

Use `Condenser` and the local ONNX embedding model without S3, Qdrant, or a paid
LLM API. Persist the returned package and provide a vector store yourself if you
need later detail retrieval. Local operation does not mean zero network access:
the model is normally fetched the first time, or pre-downloaded when building the
container.

## Current limits: when not to use it

- Do not use the single bearer-token deployment as a public pastebin or
  multi-tenant hosted memory service.
- Do not treat `.emp` as encrypted, signed, self-contained, or as a portable
  embedding database. It contains a recap and opaque vector references; indexed
  source chunks remain in the configured Qdrant collection.
- Do not upload unredacted secrets or data you cannot retain in both S3 and
  Qdrant. S3 expiry does not remove Qdrant records.
- Configuring S3 lifecycle expiry alone leaves source chunks in Qdrant. Run the
  API's DELETE operation before expiry or provide a separately monitored
  reconciliation process.
- Do not use a recap as a substitute for source-of-truth records, legal/medical
  advice, audited decisions or lossless session archives.
- Do not rely on English-optimized embeddings for multilingual semantic-search
  quality. Unicode is retained in package text, but cross-language retrieval has
  not been benchmarked.

  ## Before a public launch

  This repository is **not ready to accept arbitrary public users or sensitive
  transcripts**. Before launching a hosted offering, budget and independently review:

  1. Replace the shared bearer token with verified user identities, per-user
     authorization and ownership checks on every operation.
  2. Isolate tenant vectors in the retrieval layer, not just at the HTTP route;
     test that one user's query cannot read another tenant's chunk.
  3. Add quotas, request rate limits, abuse monitoring, billing/cost alerts,
     back-pressure and a load-tested async path for large sessions.
  4. Reconcile package expiry with vector deletion, define backups/retention, and
     test deletion failures and disaster recovery.
  5. Add privacy/consent controls, documented subprocessors, data export, key
     rotation, auditability and an incident-response contact/process.
  6. Threat-model prompt injection, confused-deputy integrations, package tampering,
     model supply chain, stolen credentials and noisy-neighbor DoS.
  7. Benchmark actual AWS cold starts, Qdrant capacity, concurrency, request sizes,
     embedding quality and per-session costs. Review the IAM role, GitHub OIDC
     trust, state backend, secret rotation, logs and deployment rollback.
  8. Complete a privacy/legal review for each target market and publish accurate
     service terms before collecting other people's conversations.

  Document decisions and test evidence; do not treat this checklist or CI as a
  security certification.

## Integration mapping

Map supported messages to `{role, content}` objects. Only `system`, `user` and
`assistant` roles are accepted. Flatten text-only tool results into a labeled
assistant or user message; the service deliberately does not execute tools,
parse images/audio, or accept arbitrary role metadata. Keep historic instructions
as quoted data and follow only current, trusted host policy.

See [the integration guide](../skills/README.md) for portable `.emp` handling.
