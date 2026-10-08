FROM python:3.13.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8095 DATABASE_PATH=/data/bonsai-sensei/billing.sqlite3 CATALOG_PATH=/config/catalog.json BILLING_ENABLED=false
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 bonsai \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin bonsai \
    && mkdir -p /data/bonsai-sensei /config && chown -R bonsai:bonsai /data
COPY service ./service
COPY catalog.json /config/catalog.json
USER 10001:10001
EXPOSE 8095
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/healthz', timeout=3)"
CMD ["python", "-m", "service"]
