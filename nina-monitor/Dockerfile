FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup -S openastro && adduser -S -G openastro -h /app openastro

COPY qsm_client.py server.py ./
COPY static ./static

RUN chown -R openastro:openastro /app

USER openastro

EXPOSE 9091

HEALTHCHECK --interval=30s --timeout=4s --retries=3 --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9091/healthz', timeout=2).read()" || exit 1

CMD ["python", "/app/server.py"]
