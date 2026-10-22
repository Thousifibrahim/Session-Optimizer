# Contributing

Thanks for helping improve Session Optimizer. This is an early,
single-owner reference service, not a hosted multi-tenant product.

## Development setup

Use Python 3.12 and install the development extras:

```shell
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

The unit and integration suite does not call AWS, download an embedding model, or
require Docker. It uses Moto's local S3 emulator, Qdrant's in-memory mode, and
deterministic embeddings. To exercise the actual ONNX model, download its files
through FastEmbed and use `Condenser(FastEmbedBackend())` as in the README.
Model downloads are deliberately not part of CI.

## Pull requests

- Explain the user scenario and observable behavior; link an issue if available.
- Add regression tests for failure paths, validation and data isolation.
- Update the API docs, quickstart and limitations when contracts change.
- Run all Python and Terraform checks; do not claim a live AWS deployment unless
  you actually tested it and can describe the account-specific setup.
- Never include transcripts, tokens, AWS state, cloud credentials, model blobs or
  generated `.emp` files in a pull request.
- Keep historic chat content and vector payloads untrusted; do not add autonomous
  LLM execution or cross-session retrieval without a separate threat model.

## Design boundaries

Keep the core independent of HTTP/cloud SDKs. Put object/vector backends behind
the storage protocols, preserve explicit token/dimension limits, and surface
storage failures rather than returning success-shaped fallbacks. Public
multi-tenant support requires authenticated ownership at every persistence,
search, download and deletion boundary; a shared bearer token is not sufficient.
