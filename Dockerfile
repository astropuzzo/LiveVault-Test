# syntax=docker/dockerfile:1.7
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl tini tzdata
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --disable-pip-version-check -r requirements.txt
COPY app ./app
RUN mkdir -p /data/recordings
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl --fail --silent http://127.0.0.1:8080/healthz >/dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--proxy-headers","--timeout-graceful-shutdown","35"]
