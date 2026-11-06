FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ingest_gtfs.py ./

# Run once and exit; scheduling (daily, after midnight UTC) is handled by the
# host cron / compose profile — see DEPLOYMENT.md.
CMD ["python", "ingest_gtfs.py"]
