ARG CORE_IMAGE
FROM ${CORE_IMAGE} AS runtime

RUN python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.1+cpu
COPY server/requirements-retrieval.txt /tmp/requirements-retrieval.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements-retrieval.txt
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-reranker-base', revision='2cfc18c9415c912f9d8155881c133215df768a70', local_dir='/opt/models/bge-reranker-base', allow_patterns=['config.json', 'tokenizer*', 'special_tokens_map.json', 'sentencepiece.bpe.model', 'model.safetensors'])"

COPY pyproject.toml README.md LICENSE /app/packages/
COPY mem0 /app/packages/mem0
RUN python -m pip install --no-cache-dir --no-deps /app/packages
COPY server /app
ENV HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
