# JijiLens in one image: the web app, its two models, and its own Postgres +
# pgvector, which loads the catalog snapshot at startup. See docker/entrypoint.sh.
FROM python:3.14-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17 postgresql-17-pgvector \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

# Hugging Face Spaces runs containers as user 1000.
RUN useradd --create-home --uid 1000 user
ENV HOME=/home/user \
    HF_HOME=/opt/hf \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:/usr/lib/postgresql/17/bin:$PATH"
WORKDIR /app

# Dependencies first, so code changes don't reinstall PyTorch.
COPY pyproject.toml uv.lock README.md ./
# uv's download cache lives in a build cache, not in the image (it was 1.2 GB).
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY db ./db
COPY config ./config
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Bake the model weights into the image, so startup doesn't download ~1.5 GB.
RUN python -c "\
from jijilens.search.embedder import DEFAULT_MODEL, Embedder, get_spec; \
from jijilens.search.detector import DEFAULT_DETECTOR, DETECTORS, Detector; \
Embedder(get_spec(DEFAULT_MODEL)); Detector(DETECTORS[DEFAULT_DETECTOR])" \
 && chmod -R a+rX /opt/hf

COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

USER user
ENV PGDATA=/home/user/pgdata \
    DATABASE_URL=postgresql://jijilens@127.0.0.1:5432/jijilens \
    SNAPSHOT_DIR=/snapshot \
    PORT=7860
EXPOSE 7860
ENTRYPOINT ["entrypoint.sh"]
