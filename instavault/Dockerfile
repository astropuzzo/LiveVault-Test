FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10002 --no-create-home vault
COPY vault ./vault
COPY static ./static
USER 10002:10002
EXPOSE 8096
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8096/healthz',timeout=3)"
CMD ["python", "-m", "vault.server"]
