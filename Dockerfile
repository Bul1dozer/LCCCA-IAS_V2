# LCCA-IAS v3 — Production Dockerfile
#
# Multi-stage build: a slim final image with only runtime dependencies.
# Defaults to SQLite (zero-config); set DATABASE_URL to point at
# PostgreSQL for real production concurrency (see docker-compose.yml).

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files / buffering stdout (cleaner logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System dependencies needed by reportlab (PDF generation) and Pillow (image processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libjpeg-dev \
        zlib1g-dev \
        libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data directory for the SQLite DB file and generated assets.
# Mount a volume here in production so data survives container restarts.
RUN mkdir -p /app/data && useradd --create-home --uid 1000 lcca \
    && chown -R lcca:lcca /app
USER lcca

ENV DATABASE_URL=sqlite:////app/data/lcca.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
