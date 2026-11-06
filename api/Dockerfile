FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV API_PORT=8080
EXPOSE 8080

RUN useradd -r -u 10001 appuser
USER appuser

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.getenv('API_PORT', '8080')}/health\", timeout=3)"

CMD ["python", "-m", "app"]
