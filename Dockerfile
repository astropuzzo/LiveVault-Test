FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl tini tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
RUN mkdir -p /data/recordings
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--proxy-headers"]
