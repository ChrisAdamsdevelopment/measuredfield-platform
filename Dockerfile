# Optional container image. render.yaml uses Render's native Python runtime; switch a
# service to `runtime: docker` to use this image instead when you need Node.js present
# (the PBC-derived growth evaluator runs in Node; without it the app falls back to the
# native Python review and says so).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 mf

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY app ./app

USER mf
EXPOSE 10000
# Migrations are NOT run here: they need the privileged MIGRATION_DATABASE_URL and belong
# in the pre-deploy step (python -m app.migrate), never in the long-running web process.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --no-proxy-headers"]
