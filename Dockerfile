# One image for every component: the package, the page and the checkpoints (D17).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /bin/uv

# One thread for PyTorch and the maths libraries (D12, section 14).
ENV OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    SDL_VIDEODRIVER=dummy \
    MPLBACKEND=Agg \
    ICADS_DB=/data/runs.sqlite

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY icads ./icads
COPY web ./web
COPY checkpoints ./checkpoints
COPY experiments ./experiments
RUN uv sync --locked --no-dev \
 && python -c "import uuid; print(uuid.uuid4())" > /app/IMAGE_BUILD_ID

ARG GIT_COMMIT=unknown
ENV ICADS_GIT_COMMIT=${GIT_COMMIT} \
    PATH=/app/.venv/bin:$PATH

VOLUME ["/data"]
ENTRYPOINT ["icads"]
CMD ["--help"]
