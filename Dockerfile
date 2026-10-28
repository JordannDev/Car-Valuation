FROM python:3.12-slim-bookworm AS api-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /opt/valueapi/src/api

COPY src/api/pyproject.toml src/api/README.md ./
COPY src/api/app ./app
RUN pip install --no-cache-dir '.[production]' \
    && useradd --system --uid 10001 --create-home valueapi

COPY src/api/alembic.ini ./
COPY src/api/migrations ./migrations

USER valueapi
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:create_app()"]

FROM api-base AS cli

USER root
WORKDIR /opt/valueapi/src/cli
COPY src/cli/pyproject.toml src/cli/README.md src/cli/main.py src/cli/rules.toml src/cli/aliases.example.json ./
COPY src/cli/reader ./reader
COPY src/cli/ingestion ./ingestion
RUN pip install --no-cache-dir '.[import]' \
    && mkdir -p /home/valueapi/.local/share/valueapi/imports \
    && chown -R valueapi:valueapi /home/valueapi/.local

USER valueapi
HEALTHCHECK NONE
ENTRYPOINT ["python", "/opt/valueapi/src/cli/main.py"]
CMD ["--help"]

FROM api-base AS api
