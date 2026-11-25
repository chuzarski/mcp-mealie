# Streamable-HTTP image for mcp-mealie. Stdio use needs no image: see the README.
#
#   docker build -t mcp-mealie .
#   docker run --rm -e MEALIE_URL=... -e MEALIE_API_TOKEN=... \
#     -e MEALIE_MCP_ALLOWED_HOSTS=my-proxy-host -p 8000:8000 mcp-mealie
#
# The token is read from the environment at run time. Nothing secret is baked in.

FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv==0.13.7
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# Dependencies first, so editing source does not reinstall them.
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mealie
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MEALIE_MCP_TRANSPORT=http \
    MEALIE_MCP_HOST=0.0.0.0 \
    MEALIE_MCP_PORT=8000
USER mealie
EXPOSE 8000
# Liveness only: /health does not call Mealie. The Host header is localhost,
# which the server always accepts.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('MEALIE_MCP_PORT','8000'), timeout=3)"]
ENTRYPOINT ["mcp-mealie"]
