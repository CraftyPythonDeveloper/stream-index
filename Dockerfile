# ─── Stage 1: Build dependencies ──────────────────────────────────────────────
# Install uv and resolve all runtime packages into a virtual environment.
# Copying pyproject.toml first keeps this layer cached when only app code changes.
FROM python:3.14-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy only the manifest — dependencies are resolved before app code is added
COPY pyproject.toml .

# Create a venv and install *only* the runtime dependencies (no dev group, no project itself)
# We exclude the project install because app/ is not present in this stage.
RUN uv venv .venv && \
    uv pip install --python .venv/bin/python \
        "python-stremio>=0.1.0" \
        "httpx>=0.27.0" \
        "beautifulsoup4>=4.12.0" \
        "lxml>=5.3.0" \
        "python-dotenv>=1.0.0" \
        "orjson>=3.10.0"

# ─── Stage 2: Runtime image ───────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

WORKDIR /app

# Copy the pre-built virtual environment
COPY --from=builder /app/.venv /app/.venv

# Copy application source code
COPY app/ app/

# Activate the virtual environment for all subsequent commands
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Stremio addon listens on 7000 by default
EXPOSE 7000

# Health check — verifies manifest endpoint is reachable
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7000/manifest.json')" || exit 1

CMD ["python", "-m", "app.main"]
