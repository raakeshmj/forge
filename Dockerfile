# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12
WORKDIR /app
# Dependencies first so source edits do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
RUN useradd --system --uid 10001 --no-create-home forge
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    FORGE_API_HOST=0.0.0.0 \
    FORGE_API_PORT=8000
USER forge
EXPOSE 8000
CMD ["python", "-m", "forge.api"]
