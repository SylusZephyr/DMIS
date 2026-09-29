# DMIS platform image: API, worker and scheduler (same image, different command).
#   docker build -t dmis-platform .
# Dependencies come from the hashed lockfile requirements/prod.txt (see docs/DEPLOYMENT_GUIDE.md,
# "Dependencies and the lockfile"); the project itself is installed without resolving anything.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY requirements/prod.txt requirements/prod.txt
RUN pip install --require-hashes -r requirements/prod.txt
COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY data/raw ./data/raw
RUN pip install --no-deps -e .
RUN useradd --create-home --uid 1000 dmis && mkdir -p /app/data/platform /app/data/lake /app/data/inbox /app/backups \
    && chown -R dmis:dmis /app/data /app/backups
USER dmis
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=5 CMD curl -fsS http://127.0.0.1:8000/api/v2/health || exit 1
CMD ["python", "scripts/dmis.py", "serve", "--host", "0.0.0.0", "--port", "8000"]
