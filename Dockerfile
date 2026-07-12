# Dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM node:22-slim AS webbuilder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ .
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd --create-home kiln && mkdir -p /data && chown kiln:kiln /data
COPY --from=builder --chown=kiln:kiln /app /app
COPY --from=webbuilder --chown=kiln:kiln /web/dist /app/web/dist
ENV PATH="/app/.venv/bin:$PATH" \
    KILNWORKS_API_HOST=0.0.0.0 \
    KILNWORKS_DATA_DIR=/data \
    KILNWORKS_WEB_DIST_DIR=/app/web/dist
USER kiln
EXPOSE 8000
ENTRYPOINT ["kilnworks"]
CMD ["serve"]
