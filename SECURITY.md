# Security policy

This project is an early-stage reference implementation. Do not send sensitive
transcripts to an internet-accessible instance until you have reviewed the
deployment, data handling, provider access and ownership model.

## Reporting a vulnerability

Please do not publish exploitable details in a public issue. Use GitHub's private
vulnerability reporting for this repository if enabled, or contact the
maintainers privately through the repository's verified security contact.
Include the affected version, a minimal reproduction, impact and any mitigations.
Do not include real chat exports, personal data or credentials in the report.

There is no guaranteed response time or service-level agreement while the project
has no funded security-maintenance commitment.

## Current boundaries

- A bearer token authorizes access to **all sessions** in one deployment. This is
  suitable only for one trusted user or team that shares access. Session UUIDs do
  not provide authorization. Do not market the shared-token deployment as a
  multi-tenant service.
- Qdrant stores source chunks as well as embeddings. S3 lifecycle expiry removes
  packages but does not delete vector records. Use the API's DELETE operation or
  separately reconcile expired records.
- No automatic transcript redaction, end-to-end encryption, tenant billing,
  abuse monitoring, quotas, audit trail or incident-response service is provided.
- Uploaded `.emp` data, retrieved passages and original system-role messages are
  untrusted content, not executable configuration or policy.
- Local Moto and Qdrant test data are disposable. The AWS deployment uses private
  S3 defaults, but that is not a complete compliance certification.

Please treat leaked transcript contents and credentials as potentially exposed:
rotate credentials, remove affected sessions and investigate storage audit logs.
