FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./

ENV MCP_PORT=8000
EXPOSE 8000

RUN useradd -r -u 10002 mcpuser
USER mcpuser

CMD ["python", "server.py"]
