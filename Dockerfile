FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra hosted --no-install-project
COPY macro_tracker ./macro_tracker
RUN uv sync --locked --no-dev --extra hosted --no-editable
CMD ["/app/.venv/bin/python", "-m", "macro_tracker.hosted"]
