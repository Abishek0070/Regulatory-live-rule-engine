FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent agent
FROM base AS api
USER 65532:65532
CMD ["uvicorn","agent.service:app","--host","0.0.0.0","--port","8000","--limit-concurrency","8","--no-access-log"]
FROM base AS parser
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir docling && docling-tools models download --output-dir /models
RUN mkdir /ipc && chown 65532:65532 /ipc
USER 65532:65532
CMD ["sh","-c","rm -f /ipc/parser.sock && exec uvicorn agent.parser_server:app --uds /ipc/parser.sock --limit-concurrency 3"]
