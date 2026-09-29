FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY app ./app
COPY web ./web
COPY alembic.ini .
COPY migrations ./migrations
COPY docker-entrypoint.sh .
RUN addgroup --system gardener && adduser --system --ingroup gardener gardener \
    && mkdir -p /app/data /app/uploads \
    && chown -R gardener:gardener /app/data /app/uploads \
    && chmod +x /app/docker-entrypoint.sh
ENV DATABASE_URL=sqlite:////app/data/gardener.db UPLOAD_DIR=/app/uploads
EXPOSE 8000
USER gardener
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)" || exit 1
CMD ["/app/docker-entrypoint.sh"]
