# API image for Render (Docker, free plan). Built from backend/ as the context.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-dev --frozen || uv sync --no-dev

COPY . .

RUN useradd --system --no-create-home api
USER api

EXPOSE 8000

# Migrations first (failed migration fails the deploy; old instance keeps serving), then the API.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port \"$PORT\" --proxy-headers --forwarded-allow-ips='*'"]
