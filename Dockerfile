FROM public.ecr.aws/lambda/python:3.12

ENV FASTEMBED_CACHE_PATH=/opt/fastembed \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    TOKENIZERS_PARALLELISM=false

RUN dnf install -y libgomp && dnf clean all

COPY pyproject.toml README.md ${LAMBDA_TASK_ROOT}/
COPY core ${LAMBDA_TASK_ROOT}/core
COPY api ${LAMBDA_TASK_ROOT}/api
RUN pip install --no-cache-dir . && \
    python -c "from fastembed import TextEmbedding; import tiktoken; list(TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/fastembed').embed(['warmup'])); tiktoken.get_encoding('cl100k_base')" && \
    chmod -R a+rX /opt/fastembed /opt/tiktoken

CMD ["api.app.handler"]
