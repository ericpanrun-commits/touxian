FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai \
    APP_ENV=production APP_RUNTIME=cloud APP_DATA_DIR=/data \
    ATTENTION_PERSONAL_DB=/data/production.sqlite3 \
    AUTO_SYNC_ENABLED=false BARK_ENABLED=false \
    PRODUCTION_NOTIFICATION_AUTHORITY=false CLOUD_BOOTSTRAP_MODE=true NOTIFICATION_DRY_RUN=true
WORKDIR /app
RUN groupadd --gid 10001 worker && useradd --uid 10001 --gid worker --no-create-home worker
COPY server/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock.txt && rm /tmp/requirements.lock.txt
COPY server /app/server
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"
CMD ["python", "-m", "server.cloud_entrypoint"]
