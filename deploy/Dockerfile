# Imagem do servidor da Aurora.
#   docker build -f deploy/Dockerfile -t leis-mcp .

FROM python:3.12-slim AS base

ARG EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    HF_HOME=/opt/hf

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Modelo antes do código: senão cada mudança em src/ baixa ~1,2 GB de novo.
RUN /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}', device='cpu')" \
    && chmod -R a+rX /opt/hf

COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --uid 10001 --create-home leis \
    && mkdir -p /dados /banco \
    && chown leis:leis /dados

ENV PATH="/app/.venv/bin:${PATH}" \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    LEIS_EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    LEIS_HOST=0.0.0.0 \
    LEIS_PORTA=8000 \
    LEIS_DB_PATH=/banco/leis.db \
    LEIS_USUARIOS_DB_PATH=/dados/usuarios.db \
    FASTMCP_HOME=/dados/fastmcp

USER leis
EXPOSE 8000

# start-period cobre o aquecimento (modelo e vetores em memória).
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/saude', timeout=4).status == 200 else 1)"

CMD ["leis-mcp"]
