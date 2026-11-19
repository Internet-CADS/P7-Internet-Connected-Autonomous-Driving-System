FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.13-slim

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY cads ./cads
RUN uv sync --locked --no-dev --no-editable
ENTRYPOINT ["cads"]
