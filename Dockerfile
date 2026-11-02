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
# Host-заголовок берётся из TRUSTED_HOSTS: в проде там только публичный домен, и
# TrustedHostMiddleware ответил бы 400 на Host: 127.0.0.1 — контейнер навсегда
# остался бы unhealthy, а proxy в compose.prod.yaml ждёт service_healthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; h=(os.environ.get('TRUSTED_HOSTS') or '127.0.0.1').split(',')[0].strip(); urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/health/ready', headers={'Host': h}), timeout=2)" || exit 1
CMD ["/app/docker-entrypoint.sh"]
